import sys, os, time
import uuid
import binascii
from functools import *
from collections import *
from itertools import *
from contextlib import *

from butter.fhandle import *

def gen_uuid(): return str(uuid.uuid4()).replace('-','')

class AttrDict(dict):
    """A dictionary that allows access to items using the attribute syntax."""
    def __getattr__(self, name):
        try: return self[name]
        except KeyError: raise AttributeEror(name)
    def __setattr__(self, name, val):
        self[name] = val
def docopt_attr(doc, *a, **kw):
    """A wrapper around `docopt` that returns a object with attributes
    instead of a dictionary. Option names are automatically transformed
    to valid python identifiers (e.g. `--output-file` to `output_file`).
    
    Is also replaces the script name with the real program name
    (from sys.argv[0]) in the usage string."""

    doc = doc.replace(os.path.basename(sys.modules['__main__'].__file__),
                        sys.argv[0])

    from docopt import docopt
    dct = docopt(doc, *a, **kw)
    ret = AttrDict()

    for k,v in dct.items():
        attr = k.lower().lstrip('-<').rstrip('>').replace('-', '_')
        setattr(ret, attr, v)

    return ret

def err(msg, retcode=1):
    print("%s: error: %s" % (sys.argv[0], msg), file=sys.stderr)
    sys.exit(retcode)

def monotime():
    # The CLOCK_MONOTONIC_COARSE should be fast because it is implemented
    # in vdso(7), saving us a syscall.
    CLOCK_MONOTONIC_COARSE = 6 # Python is missing this constant
    return time.clock_gettime(CLOCK_MONOTONIC_COARSE)


class SqliteWrapper(object):
    """A convenience wrapper around SQLite (apsw).
    """
    def __init__(self, fn, *, wal=False, timeout=30):
        import apsw
        self.connection = apsw.Connection(fn)
        if wal:
            # Use Write Ahead Log instead of a rollback journal. This significantly
            # reduces the number of fsync()s required by writing all changes to a log
            # and only moving them to the database file once in a while. The log
            # is append-only and does not need syncing. However, the syncing each
            # transaction needs to be explicitly disabled with PRAGMA synchronous.
            #
            # This provides the usual integrity guarantees (a power failure / crash
            # cannot corrupt the database) but it is possible that a few last
            # transactions will be lost (rolled back) in case of a power failure.
            # Thas is fine for our purposes.
            #
            # For details see https://www.sqlite.org/wal.html
            self.execute("PRAGMA journal_mode=WAL")
            self.execute("PRAGMA synchronous=normal")

        # XXX This waits using a (quite tight) busy loop (WTF?). Will probably
        # have to replace it with some sane custom locking.
        self.connection.setbusytimeout(int(timeout*1000))

    def _iter(self, cur):
        col_names = None
        for row in cur:
            # Hack: getdescription doesn't work when the result is empty.
            if col_names is None: col_names = [ desc[0] for desc in cur.getdescription() ]
            yield AttrDict(zip(col_names, row))
        cur.close()

    def query(self, query, *args, **kw):
        cur = self.connection.cursor()
        cur.execute(query, kw or args)
        return self._iter(cur)

    def query_first(self, query, *args, **kw):
        try:
            return next(self.query(query, *args, **kw))
        except StopIteration:
            return None

    def execute(self, query, *args, **kw):
        cur = self.connection.cursor()
        cur.execute(query, kw or args)
        cur.close()

    def insert(self, table, *args, _on_conflict=None, **kw):
        row = {}
        for dct in args: row.update(dct)
        row.update(kw)
        # sorted() is important to generate always the same query and thus
        # utilise prepared query caching
        items = sorted(row.items())
        names = ','.join(sorted( x[0] for x in items ))
        placeholders = '?' + ',?'*(len(items) - 1)
        opts = ''
        if _on_conflict: opts += ' or %s'%_on_conflict
        query = "insert%s into %s (%s) values (%s)" % (opts, table, names, placeholders)
        self.execute(query, *( x[1] for x in items ))

    def update(self, table, where, *args, _on_conflict=None, **kw):
        to_set = {}
        where_binds = []
        for arg in args:
            if isinstance(args, dict): to_set.update(arg)
            else: where_binds.append(arg)
        to_set.update(kw)
        # sorted() is important to generate always the same query and thus
        # utilise prepared query caching
        items = sorted(to_set.items())
        set_clause = ', '.join([ '%s=?'%key for key,val in items ])
        opts = ''
        if _on_conflict: opts += ' or %s'%_on_conflict
        query = "update%s %s set %s where %s" % (opts, table, set_clause, where)
        set_binds =  [ x[1] for x in items ]
        self.execute(query, *(set_binds + where_binds))

    def changes(self):
        return self.connection.changes()

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, tp, val, tb):
        return self.connection.__exit__(tp, val, tb)

def fdscandir(fd):
    """Read the contents of a directory identified by file descriptor `fd`."""
    return os.scandir("/proc/self/fd/%d" % fd)

def frealpath(fd):
    """Return the path of the open file referenced by `fd`.
    
    The output should be similar to realpath(), normalized and with
    symlinks resolved."""

    return os.readlink("/proc/self/fd/%d" % fd)

def is_mountpoint(path):
    """Test whether `path` is the root of a VFS mountpoint.

    Unlike `os.path.ismount`, this function works correctly for bind mounts
    within one filesystem."""

    #if isinstance(path, int): path = frealpath(path)
    #tab = libmount.Table('/proc/self/mountinfo')
    #try: tab.find_target(path)
    #except libmount.Error: return False
    #else: return True
    if isinstance(path, int):
        fd = path
        close = False
    else:
        try: fd = os.open(path, os.O_PATH | os.O_DIRECTORY)
        except NotADirectoryError: return False
        close = True
    try:
        mntid = name_to_handle_at(fd, "", AT_EMPTY_PATH)[1]
        try: parent_mnt = name_to_handle_at(fd, "..")[1]
        except (NotADirectoryError, FileNotFoundError): return False
        if mntid != parent_mnt: return True
        st = os.fstat(fd)
        parent_st = os.stat("..", dir_fd=fd, follow_symlinks=False)
        if os.path.samestat(st, parent_st): return True # we hit the root
        return False
    finally:
        if close: os.close(fd)

def handle_to_str(fh):
    """Return a string representation of a file handle."""
    return "%d:%s" % (fh.type, binascii.hexlify(fh.handle).decode())
def str_to_handle(s):
    """Convert a string representation to a butter-compatible FileHandle object."""
    try:
        tp, data = s.split(':', 1)
        tp = int(tp)
        data = binascii.unhexlify(data)
    except ValueError:
        raise ValueError("invalid handle: '%s'" % s)
    return FileHandle(tp, data)

def issubpath(descendant, ancestor):
    """Check whether one path is an (indirect) descendant of another.

    This is a purely string check, needs normalized and resolved paths,
    like those from realpath()."""
    return descendant == ancestor or descendant.startswith(ancestor + '/')

def openat(path, *args, dir_fd=None, **kw):
    if dir_fd is None or path.startswith('/'): return open(path, *args, **kw)
    else: return open("/proc/self/fd/%d/%s"%(dir_fd, path), *args, **kw)

def slurp(path, *, dir_fd=None):
    """Return the whole content of a file as a string.
    
    Inspired by a namesake function in Perl 6."""
    with openat(path, 'r', dir_fd=dir_fd) as file: return file.read()
def spurt(path, content, *, dir_fd=None):
    """Atomically overwrite `path` with given content."""
    with openat(path+'.tmp', 'w', dir_fd=dir_fd) as file: file.write(content)
    os.rename(path+'.tmp', path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


### DEBUG UTILS ###

@contextmanager
def stdio_to_tty():
    # Must do low-level redirection because redirecting using `sys.stdout = open(...)`
    # breaks readline (arrow keys stop working and the like). Dunno why.
    import os
    sys.stdout.flush()
    sys.stderr.flush()
    orig = os.dup(0), os.dup(1), os.dup(2)
    try:
        inp = os.open('/dev/tty', os.O_RDONLY)
        out = os.open('/dev/tty', os.O_WRONLY)
        os.dup2(inp, 0)
        os.dup2(out, 1)
        os.dup2(out, 2)
        os.close(inp)
        os.close(out)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        for i in range(3):
            os.dup2(orig[i], i)
            os.close(orig[i])

def ipy():
    """Run the IPython console in the context of the current frame.

    Useful for ad-hoc debugging."""
    from IPython.terminal.embed import InteractiveShellEmbed
    from IPython import embed
    frame = sys._getframe(1)
    with stdio_to_tty():
        shell = InteractiveShellEmbed.instance()
        shell(local_ns=frame.f_locals, global_ns=frame.f_globals)

try:
    # Live debugging on exception using IPython/ipdb
    from IPython.core import ultratb
    def excepthook(t,v,tb):
        # Do not catch KeyboardInterrupt and the like
        if not issubclass(t, Exception): return sys.__excepthook__(t,v,tb)
        # stdio may be redirected in some workers, we want ipython to access the tty
        with stdio_to_tty():
            ultratb.FormattedTB(mode='Verbose', color_scheme='Linux', call_pdb=1)(t,v,tb)
    sys.excepthook = excepthook
except ImportError:
    pass

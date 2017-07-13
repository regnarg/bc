import sys, os, time, stat
import uuid
import asyncio
import binascii
from weakref import ref as WeakRef

from functools import *
from collections import *
from itertools import *
from contextlib import *

from butter.fhandle import *
import logging
from pathlib import Path

from clize import run, ArgumentError

FILOCO_LIBDIR = Path(__file__).parent.resolve()

log_prefix = os.environ.get('FILOCO_LOGPREFIX')
logging.basicConfig(level=logging.DEBUG, format=(log_prefix+': ' if log_prefix else '')+'%(message)s')

def init_debug(cats):
    enabled_cats = os.environ.get('FILOCO_DBG', '').split(',')
    glob = sys._getframe(1).f_globals
    for cat in cats:
        glob['D_' + cat.upper()] = cat in enabled_cats
init_debug(['dbw', 'fd', 'pdb'])

def gen_uuid():
    """Generate a random 128-bit ID and return it as a hexadecimal string."""
    return str(uuid.uuid4()).replace('-','')

class AttrDict(dict):
    """A dictionary that allows access to items using the attribute syntax."""
    def __getattr__(self, name):
        try: return self[name]
        except KeyError: raise AttributeError(name)
    def __setattr__(self, name, val):
        self[name] = val

def err(msg, retcode=1):
    print("%s: error: %s" % (sys.argv[0], msg), file=sys.stderr)
    sys.exit(retcode)

def monotime():
    # The CLOCK_MONOTONIC_COARSE should be fast because it is implemented
    # in vdso(7), saving us a syscall.
    CLOCK_MONOTONIC_COARSE = 6 # Python is missing this constant
    return time.clock_gettime(CLOCK_MONOTONIC_COARSE)


import contextlib
@contextlib.contextmanager
def null_contextmanager():
    yield

class SqliteWrapper(object):
    """A convenience wrapper around SQLite (apsw).
    """
    log = logging.getLogger('filoco.debug')

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

    def _iter(self, cur, assoc=True):
        col_names = None
        for row in cur:
            # Hack: getdescription doesn't work when the result is empty.
            if assoc:
                if col_names is None: col_names = [ desc[0] for desc in cur.getdescription() ]
                yield AttrDict(zip(col_names, row))
            else:
                yield row
        cur.close()

    def query(self, query, *args, **kw):
        assoc = kw.pop('_assoc', True)
        cur = self.connection.cursor()
        cur.execute(query, kw or args)
        return self._iter(cur, assoc=assoc)

    def query_first(self, query, *args, **kw):
        try:
            return next(self.query(query, *args, **kw))
        except StopIteration:
            return None

    def execute(self, query, *args, **kw):
        if D_DBW and not query.lower().startswith('select '):
            self.log.debug('%s %r', query, kw or args)
        cur = self.connection.cursor()
        cur.execute(query, kw or args)
        cur.close()

    def executemany(self, query, data):
        cur = self.connection.cursor()
        cur.executemany(query, data)
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

    def ensure_transaction(self):
        """Wrap in a transaction if one is not already active but do not create a nested transaction"""
        if self.connection.getautocommit(): return self
        else: return null_contextmanager()

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
    with openat(str(path), 'r', dir_fd=dir_fd) as file: return file.read()
def spurt(path, content, *, dir_fd=None):
    """Atomically overwrite `path` with given content."""
    path = str(path)
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

def async_wait_readable(fd):
    loop = asyncio.get_event_loop()
    fut = asyncio.Future()
    def cb():
        fut.set_result(None)
        loop.remove_reader(fd)
    loop.add_reader(fd, cb)
    return fut

def mode2type(mode):
    if not isinstance(mode, int): mode = mode.st_mode
    if stat.S_ISDIR(mode):
        return 'd'
    elif stat.S_ISREG(mode):
        return 'r'
    elif stat.S_ISLNK(mode):
        return 'l'
    else:
        return 'S' # special file (socket, fifo, device)

class FD:
    """An object managing the lifetime of a file descriptor.

    We use Python's reference counting to keep track of file descriptors.
    When there are no references to the FD object, it automatically
    closes the underlying FD."""
    __slots__ = ('fd', '_name', '__weakref__') # name is only for debug
    def __init__(self, fd, _name=None):
        self.fd = fd
        self._name = _name
    @classmethod
    def open(cls, *a, **kw):
        if isinstance(kw.get('dir_fd'), FD): kw['dir_fd'] = kw['dir_fd'].fd
        ret =  cls(os.open(*a, **kw), _name=a[0])
        if D_FD: logging.debug('Opened %r' % ret)
        return ret

    def _close(self):
        """Explicitly close the file descriptor (dangerous).
        Do not call unless absolutely sure nobody else has a reference to the FD object."""
        if self.fd is None: return
        if D_FD: logging.debug('Closing %r' % self)
        os.close(self.fd)
        self.fd = None

    def __del__(self):
        if D_FD: logging.debug('Destructing %r' % self)
        self._close()

    def __int__(self):
        return self.fd

    def __pos__(self):
        """Use unary + to convert to int (like in Perl 6)."""
        return self.fd

    def __repr__(self):
        if self._name:
            return 'FD(%d, %r)' % (self.fd, self._name)
        else:
            return 'FD(%d)' % self.fd

import codecs
def binhex(b):
    return codecs.encode(b, 'hex').decode('ascii')

def binxor(a,b):
    return bytes( x^y for x,y in zip(a,b) )

try:
    # Live debugging on exception using IPython/ipdb
    from IPython.core import ultratb
    def excepthook(t,v,tb):
        # Do not catch KeyboardInterrupt and the like
        if not issubclass(t, Exception): return sys.__excepthook__(t,v,tb)
        # stdio may be redirected in some workers, we want ipython to access the tty
        with stdio_to_tty():
            ultratb.FormattedTB(mode='Verbose', color_scheme='Linux', call_pdb=D_PDB)(t,v,tb)
        sys.exit(1)
    sys.excepthook = excepthook
except ImportError:
    pass

# Based on https://gist.github.com/nathan-hoad/8966377
async def aio_read_pipe(file):
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, file)
    return reader

async def aio_write_pipe(file):
    from asyncio.streams import StreamWriter, FlowControlMixin
    loop = asyncio.get_event_loop()
    writer_transport, writer_protocol = await loop.connect_write_pipe(FlowControlMixin, file)
    writer = StreamWriter(writer_transport, writer_protocol, None, loop)
    return writer

import struct
import cbor
class Protocol:
    SIZE_FMT = '>L'
    SIZE_BYTES = struct.calcsize(SIZE_FMT)
    xchg_timeout = 10

    def __init__(self, file):
        if isinstance(file, tuple):
            self.in_file, self.out_file = file
        else:
            self.in_file = self.out_file = file
        self.did_hello = False

    def send_sized(self, data):
        self.out_stream.write(struct.pack(self.SIZE_FMT, len(data)))
        self.out_stream.write(data)

    def send_cbor(self, obj):
        # XXX we prefix each CBOR object with a size because the `cbor' module
        # does not support incremental (push) decoding and we would not know
        # how much data to read on the receiving end. This is redundant but
        # apart from rewriting the cbor module and ugly hacks, not much can be
        # done.
        self.send_sized(cbor.dumps(obj))

    async def recv_sized(self):
        size = struct.unpack(self.SIZE_FMT, await self.in_stream.readexactly(self.SIZE_BYTES))[0]
        return await self.in_stream.readexactly(size)

    async def recv_cbor(self):
        body = await self.recv_sized()
        return cbor.loads(body)

    async def send_multi(self, msgs):
        for msg in msgs: self.send(msg)
        await self.out_stream.drain()

    async def recv_multi(self, types):
        r = []
        for type in types:
            r.append(await self.recv(type))
        return r

    def _dispatch(self, prefix, what):
        if isinstance(what, tuple):
            tp = what[0]
            args = what[1:]
        elif isinstance(what, str):
            tp = what
            args = ()
        else:
            raise TypeError
        return getattr(self, prefix+tp)(*args)

    def send(self, what):
        return self._dispatch('send_', what)
    async def recv(self, what):
        return await self._dispatch('recv_', what)

    def send_hello(self):
        pass
    async def recv_hello(self):
        pass

    async def exchange(self, send_objects, to_recv):
        if not self.did_hello:
            send_objects = ['hello'] + send_objects
            to_recv = ['hello'] + to_recv
        send_task = asyncio.ensure_future(self.send_multi(send_objects))
        recv_task = asyncio.ensure_future(self.recv_multi(to_recv))
        done, pending = await asyncio.wait([send_task, recv_task],
                                timeout=self.xchg_timeout,
                                return_when=asyncio.FIRST_EXCEPTION)

        if pending: # error or timeout
            for fut in pending:
                fut.cancel()
        for fut in done: fut.result() # if there was an exception, raise it
        if pending: raise asyncio.TimeoutError("Timeout while doing protocol exchange")

        ret = recv_task.result()
        if not self.did_hello:
            remote_hello = ret.pop(0)
            self.process_hello(remote_hello)
        return ret

    def process_hello(self, remote_hello):
        pass

    async def prepare(self):
        self.in_stream = await aio_read_pipe(self.in_file)
        self.out_stream = await aio_write_pipe(self.out_file)

"""
A module dealing with the on-disk layout of a store.

This includes reading configuration files or manipulating the metadata
SQLite database.
"""


import sys, os
from utils import *
from butter.fhandle import *

## class PerPartesTransactionManager:
##     """A class that splits a long-running transaction into several smaller ones.
## 
##     Use case: you have a long batch of SQL commands to execute (lasting perhaps
##     several minutes). These consist of small indivisible units of work
##     ("microtransactions") that are independent of each other. This class groups
##     such units into reasonably-sized """
## 
##     def __init__(self, con, begin_cmd='begin'):
##         self.con = con
##         self.cur = con.cursor()
##         self.begin_cmd = begin_cmd
##         self.start_time = 0
## 
##     def start_unit():
##         if not self.con.in_transaction:
##             self.con.execute(self.begin_cmd).close()
##             self.start_time = monotime()
## 
##     def end_unit(force_commit=False):
##         if self.in_transaction and (force_commit
##                 or monotime() - self.start_time > self.MAX_TRANS_DURATION):
##             self.con.execute('commit').close()

META_DIR = '.filoco'

class StoreNotFound(FileNotFoundError):
    def __init__(self, path):
        if isinstance(path, int): path = frealpath(path)
        super().__init__("'%s' is not (in) a Filoco repository." % path)


class Object:
    def __init__(self, store, oid=None):
        if oid is None: oid = gen_uuid()
        self.oid = oid

import hashlib, codecs
class SyncTree:
    #TODO: Split logic and database handling
    POS_BITS = 48
    ID_BITS = 128
    ID_BYTES = ID_BITS//8
    ZERO = b'\0' * ID_BYTES
    LEAF = 1 << (POS_BITS-1)
    POS_SALT = b'filoco-pos-'
    CHK_SALT = b'filoco-chk-'
    BITS_PER_LEVEL = 1
    ARITY = 1 << BITS_PER_LEVEL
    LEVELS = POS_BITS // BITS_PER_LEVEL
    def __init__(self, db):
        self.db = db

    def add(self, id, kind):
        with self.db.ensure_transaction():
            bin_id = codecs.decode(id, 'hex')
            self.db.insert('syncables', _on_conflict='ignore', id=id, kind=kind, tree_key=self.hash_pos(bin_id))
            if self.db.changes():
                self._update_synctree(id)

    def has(self, id):
        return bool(self.db.query_first('select 1 from syncables where id=?', id))

    def subtree_key_range(self, pos):
        left = right = pos
        while not left & self.LEAF:
            left <<= 1
            right = (right << 1) + 1
        return left, right

    @classmethod
    def hash_pos(cls, id):
        return int(hashlib.md5(cls.POS_SALT + id).hexdigest()[:cls.POS_BITS//8], 16) | cls.LEAF
    @classmethod
    def hash_chk(cls, id):
        return hashlib.md5(cls.CHK_SALT + id).digest()

    def _update_synctree(self, id):
        """Update synctree after adding or removing a syncable with given id.
        (as we are xorring, the update is the same for adding and removing)

        Call from within a transaction!"""
        
        bin_id = codecs.decode(id, 'hex')
        pos = self.hash_pos(bin_id)
        chk = self.hash_chk(bin_id)
        while pos:
            self.db.execute('insert or ignore into synctree values (?,?,?)', pos, bin_id, chk)
            if not self.db.changes():
                self.db.execute('update synctree set xor=binxor(xor,?), chxor=binxor(chxor,?) where pos=?', bin_id, chk, pos)
                self.db.execute('delete from synctree where pos=? and xor=zeroblob(%d)'%(self.ID_BITS//8), pos)
            pos >>= self.BITS_PER_LEVEL


    # def create_trigger(self):
    #     for (event, rec) in (('insert', 'new'), ('delete', 'old')):
    #         zero = 'zeroblob(%d)' % (self.ID_BITS // 8)
    #         l = ['create temp trigger update_synctree_{event} after {event} on syncables begin'.format(event=event)]
    #         for shift in range(self.LEVELS):
    #             pos = '(%s.tree_key >> %d)' % (rec, shift)
    #             l.append('insert or ignore into synctree values ({pos},{zero},{zero});'.format(rec=rec, pos=pos, zero=zero))
    #             l.append("update synctree set xor=binxor(xor, {rec}.id), chxor=binxor(chxor,{rec}.chk) where pos={pos};"
    #                         .format(shift=shift, rec=rec, pos=pos))
    #             l.append("delete from synctree where pos=pos and xor={zero};"
    #                         .format(shift=shift, pos=pos, zero=zero))
    #         l.append('end;')
    #         #print('\n'.join(l))
    #         self.db.execute('\n'.join(l))


def lazy(init_func):
    from functools import wraps
    attr = '_' + init_func.__name__
    @propery
    @wraps(init_func)
    def prop(self):
        if hasattr(self, attr):
            return getattr(self, attr)
        else:
            val = init_func(self)
            setattr(self, attr, val)
            return val
    

class Store:
    root_fd = None
    meta_fd = None
    SQLITE_CACHE_MB = 256
    TYPE2TABLE = {'fob': 'fobs', 'fov': 'fovs'}

    def __init__(self, root):
        if isinstance(root, int):
            root = FD(root)
        if isinstance(root, FD): # fall thru
            self.root_fd = root
            self.root_path = frealpath(self.root_fd)
        else:
            self.root_path = os.path.realpath(root)
            self.root_fd = FD.open(self.root_path, os.O_DIRECTORY)
        self.root_mnt = name_to_handle_at(self.root_fd, "", AT_EMPTY_PATH)[1]
        self.meta_path = os.path.join(self.root_path, META_DIR)
        self.meta_fd = FD.open(META_DIR, os.O_DIRECTORY, dir_fd=self.root_fd)
        self.open_db()
        self.sync_mode = slurp(os.path.join(self.meta_path, 'sync_mode'))
        self.synctree = SyncTree(self.db)
        #self.synctree.create_trigger()

    #@lazy
    #def db(self):
    #    return self.open_db()

    @classmethod
    def find(cls, dir='.'):
        """Find the root of a Filoco store containing `dir`.
        
        Walk up `dir` and its parents until a directory with a `.filoco`
        subdirectory is found. This is very similar to what `git` does."""
        if isinstance(dir, int): dfd = os.dup(dir)
        else: dfd = os.open(dir, os.O_DIRECTORY)
        try:
            while True:
                try: st = os.stat(META_DIR, dir_fd=dfd, follow_symlinks=False)
                except FileNotFoundError: pass
                else:
                    store = Store(dfd)
                    dfd = None # prevent closing
                    return store

                # We do not support stores that cross mount boundaries. This also
                # takes care of stopping when we hit the root.
                if is_mountpoint(dfd): break

                parent = os.open("..", os.O_DIRECTORY|os.O_PATH, dir_fd=dfd)
                os.close(dfd)
                dfd = parent
            raise StoreNotFound(dir)
        finally:
            if dfd is not None: os.close(dfd)

    def add_syncable(self, id, kind, **data):
        if self.sync_mode == 'synctree':
            self.synctree.add(id, kind)
        else:
            self.db.execute('insert into syncables_local (id, kind) values (?, ?)', id, kind)
        self.db.insert(self.TYPE2TABLE[kind], id=id, **data)

    def open_db(self):
        self.db = SqliteWrapper('/proc/self/fd/%d/meta.sqlite' % self.meta_fd, wal=True)
        # TODO set those only for large scans and not live updates?
        self.db.execute('PRAGMA wal_autocheckpoint=20000')
        # https://www.sqlite.org/pragma.html#pragma_cache_size
        self.db.execute('PRAGMA cache_size=%d' % (- self.SQLITE_CACHE_MB*1024))
        self.db.connection.enableloadextension(True)
        self.db.connection.loadextension(str(FILOCO_LIBDIR / 'binxor.so'))

    def open_handle(self, handle, flags):
        return FD(open_by_handle_at(self.root_fd, str_to_handle(handle), flags))

    def handle_exists(self, handle):
        try:
            fd = self.open_handle(handle, os.O_PATH)
        except StaleHandle:
            return False
        else:
            fd._close()
            return True

    def create_fob(self, *, type, name=None, parent=None):
        id = gen_uuid()
        self.add_syncable(id, 'fob')


def stat_tuple(st):
    return {'mtime': st.st_mtime, 'ctime': st.st_ctime, 'size': st.st_size, 'ino': st.st_ino}

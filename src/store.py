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

import hashlib
class SyncTree:
    #TODO: Split logic and database handling
    LEAF = 1 << 128
    POS_SALT = 'filoco-pos-'
    CHK_SALT = 'filoco-chk-'
    def __init__(self, db):
        self.db = db

    def add(self, id, kind):
        with self.db:
            self.db.insert('syncables', _on_conflict='ignore', id=id, kind=kind)
            if self.db.changes():
                self._update_synctree(id)

    def hash_pos(self, id):
        return int(hashlib.md5((self.POS_SALT + id).encode('ascii')).hexdigest(), 16) | self.LEAF
    def hash_chk(self, id):
        return hashlib.md5((self.CHK_SALT + id).encode('ascii')).hexdigest()

    def _update_synctree(self, id):
        """Update synctree after adding or removing a syncable with given id.
        (as we are xorring, the update is the same for adding and removing)

        Call from within a transaction!"""
        
        cur = self.hash_pos(id)
        num_id = int(id, 16)
        num_chk = int(self.hash_chk(id), 16)
        while cur:
            hexpos = '%x' % cur
            row = self.db.query_first('select xor, chk from synctree where pos=?', hexpos, _assoc=False)
            if row:
                xor, chk = (int(x, 16) for x in row)
                assert xor != 0 and chk != 0
            else:
                xor, chk = 0, 0
            xor ^= num_id
            chk ^= num_chk
            delete = (xor == 0)
            assert delete == (chk == 0)
            if delete:
                self.db.execute('delete from synctree where pos=?', hexpos)
            else:
                #self.db.update('synctree', 'pos=?', hexpos, xor='%x'%xor, chk='%x'%chk)
                self.db.insert('synctree', _on_conflict='replace',pos=hexpos, xor='%x'%xor, chk='%x'%chk)
            cur = cur >> 1


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
        self.synctree = SyncTree(self.db)

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

    def add_syncable(self, id, kind):
        self.db.insert('syncables', id=id, kind=kind)
        self.synctree.add(id)

    def open_db(self):
        self.db = SqliteWrapper('/proc/self/fd/%d/meta.sqlite' % self.meta_fd, wal=True)

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

    def create_object(self, *, type, name=None, parent=None):
        oid = gen_uuid()


def stat_tuple(st):
    # TODO: order?
    return (st.st_mtime, st.st_ctime, st.st_size, st.st_ino)

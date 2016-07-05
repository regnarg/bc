"""
A module dealing with the on-disk layout of a store.

This includes reading configuration files or manipulating the metadata
SQLite database.
"""


import sys, os
import apsw # an alternative sqlite wrapper
from utils import *

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

class Store:
    root_fd = None
    meta_fd = None
    def __init__(self, root):
        if isinstance(root, int):
            self.root_fd = root
            self.root_path = frealpath(self.root_fd)
        else:
            self.root_path = os.path.realpath(root)
            self.root_fd = os.open(self.root_path, os.O_DIRECTORY)
        self.meta_path = os.path.join(self.root_path, META_DIR)
        self.meta_fd = os.open(META_DIR, os.O_DIRECTORY, dir_fd=self.root_fd)

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


    def open_db(self):
        return SqliteWrapper('/proc/self/fd/%d/meta.sqlite' % self.meta_fd, wal=True)

    def __del__(self):
        if self.meta_fd: os.close(self.meta_fd)
        if self.root_fd: os.close(self.root_fd)

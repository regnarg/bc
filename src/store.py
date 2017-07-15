"""
A module dealing with the on-disk layout of a store.

This includes reading configuration files or manipulating the metadata
SQLite database.
"""


import sys, os
from utils import *
from butter.fhandle import *
from pathlib import Path
import json, hashlib

import logging
log = logging.getLogger('filoco.store')

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

# Scan state constants
SCAN_NEVER_SCANNED = 0
SCAN_NEEDS_RESCAN = 1
SCAN_WANT_RESCAN = 2
SCAN_UP_TO_DATE = 100

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

    def add(self, id, kind, **kw):
        with self.db.ensure_transaction():
            bin_id = codecs.decode(id, 'hex')
            self.db.insert('syncables', _on_conflict='ignore', id=id, kind=kind, tree_key=self.hash_pos(bin_id), **kw)
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

class InodeInfo:
    __slots__ = ('store', '_fd', 'handle', 'stat', 'ino', 'iid', 'ino', 'type')
    def __init__(self, store, **kw):
        for attr in InodeInfo.__slots__: setattr(self, attr, None)
        self.store = store # TODO: weakref?
        for k,v in kw.items(): setattr(self, k, v)
        if isinstance(self.fd, int): self.fd = FD(self.fd)
    @classmethod
    def from_db(cls, store, row):
        """Create an InodeInfo from a row in the `inodes` table."""
        return cls(store, handle=FileHandle(row.handle_type, row.handle), ino=row.ino, type=row.type, iid=row.iid)

    def release_fd(self):
        """Convert self.fd into a weakref so that we do not keep the FD open too long."""
        if isinstance(self._fd, WeakRef): return
        if self.fd is None: return
        self.get_handle() # because fd might be released unexpectedly, we must keep handle
        self._fd = WeakRef(self._fd)

    def close(self):
        if self.fd is None: return
        self.fd = None

    @property
    def fd(self):
        if isinstance(self._fd, WeakRef):
            fd = self._fd()
            if fd is None: self._fd = None
            return fd
        else:
            return self._fd

    @fd.setter
    def fd(self, fd):
        self._fd = fd

    def get_handle(self): # TODO: ,check=False
        if self.handle:
            return self.handle
        elif self.fd:
            self.handle = name_to_handle_at(self.fd.fd, "", AT_EMPTY_PATH)[0]
            return self.handle
        else:
            raise NotImplementedError # TODO: lookup from db?

    def get_fd(self):
        fd = self.fd
        if fd is None:
            if not self.handle: raise ValueError()
            fd = self._fd = self.store.open_handle(self.handle, os.O_PATH)
        return fd

    def get_stat(self, force=False):
        if force or not self.stat:
            self.stat = st = os.fstat(self.get_fd())
            self.ino = st.st_ino
            self.type = mode2type(st)
        return self.stat

    def clear_stat(self):
        self.stat = None

    def get_ino(self):
        if not self.ino: self.get_stat()
        return self.ino
    def get_type(self):
        if not self.type: self.get_stat()
        return self.type

    def __repr__(self):
        attrs = ['fd', 'handle', 'ino', 'iid']
        args = ', '.join( '%s=%r'%(attr, getattr(self,attr)) for attr in attrs if getattr(self, attr, None) )
        return 'InodeInfo(%s)' % args

class Store:
    root_fd = None
    meta_fd = None
    SQLITE_CACHE_MB = 256
    TYPE2TABLE = {'fob': 'fobs', 'fcv': 'fcvs', 'flv': 'flvs'}
    # Files whose dara is not currently stored in this store are represented by symlinks
    # with a fake nonexistent target. We call these links *placeholders*.
    PLACEHOLDER_TARGET = '/!/filoco-missing'
    # Whenever we cannot use the logical name of an object as a file name as it appears
    # in the FLV (e.g. when there is a conflict between two versions of a file or a pigeonhole
    # conflict between two files wanting to use the same name), we use a so-called
    # longname in the form <logical name>.FL-<suffix>. These special names are detected by
    # the scanner and the suffix is stripped when creating FLVs so longnames are always
    # restricted to the local filesystem and never make it to the synced metadata.
    LONGNAME_SEPARATOR = '.FL-'

    def __init__(self, root):
        if isinstance(root, int):
            root = FD(root)
        if isinstance(root, FD): # fall thru
            self.root_fd = root
            self.root_path = Path(frealpath(self.root_fd))
        else:
            self.root_path = Path(os.path.realpath(root))
            self.root_fd = FD.open(self.root_path, os.O_DIRECTORY)
        self.root_mnt = name_to_handle_at(self.root_fd, "", AT_EMPTY_PATH)[1]
        self.meta_path = self.root_path / META_DIR
        self.meta_fd = FD.open(META_DIR, os.O_DIRECTORY, dir_fd=self.root_fd)
        self.store_id = slurp(self.meta_path / 'store_id')
        self.sync_mode = slurp(self.meta_path / 'sync_mode')
        root_stat = os.fstat(self.root_fd)
        self.owner = (root_stat.st_uid, root_stat.st_gid)
        self.open_db()
        if self.sync_mode == 'synctree':
            self.synctree = SyncTree(self.db)
            #self.synctree.create_trigger()
        self.store_id_cache = {}
        self.store_idx_cache = {}

    #@lazy
    #def db(self):
    #    return self.open_db()

    def create_inode(self, info, *, iid=None, **kw):
        if iid is None: iid = gen_uuid()
        fd = info.get_fd() # keep inode alive
        st = info.get_stat()
        handle = info.get_handle()
        ftype = info.get_type()
        # We can insert safely without any locking. Because we hold an open FD to
        # the inode, it cannot just disappear and thus we are writing correct data.
        self.db.insert('inodes', ino=st.st_ino, handle_type=handle[0], handle=handle[1], iid=iid, type=ftype,
                        size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime,
                        btime=st.st_mtime, # btime currently not available b/c of missing statx userspace wrapper
                        scan_state=(SCAN_NEVER_SCANNED if ftype=='d' else SCAN_UP_TO_DATE), **kw)
        #self.db.insert('fslog', event=EVENT_CREATE, iid=iid)
        ret = self.db.query_first('select * from inodes where ino=?', st.st_ino)
        info.iid = ret['iid']
        return ret

    def find_inode(self, info):
        handle = info.get_handle()
        ino = info.get_ino()
        with self.db.ensure_transaction():
            obj = self.db.query_first('select * from inodes where ino=?', ino)
            if obj is not None:
                obj_handle = FileHandle(obj.handle_type, obj.handle)
                if obj_handle == handle or self.store.handle_exists(obj_handle):
                    info.iid = obj['iid']
                    return obj
                else:
                    self.do_delete_inode(obj.iid)
            else:
                return None

    def find_or_create_inode(self, info, **kw):
        with self.db.ensure_transaction():
            self.db.lock_now()
            fd = info.get_fd() # hold fd to prevent races
            inode = self.find_inode(info)
            created = False
            if inode is None:
                inode = self.create_inode(info, **kw)
                created = True
            return inode, created

    def get_root(self):
        db_root = self.db.query_first("select * from inodes where iid='ROOT'")
        if db_root is None: raise RuntimeError("Missing root inode")
        root_info = InodeInfo(self, fd=self.root_fd)
        db_root2 = self.find_inode(root_info)
        if db_root2 is None or db_root2.iid != 'ROOT':
            raise RuntimeError('Root inode was replaced. This is not supported.')
        return db_root, root_info


    @classmethod
    def find(cls, dir='.'):
        """Find the root of a Filoco store containing `dir`.

        Walk up `dir` and its parents until a directory with a `.filoco`
        subdirectory is found. This is very similar to what `git` does."""

        pth = Path(os.path.abspath(dir))
        store_pth = pth
        sub_pth = []

        while store_pth != '/' and not (store_pth / META_DIR).exists() and (store_pth.is_symlink() or not is_mountpoint(store_pth)):
            sub_pth.append(store_pth.name)
            store_pth = store_pth.parent

        sub_pth = Path('/'.join(reversed(sub_pth)))

        if not (store_pth / META_DIR).exists():
            raise StoreNotFound(dir)

        return cls(store_pth), sub_pth

        #if isinstance(dir, int): dfd = os.dup(dir)
        #else: dfd = os.open(dir, os.O_DIRECTORY)
        #try:
        #    while True:
        #        try: st = os.stat(META_DIR, dir_fd=dfd, follow_symlinks=False)
        #        except FileNotFoundError: pass
        #        else:
        #            store = Store(dfd)
        #            dfd = None # prevent closing
        #            return store

        #        # We do not support stores that cross mount boundaries. This also
        #        # takes care of stopping when we hit the root.
        #        if is_mountpoint(dfd): break

        #        parent = os.open("..", os.O_DIRECTORY|os.O_PATH, dir_fd=dfd)
        #        os.close(dfd)
        #        dfd = parent
        #    raise StoreNotFound(dir)
        #finally:
        #    if dfd is not None: os.close(dfd)

    def compute_object_id(self, kind, origin=None, **data):
        data = dict(data)
        data['kind'] = kind
        data['origin'] = (origin or self.store_id).lower()
        # Skip None/NULL fields to allow further extension without changing IDs.
        data = {k:v for k,v in data.items() if v is not None}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()[:32]

    def add_syncable(self, id, kind, origin=None, serial=None, created=None, **data):
        if id is None:
            #if kind == 'fob': id = gen_uuid()
            #else: id = self.compute_object_id(kind, origin, **data)
            id = gen_uuid()
        if origin is None: origin_idx = 0
        else: origin_idx = self.get_store_idx(origin)
        if created is None: created = time.time()

        if self.sync_mode == 'synctree':
            self.synctree.add(id, kind, origin_idx=origin_idx, created=created)
        else:
            if serial is None:
                assert origin is None
                self.db.execute('insert into syncables_local (id, kind, created) values (?, ?, ?)', id, kind, created)
            else:
                self.db.execute('insert into syncables (id, kind, origin_idx, serial, created) values (?, ?, ?, ?, ?)',
                                    id, kind, origin_idx, serial, created)
        self.db.insert(self.TYPE2TABLE[kind], id=id, **data)
        return id

    def open_db(self):
        self.db = SqliteWrapper('/proc/self/fd/%d/meta.sqlite' % self.meta_fd, wal=True)
        # TODO set those only for large scans and not live updates?
        self.db.execute('PRAGMA wal_autocheckpoint=20000')
        # https://www.sqlite.org/pragma.html#pragma_cache_size
        self.db.execute('PRAGMA cache_size=%d' % (- self.SQLITE_CACHE_MB*1024))
        if self.sync_mode == 'synctree':
            self.db.connection.enableloadextension(True)
            self.db.connection.loadextension(str(FILOCO_LIBDIR / 'binxor.so'))

    def open_handle(self, handle, flags):
        return FD(open_by_handle_at(self.root_fd, handle, flags))

    def handle_exists(self, handle):
        try:
            fd = self.open_handle(handle, os.O_PATH)
        except StaleHandle:
            return False
        else:
            fd._close()
            return True

    def create_fob(self, *, type, name=None, parent=None):
        with self.db.ensure_transaction():
            fob_id = self.add_syncable(None, 'fob', type=type)
            flv_id = self.add_syncable(None, 'flv', parent_fob=parent, name=name, parent_vers='', fob=fob_id)
            if type == 'r':
                fcv_id = self.create_working_version(fob_id, [])
            else:
                fcv_id = None
            return fob_id, flv_id, fcv_id

    def create_working_version(self, fob, parent_vers):
        if parent_vers is None: parent_vers = []
        elif isinstance(parent_vers, str): parent_vers = [parent_vers]
        if len(parent_vers) == 1:
            # If the (single) parent is already a working verision, there is no need to create another.
            parent = self.db.query_first('select s.origin_idx as origin_idx, v.content_hash as content_hash from fcvs v join syncables s on s.id=v.id where v.id=?', parent_vers[0])
            if parent.origin_idx == 0 and parent.content_hash is None:
                return parent_vers[0]
        id = self.add_syncable(None, 'fcv', content_hash=None, parent_vers=','.join(parent_vers), fob=fob, _is_head=1)
        return id

    def create_flv(self, fob, parent_fob, name, parent_vers):
        if parent_vers is None: parent_vers = []
        elif isinstance(parent_vers, str): parent_vers = [parent_vers]
        if len(parent_vers) == 1:
            parent = self.db.query_first('select * from flvs where id=?', parent_vers[0])
            if parent.parent_fob == parent_fob and parent.name == name:
                return parent.id
        id = self.add_syncable(None, 'flv', parent_vers=','.join(parent_vers),
                                fob=fob, parent_fob=parent_fob, name=name, _is_head=1)
        return id

    def delete_inode(self, info):
        log.debug('Deleting inode %r from database', info)
        self.db.execute('delete from inodes where iid=?', info.iid)


    def get_store_idx(self, id):
        try: return self.store_idx_cache[id]
        except KeyError: pass
        row = self.db.query_first('select idx from stores where id=?', id)
        if row is None:
            self.db.execute('insert or ignore into stores (id) values (?)', id)
            row = self.db.query_first('select idx from stores where id=?', id)
        self.store_idx_cache[id] = idx = row['idx']
        return idx
    def get_store_id(self, idx):
        try: return self.store_id_cache[idx]
        except KeyError: pass
        row = self.db.query_first('select id from stores where idx=?', idx)
        if row is None:
            raise KeyError(idx)
        self.store_id_cache[idx] = id = row['id']
        return id


def stat_tuple(st):
    return {'mtime': st.st_mtime, 'ctime': st.st_ctime, 'size': st.st_size, 'ino': st.st_ino}

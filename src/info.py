#!/usr/bin/python3

from utils import *
from store import *
from clize import run


class InfoPrinter:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.indent = 0

    def filename_info(self, filename):
        fd = FD.open(self.store.root_path / filename, os.O_PATH|os.O_NOFOLLOW)
        info = InodeInfo(self.store, fd=fd)
        inode = self.store.find_inode(info)
        st = info.get_stat()
        self.print("Store root: %s" % self.store.root_path)
        self.print("Inode number: %d" % st.st_ino)
        handle = info.get_handle()
        self.print("File handle: %d:%s" % (handle.type, binhex(handle.handle)))
        if inode:
            self.print("DB handle:   %d:%s" % (inode.handle_type, binhex(inode.handle)))
        self.print("Stat tuple: ", (st.st_size, st.st_mtime))
        if inode:
            self.print("DB   tuple: ", (inode.size, inode.mtime))
        if inode is None:
            self.print("No inode record")
            return
        self.print("Type:", inode.type)
        self.print("IID: ", binhex(inode.iid))
        self.print("FOB: ", binhex(inode.fob))
        if inode.fob:
            with self.indented(): self.print_syncable(inode.fob, skip_type=True)
        self.print("FLV: ", binhex(inode.flv))
        if inode.flv:
            with self.indented(): self.print_syncable(inode.flv, skip_type=True)
        self.print("FCV: ", binhex(inode.fcv))
        if inode.fcv:
            with self.indented(): self.print_syncable(inode.fcv, skip_type=True)

    def print_syncable(self, id, *, skip_id=True, skip_type=False):
        row = self.db.query_first('select * from syncables where id=?', id)
        if not skip_id:
            self.print("ID:", binhex(id))
        if not skip_type:
            self.print("Kind:", row.kind)
        origin = self.store.get_store_id(row.origin_idx)
        self.print("Origin: %s (idx %d)" % (origin, row.origin_idx))
        if 'serial' in row:
            self.print("Serial:", row.serial)
        self.print("Insert order:", row.insert_order)
        self.print("Created: ", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row.created)))

        data = self.db.query_first("select * from %s where id=?"%Store.TYPE2TABLE[row.kind], id)

        if row.kind == 'fob':
            self.print("Type:", data.type)

    def print(self, *a, **kw):
        print(" "*self.indent, end="")
        print(*a, **kw)

    @contextmanager
    def indented(self):
        self.indent += 4
        try: yield
        finally: self.indent -= 4

import re
ID_RE = re.compile(r'^[0-9a-fA-F]{16,}$')

def main(filename, id=None):
    if id is not None:
        store = Store(filename)
        printer = InfoPrinter(store)
        printer.print_syncable(id)
    else:
        store, path = Store.find(filename_or_syncable)
        printer = InfoPrinter(store)
        printer.filename_info(path)

run(main)

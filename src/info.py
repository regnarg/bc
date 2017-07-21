#!/usr/bin/python3

from utils import *
from store import *
from clize import run
import codecs

def ohex(v):
    if v is None: return '-'
    else: return binhex(v)


class InfoPrinter:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.indent = 0

    def find_inode(self, filename):
        fd = FD.open(self.store.root_path / filename, os.O_PATH|os.O_NOFOLLOW)
        info = InodeInfo(self.store, fd=fd)
        inode = self.store.find_inode(info)
        return inode, info

    def filename_info(self, filename):
        inode, info = self.find_inode(filename)
        st = info.get_stat()
        self.print("Store root: %s" % self.store.root_path)
        self.print("Inode number: %d" % st.st_ino)
        handle = info.get_handle()
        self.print("File handle: %d:%s" % (handle.type, ohex(handle.handle)))
        if inode:
            self.print("DB handle:   %d:%s" % (inode.handle_type, ohex(inode.handle)))
        self.print("Stat tuple: ", (st.st_size, st.st_mtime))
        if inode:
            self.print("DB   tuple: ", (inode.size, inode.mtime))
        if inode is None:
            self.print("No inode record")
            return
        self.print("Type:", inode.type)
        self.print("IID: ", ohex(inode.iid))
        self.print("FOB: ", ohex(inode.fob))
        if inode.fob:
            with self.indented(): self.print_syncable(inode.fob, skip_type=True)
        self.print("FLV: ", ohex(inode.flv))
        if inode.flv:
            with self.indented(): self.print_syncable(inode.flv, skip_type=True)
        self.print("FCV: ", ohex(inode.fcv))
        if inode.fcv:
            with self.indented(): self.print_syncable(inode.fcv, skip_type=True)

    def print_syncable(self, id, *, skip_id=True, skip_type=False):
        row = self.db.query_first('select * from syncables where id=?', id)
        if not skip_id:
            self.print("ID:", ohex(id))
        if not skip_type:
            self.print("Kind:", row.kind)
        origin = self.store.get_store_id(row.origin_idx)
        self.print("Origin: %s (idx %d)" % (origin, row.origin_idx))
        if 'serial' in row:
            self.print("Serial:", row.serial)
        self.print("Insert order:", row.insert_order)
        self.print("Created: ", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row.created)))

        row.update(self.db.query_first("select * from %s where id=?"%Store.TYPE2TABLE[row.kind], id))

        if row.kind == 'fob':
            self.print("Type:", row.type)

        if row.kind in ('flv', 'fcv'):
            self.print("Parent vers:", ', '.join( binhex(v) for v in  split_idlist(row.parent_vers)))

    def print(self, *a, **kw):
        print(" "*self.indent, end="")
        print(*a, **kw)

    @contextmanager
    def indented(self):
        self.indent += 4
        try: yield
        finally: self.indent -= 4

    def print_graph(self, fob_id, kind, cur=None):
        print("digraph G {")
        if cur: print('"%s" [color=red];'%binhex(cur))
        for row in self.db.query('select * from %ss where fob=?'%kind, fob_id):
            id = binhex(row.id)
            lbl = "%s [%s/%s]" % (id, binhex(row.parent_fob), row.name)
            if row._is_head: lbl += " [head]"
            print('"%s" [label="%s"];' % (id,lbl))
            for par in split_idlist(row.parent_vers):
                print('"%s" -> "%s";' % (id, binhex(par)))
        print("}")

import re
ID_RE = re.compile(r'^[0-9a-fA-F]{16,}$')

def main(filename, id=None, *, flv_graph=False, fcv_graph=False):
    if id is not None:
        store = Store(filename)
        id = codecs.decode(id, 'hex')
        printer = InfoPrinter(store)
        if flv_graph:
            printer.print_graph(id, 'flv')
        elif fcv_graph:
            printer.print_graph(id, 'fcv')
        else:
            printer.print_syncable(id)
    else:
        store, path = Store.find(filename)
        printer = InfoPrinter(store)
        inode, info = printer.find_inode(path)
        if flv_graph:
            printer.print_graph(inode.fob, 'flv', inode.flv)
        elif fcv_graph:
            printer.print_graph(inode.fob, 'fcv', inode.fcv)
        else:
            printer.filename_info(path)

run(main)

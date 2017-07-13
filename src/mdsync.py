#!/usr/bin/python3

import logging
log = logging.getLogger('filoco.mdsync')

from utils import *
from store import *
import struct
import cbor, json

init_debug(['synctree', 'sendobj'])

PROTOCOL = 1


class MDSync(Protocol):
    def __new__(cls, store, *a, **kw):
        if cls is MDSync:
            # Automatically create instance of the right subclass for store's sync mode
            if store.sync_mode == 'synctree':
                return super().__new__(TreeMDSync)
            else:
                return super().__new__(SerialMDSync)
        else:
            return super().__new__(cls)

    def __init__(self, store, file):
        super().__init__(file=file)
        self.store = store
        self.db = store.db
        self.store_id2idx = {}
        self.store_idx2id = {}
        for store in list(self.db.query('select * from stores')):
            self.store_id2idx[store.id] = store.idx
            self.store_idx2id[store.idx] = store.id

    async def send_by_syncable_row(self, row):
        kind = row['kind']
        tbl = Store.TYPE2TABLE[kind]
        obj = self.db.query_first('select * from %s where id=?'%tbl, row['id'])
        to_send = {'kind': kind, 'origin': self.store_idx2id[row['origin_idx']], 'data': obj, 'id': row['id']}
        del obj['id']
        if self.store.sync_mode == 'serial':
            to_send['serial'] = row['serial']
        if D_SENDOBJ:
            log.debug('sending object %s', json.dumps(to_send))
        self.send_cbor(to_send)
        await self.out_stream.drain()

    async def recv_objects(self):
        while True:
            for i in range(1000):
                # XXX this transacton affects the concurrent send task! is it a problem?
                with self.db:
                    data = await self.recv_sized()
                    if not data: break
                    obj = cbor.loads(data)
                    kw = dict(obj['data'])
                    if self.store.sync_mode == 'serial':
                        kw['serial'] = obj['serial']
                    self.store.add_syncable(obj['id'], obj['kind'], origin=obj['origin'], **kw)
            if not data: break

    async def exchange_objects(self, to_send):
        send_task = asyncio.ensure_future(self.send_objects(to_send))
        recv_task = asyncio.ensure_future(self.recv_objects())
        done, pending = await asyncio.wait([send_task, recv_task],
                                return_when=asyncio.FIRST_EXCEPTION)
        return [ x.result() for x in done ]

    async def run(self):
        await self.prepare()
        to_send = await self.compute_diff()
        await self.exchange_objects(to_send)

class TreeMDSync(MDSync):
    # TODO: make parameters configurable per-world (all stores in a world must
    # have same configuration)
    # Start at some reasonable level so as not to send only a few bytes in the
    # first exchange. For level 4, we have 16 nodes with 4+16+16 (pos+xor+chxor)
    # = 36 bytes per node, which gives 16*36 = 576 bytes for the first exchnage.
    # This seems reasonable cost even when there are no changes at all and it
    # saves 4 roundtrips in the common case of several changes.
    START_LVL = 4
    NODE_FMT = '>Q16s16s' # a 64b position and two 128b xors
    NODE_BYTES = struct.calcsize(NODE_FMT)
    def __init__(self, store, file):
        super().__init__(store=store, file=file)
        self.synctree = store.synctree

    def get_xors(self, positions):
        # TODO: is this better than several queries that can be precompiled?
        ret = self.db.query('select pos, xor, chxor from synctree where pos in (%s)'
                                   % ','.join(repeat('?', len(positions))), *positions)
        return { row['pos'] : (row['xor'], row['chxor']) for row in ret }

    def send_level(self, level):
        """Transfer all the active vertices from one level of the synctree.
        'level' should be a dictionary in the form {pos: (id_xor, chk_xor)}"""
        if D_SYNCTREE: log.debug('Sending: %r', level)
        self.out_stream.write(struct.pack(self.SIZE_FMT, len(level)*self.NODE_BYTES))
        for pos, (id_xor, chk_xor) in level.items():
            self.out_stream.write(struct.pack(self.NODE_FMT, pos, id_xor, chk_xor))

    async def recv_level(self):
        if self.recv_tree_eof: return {}
        ret = {}
        data = await self.recv_sized()
        if data == b'':
            if D_SYNCTREE: log.debug('Received EOF')
            self.recv_tree_eof = True
            return {}
        for pos in range(0, len(data), self.NODE_BYTES):
            chunk = data[pos:pos + self.NODE_BYTES]
            pos, id_xor, chk_xor = struct.unpack(self.NODE_FMT, chunk)
            ret[pos] = (id_xor, chk_xor)
        return ret


    async def compute_diff(self): 
        self.recv_tree_eof = False
        lvl_num = self.START_LVL
        start_off = start_size = 1 << self.START_LVL
        lvl_alive = list(range(start_off, start_off + start_size))
        send_objects = []
        send_subtrees = []
        while  lvl_num < SyncTree.LEVELS:
            if D_SYNCTREE: log.debug("Level %d, alive %r", lvl_num, lvl_alive)
            sent = self.get_xors(lvl_alive)
            if not self.recv_tree_eof:
                (recv,) = await self.exchange([('level', sent)], ['level'])
            if not sent:
                if D_SYNCTREE: log.debug("Sent EOF, exiting")
                break
            next_lvl = []
            for vert in sent:
                my_val, my_chk = sent[vert]
                their_val, their_chk = recv.get(vert, (SyncTree.ZERO,SyncTree.ZERO))
                if D_SYNCTREE: log.debug("Vert %d: my %s/%s, their %s/%s", vert,
                                    binhex(my_val), binhex(my_chk), binhex(their_val), binhex(their_chk))

                if their_val == SyncTree.ZERO and their_chk == SyncTree.ZERO: # they have nothing, send whole subtree, no need to recurse
                    send_subtrees.append(vert)
                    continue

                if my_val == their_val and my_chk == their_chk:
                    continue # no changes

                diff = binxor(my_val, their_val)
                if SyncTree.hash_chk(diff) == binxor(my_chk, their_chk): # only single chnage in subtree
                    if D_SYNCTREE: log.debug('Single change: %s', binhex(diff))
                    hex_id = binhex(diff)
                    if self.synctree.has(hex_id):
                        send_objects.append(hex_id)
                    continue

                # all other cases: we have two different non-trivial subtrees, recurse on both ends
                if lvl_num == SyncTree.LEVELS - 1:
                    if D_SYNCTREE: log.debug("...leaf collision")
                    send_subtrees.append(vert)
                else:
                    if D_SYNCTREE: log.debug("...recursing")
                    child_base = vert << SyncTree.BITS_PER_LEVEL
                    next_lvl += range(child_base, child_base + SyncTree.ARITY)
            if self.recv_tree_eof:
                break
            lvl_alive = next_lvl
            lvl_num += 1
        if D_SYNCTREE:
            logging.debug('Send subtrees: %r', send_subtrees)
            logging.debug('Send objects: %r', send_objects)
        return send_subtrees, send_objects


    async def send_objects(self, what):
        send_subtrees, send_objects = what
        rows = []
        for oid in send_objects:
            rows.append(self.db.query_first("select insert_order, id, kind from syncables where id=?", oid))
        for vert in send_subtrees:
            minkey, maxkey = self.synctree.subtree_key_range(vert)
            rows += self.db.query("select insert_order, id, kind from syncables where tree_key >= ? and tree_key < ?",
                                    minkey, maxkey)
        # We need to send objects in insertion order, so that other side can
        # recreate them without violating foreign key constraints
        rows.sort(key=lambda row: row['insert_order'])
        for row in rows:
            await self.send_by_syncable_row(row)
        self.send_sized(b'')
        await self.out_stream.drain()

class SerialMDSync(MDSync):
    async def compute_diff(self):
        local_maxsers = {}
        id2idx = {}
        for store in list(self.db.query('select * from stores')):
            max_serial = self.db.query_first('select max(serial) from syncables where origin_idx=?', store.idx, _assoc=False)[0]
            if max_serial is None or max_serial <= 0: continue
            local_maxsers[store.id] = max_serial
            id2idx[store.id] = store.idx
        log.debug('local_maxsers: %r', local_maxsers)
        (remote_maxsers,) = await self.exchange([('cbor', local_maxsers)], ['cbor'])
        log.debug('remote_maxsers: %r', remote_maxsers)
        to_send = []
        for origin_id, local_maxser in local_maxsers.items():
            remote_maxser = remote_maxsers.get(origin_id, -1)
            if local_maxser > remote_maxser:
                to_send.append((id2idx[origin_id], origin_id, remote_maxser + 1))
        log.debug('to_send: %r', to_send)
        return to_send

    async def send_objects(self, to_send):
        #for origin_idx, origin_id, start in to_send:
        #    log.debug('Sending origin %s(%d), start %d', origin_id, origin_idx, start)
        #    for row in self.db.query("select * from syncables where origin_idx=? and serial>=? order by serial asc",
        #                                origin_idx, start):
        #        await self.send_by_syncable_row(row)
        if to_send:
            origin_conds = " or ".join(  "(origin_idx=%d and serial>=%d)"%(origin_idx, start)
                                         for (origin_idx, origin_id, start) in to_send  )
            # Send in local insertion order to guarantee referential correctness of inserts on target.
            query = "select * from syncables where %s order by insert_order asc" % origin_conds
            for row in self.db.query(query):
                await self.send_by_syncable_row(row)
        self.send_sized(b'')
        await self.out_stream.drain()



def main(args):
    # .buffer is for binary stdio
    st, sub = Store.find(args.store)
    mdsync = MDSync(store=st, file=(sys.stdin.buffer, sys.stdout.buffer))
    asyncio.get_event_loop().run_until_complete(mdsync.run())

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('store')

if __name__ == '__main__':
    main(parser.parse_args())

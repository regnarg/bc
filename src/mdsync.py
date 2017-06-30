#!/usr/bin/python3

import logging
log = logging.getLogger('filoco.mdsync')
logging.basicConfig(level=logging.DEBUG)

from utils import *
from store import *
import struct
import cbor

init_debug(['synctree'])

PROTOCOL = 1

class Protocol:
    def __init__(self, file):
        if isinstance(file, tuple):
            self.in_file, self.out_file = file
        else:
            self.in_file = self.out_file = file
        self.did_hello = False

    def write_sized(self, data):
        self.out_stream.write(struct.pack(self.SIZE_FMT, len(data)))
        self.out_stream.write(data)

    def write_cbor(self, obj):
        # XXX we prefix each CBOR object with a size because the `cbor' module
        # does not support incremental (push) decoding and we would not know
        # how much data to read on the receiving end. This is redundant but
        # apart from rewriting the cbor module and ugly hacks, not much can be
        # done.
        self.write_sized(cbor.dumps(obj))

    async def read_sized(self):
        size = struct.unpack(self.SIZE_FMT, await self.in_stream.read(self.SIZE_BYTES))[0]
        return await self.in_stream.read(size)

    async def read_cbor(self):
        body = await self.read_sized()
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

    async def exchange(self, to_send, to_recv):
        if not self.did_hello:
            to_send = ['hello'] + to_send
            to_recv = ['hello'] + to_recv
        send_task = asyncio.ensure_future(self.send_multi(to_send))
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

class MDSync(Protocol):
    SIZE_FMT = '>L'
    SIZE_BYTES = struct.calcsize(SIZE_FMT)
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
    xchg_timeout = 10
    def __init__(self, store, file):
        super().__init__(file=file)
        self.store = store

    def get_xors(self, positions):
        # TODO: is this better than several queries that can be precompiled?
        ret = self.store.db.query('select pos, xor, chxor from synctree where pos in (%s)'
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
        data = await self.read_sized()
        if data == b'':
            if D_SYNCTREE: log.debug('Received EOF')
            self.recv_tree_eof = True
            return {}
        for pos in range(0, len(data), self.NODE_BYTES):
            chunk = data[pos:pos + self.NODE_BYTES]
            pos, id_xor, chk_xor = struct.unpack(self.NODE_FMT, chunk)
            ret[pos] = (id_xor, chk_xor)
        return ret


    async def do_synctree(self): 
        self.recv_tree_eof = False
        lvl_num = self.START_LVL
        start_off = start_size = 1 << self.START_LVL
        lvl_alive = list(range(start_off, start_off + start_size))
        to_send = []
        send_subtree = []
        while  lvl_num < SyncTree.POS_BITS:
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
                    send_subtree.append(vert)
                    continue

                if my_val == their_val and my_chk == their_chk:
                    continue # no changes

                diff = binxor(my_val, their_val)
                if SyncTree.hash_chk(diff) == binxor(my_chk, their_chk): # only single chnage in subtree
                    if self.store.synctree.has(diff):
                        to_send.append(diff)
                    continue

                # all other cases: we have two different non-trivial subtrees, recurse on both ends
                assert lvl_num < SyncTree.POS_BITS - 1
                if D_SYNCTREE: log.debug("...recursing")
                child_base = vert << SyncTree.BITS_PER_LEVEL
                next_lvl += range(child_base, child_base + SyncTree.ARITY)
            if self.recv_tree_eof:
                break
            lvl_alive = next_lvl
            lvl_num += 1

    async def run(self):
        await self.prepare()
        await self.do_synctree()

def main(args):
    # .buffer is for binary stdio
    st = Store.find(args.store)
    mdsync = MDSync(store=st, file=(sys.stdin.buffer, sys.stdout.buffer))
    asyncio.get_event_loop().run_until_complete(mdsync.run())

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('store')

if __name__ == '__main__':
    main(parser.parse_args())

#!/usr/bin/python3

from random import randrange
from copy import copy
import asyncio

import hashlib

def md4(x): return int(hashlib.new('md4', x.encode('ascii')).hexdigest(), 16)

KEY_BITS = 128
VAL_BITS = 128
MAX_KEY = 2**KEY_BITS
MAX_VAL = 2**VAL_BITS
ISLEAF = 1 << KEY_BITS
BITS_PER_LEVEL = 1
ARITY = 1 << BITS_PER_LEVEL
assert KEY_BITS % BITS_PER_LEVEL == 0
SALT1 = 'a'
SALT2 = 'b'

class ERT:
    def __init__(self):
        self.data = {}
        self.count = 0
    def __getitem__(self, key):
        return self.data[key | ISLEAF]
    def __setitem__(self, key, val):
        assert 0 <= key <= MAX_KEY
        assert 0 <= val <= MAX_VAL
        idx = key | ISLEAF
        oldval = self.data.get(idx, 0)
        if val == 0:
            try: del self.data[idx]
            except KeyError: pass
            else: self.count -= 1
        else:
            if idx not in self.data: self.count += 1
            self.data[idx] = val
        change = val ^ oldval
        while True:
            idx >>= BITS_PER_LEVEL
            if idx <= 0: break
            new = self.data.get(idx, 0) ^ change
            if new == 0:
                try: del self.data[idx]
                except KeyError: pass
            else:
                self.data[idx] = val
    def __delitem__(self, key):
        self[key] = 0
    def __copy__(self):
        r = ERT()
        r.data = dict(self.data)
        return r
    def __contains__(self, key):
        return key|ISLEAF in self.data
    def __len__(self):
        return self.count

SIZE = 10**7
CHANGES = 100

async def endpoint(tree, rx, tx, archive):
    lvl_alive = [1]
    level = 0
    ret = []
    while True:
        sent = { idx: tree.data[idx] for idx in lvl_alive }
        await tx.put(sent)
        recv = await rx.get()
        next_lvl = []
        for vert in set(sent.keys()) + set(recv.keys()):
            mine = sent.get(vert, 0)
            theirs = recv.get(vert, 0)
            if mine != theirs: # differences in subtree, need to explore children
                child_base = vert << BITS_PER_LEVEL
                next_lvl += range(vert, vert + ARITY)
        lvl_alive = next_lvl
        level += 1
                
        

def reconcile(A, B):
    ab_archive = []
    ba_archive = []
    ab = asyncio.Queue()
    ba = asyncio.Queue()
    endp_a = endpoint(A, ba, ab, ab_archive)
    endp_b = endpoint(B, ab, ba, ba_archive)
    fut = asyncio.gather(endp_a, endp_b)
    asyncio.get_event_loop().run_until_complete(fut)

def test():
    orig = ERT()
    for i in range(SIZE):
        while True:
            key = randrange(MAX_KEY)
            if key not in orig: break
        orig[key] = randrange(MAX_VAL)
    assert len(orig) == SIZE

    new = copy(orig)

    for i in range(CHANGES):
        key = randrange(MAX_KEY)
        new[key] = randrange(MAX_VAL)

    reconcile(old, new)



if __name__ == '__main__':
    test()

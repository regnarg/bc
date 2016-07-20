#!/usr/bin/python3

from random import randrange, seed
from copy import copy
import asyncio

import hashlib
from math import *

##seed(42)

def md4(x): return int(hashlib.new('md4', x.encode('ascii')).hexdigest(), 16)

KEY_BITS = 128
VAL_BITS = 128
MAX_KEY = 2**KEY_BITS
MAX_VAL = 2**VAL_BITS
ISLEAF = 1 << KEY_BITS
BITS_PER_LEVEL = 4
ARITY = 1 << BITS_PER_LEVEL
assert KEY_BITS % BITS_PER_LEVEL == 0
LEVELS = KEY_BITS // BITS_PER_LEVEL
SALT1 = 'a'
SALT2 = 'b'

def h1(x): return md4(SALT1 + hex(x))
def h2(x): return md4(SALT2 + hex(x))

class SparseDict(dict):
    def __setitem__(self, key, val):
        if val == 0:
            try: del self[key]
            except KeyError: pass
        else:
            super().__setitem__(key, val)
    def __getitem__(self, key):
        try: return super().__getitem__(key)
        except KeyError: return 0

class XorTree:
    def __init__(self):
        self.data = SparseDict()
        self.cnts = SparseDict()
        self.count = 0
    def __getitem__(self, key):
        return self.data[key | ISLEAF]
    def __setitem__(self, key, val):
        assert 0 <= key <= MAX_KEY
        assert 0 <= val <= MAX_VAL
        idx = key | ISLEAF
        oldval = self.data[idx]
        change = val ^ oldval
        cntdiff = (val != 0) - (oldval != 0)
        while idx > 0:
            self.data[idx] ^= change
            self.cnts[idx] += cntdiff
            assert (self.data[idx] == 0) == (self.cnts[idx] == 0)
            idx >>= BITS_PER_LEVEL
        self.count += cntdiff
    def __delitem__(self, key):
        self[key] = 0
    def __copy__(self):
        r = XorTree()
        r.data = SparseDict(self.data)
        r.cnts = SparseDict(self.cnts)
        r.count = self.count
        return r
    def __contains__(self, key):
        return key|ISLEAF in self.data
    def __len__(self):
        return self.count

class HybridTree:
    def __init__(self):
        self.T1 = XorTree()
        self.T2 = XorTree()
        
    def add(self, key):
        sortkey = h1(key)
        self.T1[sortkey] = key
        self.T2[sortkey] = h2(key)

    def remove(self, key):
        self.T1[key] = 0
        self.T2[key] = 0

    def __copy__(self):
        r = HybridTree()
        r.T1 = copy(self.T1)
        r.T2 = copy(self.T2)
        return r

    def __contains__(self, key):
        return h1(key) in self.T1

SIZE = 10**3
CHANGES = 5000

async def endpoint(H, rx, tx, archive):
    lvl_alive = [1]
    level = 0
    to_send = []
    send_subtree = []
    while lvl_alive:
        print("Level %d, alive %r" % (level, lvl_alive))
        sent = { idx: (H.T1.data[idx], H.T2.data[idx]) for idx in lvl_alive }
        archive.append(sent)
        await tx.put(sent)
        recv = await rx.get()
        next_lvl = []
        for vert in set(sent.keys()) | set(recv.keys()):
            my_val, my_chk = sent.get(vert, (0,0))
            their_val, their_chk = recv.get(vert, (0,0))
            print("Vert %d: my %x/%x, their %x/%x" % (vert, my_val, my_chk, their_val, their_chk))
            if my_val == 0 and my_chk == 0: continue # subtree empty, nothing to send
            if their_val == 0 and their_chk == 0: # they have nothing, send whole subtree, no need to recurse
                send_subtree.append(vert)
                continue

            if my_val == their_val and my_chk == their_chk:
                continue # no changes

            if h2(my_val ^ their_val) == my_chk ^ their_chk: # only single chnage in subtree
                extra_val = my_val ^ their_val
                if extra_val in H: # if we have the extra object, send it
                    to_send.append(extra_val)
                continue

            # all other cases: we have two different non-trivial subtrees, recurse on both ends
            assert level < LEVELS -1
            print("...recursing")
            child_base = vert << BITS_PER_LEVEL
            next_lvl += [ idx for idx in range(child_base, child_base + ARITY) if idx in H.T1.data ]
        lvl_alive = next_lvl
        level += 1
    for vert in send_subtree:
        print("send_subtree", vert)
        # BFS of the subtree
        from collections import deque
        q = deque([vert])
        while q:
            itm = q.popleft()
            if itm & ISLEAF:
                to_send.append(H.T1.data[itm])
            else:
                child_base = itm << BITS_PER_LEVEL
                q += [ idx for idx in range(child_base, child_base + ARITY) if idx in H.T1.data ]
    return to_send
                
        
def xfer_stat(archive):
    # Each vertex containss 2 128b numbers
    return (16*2) * sum( [ len(msg) for msg in archive ] )

def reconcile(A, B):
    ab_archive = []
    ba_archive = []
    ab = asyncio.Queue()
    ba = asyncio.Queue()
    endp_a = endpoint(A, ba, ab, ab_archive)
    endp_b = endpoint(B, ab, ba, ba_archive)
    fut = asyncio.gather(endp_a, endp_b)
    ret_a, ret_b = asyncio.get_event_loop().run_until_complete(fut)
    assert len(ab_archive) == len(ba_archive)
    xfer = xfer_stat(ab_archive) + xfer_stat(ba_archive)
    print("Roundtrips:", len(ab_archive))
    print("Total xfer: %.1f kB" % (xfer/1024))
    print("Per change: %.1f B" % (xfer / (2*CHANGES))) # 2x because CHANGES is per direction
    # This assumes no of the previous rounds succeed and the big enough one succeeds.
    print("IBF roundtrips: %d" % int(floor(log2(2*CHANGES))))
    print("IBF transfer: %.1f B per change" % ( (8+8+4) * int(floor(log2(2*CHANGES)))))
    #print(len(ret_a), ret_a)
    #print(len(ret_b), ret_b)d
    #CHECK CORRECTNESS
    for itm in ret_a: B.add(itm)
    for itm in ret_b: A.add(itm)
    assert set(A.T1.data.values()) == set(B.T1.data.values())

    

def test():
    orig = HybridTree()
    print("Generating base tree")
    for i in range(SIZE):
        key = randrange(MAX_KEY)
        orig.add(key)

    new = copy(orig)

    print("Generating changes")
    for i in range(CHANGES):
        new.add(randrange(MAX_KEY))
        orig.add(randrange(MAX_KEY))

    print(orig.T1.data[1], new.T1.data[1])

    print("Reconciling")
    reconcile(orig, new)



if __name__ == '__main__':
    test()

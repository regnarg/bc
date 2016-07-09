#!/usr/bin/python3

from random import randrange
from copy import copy
import asyncio

import hashlib

KEY_BITS = 128
MAX_KEY = 2**KEY_BITS
ISLEAF = 1 << KEY_BITS
def md4(x): return int(hashlib.new('md4', x.encode('ascii')).hexdigest(), 16)
SALT1 = 'a'
SALT2 = 'b'
def h1(x): return md4(SALT1 + hex(x))
def h2(x): return md4(SALT2 + hex(x))

from itertools import takewhile
def common_prefix(a,b):
    return ''.join( takewhile((lambda pair: pair[0]==pair[1]), zip(a,b)) )

class Edge:
    def __init__(self, label):
        self.label = label
        self.val = 0
        self.chk = 0
        self.cnt = 1
        self.children = [None, None]

    def find(self, path, parent=None):
        """Look up a given path. Return pair (edge, path_from_edge).
        
        If the path lies on an existing edge, that edge is returned.
        If not, the last existing edge encountered while looking up `path` is returned.
        In both cases, `path_from_edge` is a path of the looked-up node relative to the
        top of the returned edge."""
        if key == self.label: return (self, '')
        elif self.label.startswtih(key): return (parent, key)
        elif key.startswith(self.label):
            rest = key[len(self.label):]
            ch = self.children[int(rest[0])]
            return ch.find(rest, self)
        else:
            return (parent, key)

    def update(self):
        """Recompute `val` and `chk` hashes from child values."""
        self.val = self.children[0].val ^ self.children[1].val
        self.chk = self.children[0].chk ^ self.children[1].chk
        self.cnt = self.children[0].cnt + self.children[1].cnt

    def insert(self, node):
        """Insert a new node branching off this edge. Return the node created to split the edge."""

        prefix = common_prefix(self.label, node.label)
        plen = len(prefix)
        assert 0 < plen < len(self.label)

        new_split = Node(prefix)

        for ch in (node, self):
            ch.label = ch.label[plen:]
            new_split.children[int(ch.label[0])] = ch
        new_split.update()

        return new_split


class HybridTree:
    def __init__(self):
        self.root = None

    def find(self, key):
        pass

    def insert(self, node):
        if self.root is None:
            self.root = node


    def add(self, val):
        key = bin(h1(val))[2:]
        chk = h2(val)

        self.find(key)

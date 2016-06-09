#!/usr/bin/python3

import sys,os,re

BLOCK_SIZE = 4096
INODE_SIZE = 256
INODES_PER_BLOCK = BLOCK_SIZE//INODE_SIZE
ranges = []

with open('inode-locs', 'r') as fd:
    for line in fd:
        line = line.strip()
        start, end = map(int, line.split('-'))
        ranges.append((start, end))

ino2name = {}
with open('tdata.all.ino', 'rb') as fd:
    for line in fd:
        ino,fn = line.strip().split(b' ', 1)
        ino = int(ino)
        ino2name[ino] = fn

def blk2inos(blk):
    ino = 1
    for start, end in ranges:
        if blk >= start and blk < end:
            ino += (blk-start) * INODES_PER_BLOCK
            return list(range(ino, ino + INODES_PER_BLOCK))
        else:
            length = end - start
            ino += length * INODES_PER_BLOCK
    return []


JLS_RE = re.compile(r'\d+:\s*Allocated FS Block (\d+)')

with open('jls', 'r') as fd:
    for line in fd:
        line = line.strip()
        m = JLS_RE.match(line)
        if not m: continue
        blk = int(m.group(1))
        inos = blk2inos(blk)
        if not inos: continue
        files = [ ino2name.get(ino, '?') for ino in inos ]

        print("blk", blk, "inos", inos, "files", files)



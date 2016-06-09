#!/usr/bin/python3

dev = open('/dev/terrier/data', 'rb')

with open('inode-locs', 'r') as fd:
    for line in fd:
        line = line.strip()
        start, end = map(int, line.split('-'))
        cnt = end-start+1
        dev.seek(start*4096)
        dev.read(cnt*4096)


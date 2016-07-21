#!/usr/bin/python

import sys,os
import json
from butter.fhandle import *
from subprocess import *
import binascii

r = []

root = sys.argv[1]
proc = Popen(["find", root, "-xdev", "-printf", "%i %y %p\\n"], stdout=PIPE)

def handle_to_str(fh):
    """Return a string representation of a file handle."""
    return "%d:%s" % (fh.type, binascii.hexlify(fh.handle).decode())
def str_to_handle(s):
    """Convert a string representation to a butter-compatible FileHandle object."""
    try:
        tp, data = s.split(':', 1)
        tp = int(tp)
        data = binascii.unhexlify(data)
    except ValueError:
        raise ValueError("invalid handle: '%s'" % s)
    return FileHandle(tp, data)

for line in proc.stdout:
    line = line.rstrip(b'\n')
    ino, type, fn = line.split(b' ', 2)
    ino = int(ino)
    type = type.decode('ascii')
    try: fn = fn.decode('utf-8')
    except UnicodeDecodeError:
        print("Invalid UTF-8:", ascii(fn))
        continue
    handle, mntid = name_to_handle_at(AT_FDCWD, fn)
    r.append((ino,handle_to_str(handle),type,fn))

with open('inos.json', 'w') as fd:
    json.dump(r, fd,indent=2)

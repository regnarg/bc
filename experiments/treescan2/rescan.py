#!/usr/bin/python

import sys,os
import json
from butter.fhandle import *
from subprocess import *
from time import *
import binascii
import random

root = sys.argv[1]
root_fd = os.open(root, os.O_RDONLY|os.O_DIRECTORY)

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

def rescan(*, order='orig', use_handles=False, only_dir=False):
    with open('inos.json') as fd:
        data = [ (ino, str_to_handle(handle), type, path) for (ino,handle,type,path) in  json.load(fd) ]
    check_call(['sysctl', '-w', 'vm.drop_caches=3'])
    if order == 'ino':
        data.sort()
    elif order == 'rand':
        random.shuffle(data)
    elif order == 'orig':
        pass
    else:
        raise ValueError

    if only_dir: data = [ itm for itm in data if itm[2] != 'd' ]

    errors = 0
    start = clock_gettime(CLOCK_MONOTONIC)
    for ino, handle, type, fn in data:
        try:
            if use_handles:
                fd = open_by_handle_at(root_fd, handle, os.O_PATH)
                st = os.fstat(fd)
                os.close(fd)
            else:
                st = os.lstat(fn)
        except (FileNotFoundError, StaleHandle):
            errors += 1
    end = clock_gettime(CLOCK_MONOTONIC)
    return end-start, errors


for order in ['ino', 'orig', 'rand']:
    for only_dir in [True, False]:
        for use_handles in [False, True]:
            time, errors = rescan(order=order, use_handles=use_handles, only_dir=only_dir)
            print("%-4s %-4s %-4s %.1f %d" % (order, 'handle' if use_handles else 'path', 'dir' if only_dir else 'all',  time, errors))

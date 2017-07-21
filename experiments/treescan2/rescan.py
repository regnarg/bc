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

def rescan(*, order='orig', use_handles=False, type=None):
    with open('inos.json') as fd:
        data = [ (ino, str_to_handle(handle), type, path) for (ino,handle,type,path) in  json.load(fd) ]
    check_call(['sysctl', '-w', 'vm.drop_caches=3'], stdout=open("/dev/null", "w"))
    if order == 'ino':
        data.sort()
    elif order == 'rand':
        random.shuffle(data)
    elif order == 'orig':
        pass
    else:
        raise ValueError

    if type: data = [ itm for itm in data if itm[2] == type ]

    errors = 0
    ok = 0
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
        else:
            ok += 1
    end = clock_gettime(CLOCK_MONOTONIC)
    return end-start, errors, ok


for type in ['f', 'd', None]:
    for order in ['ino', 'orig']: #, 'rand']:
        for use_handles in [True, False]:
            time, errors, ok = rescan(order=order, use_handles=use_handles, type=type)
            min = time // 60
            sec = time % 60
            tput = ok / time * 60
            if tput > 10**6:
                tf = "%.1f$\,$M" % (tput / 10**6)
            else:
                tf = "%.1f$\,$k" % (tput / 10**3)
            print("%-4s %-4s %-4s %.1f %d:%02.0f %s %d %d" % (order, 'handle' if use_handles else 'path', type,  time, min, sec, tf, errors, ok))

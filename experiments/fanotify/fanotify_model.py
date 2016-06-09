#!/usr/bin/python3

import sys,os,stat
import threading as thr
from queue import Queue
from butter.fanotify import *
from uuid import uuid1

FAN_MODIFY_DIR = 0x00040000

dir = sys.argv[1]
if not os.path.ismount(dir): raise RuntimeError

f = Fanotify(0)
f.watch(sys.argv[1], FAN_CLOSE_WRITE | FAN_MODIFY_DIR | FAN_OPEN | FAN_EVENT_ON_CHILD | FAN_ONDIR, FAN_MARK_ADD|FAN_MARK_MOUNT)

event_q = Queue()
# Queue events while scanning, kernel buffer is small
def event_reader(q):
    while True:
        ev = f.read_event()
        q.put(ev)
event_reader_thr = thr.Thread(target=event_reader, args=(q,))
event_reader_thr.start()

class FsObject(object):
    def __init__(self, ino):
        self.ino = ino
        self.uid = uuid()
        self.parent = None
        self.name = None

class Directory(FsObject):
    def __init__(self, ino):
        super().__init__(ino)
        self.children = None # not yet known


class File(FsObject):
    pass


objs = {}

def stat2obj(st):
    if isinstance(st, int):
        st = '/proc/self/fd/%d' % st
    if isinstance(st, str):
        st = os.lstat(st)
    if ino in objs:
        return objs[ino]
    else:
        objs[ino] = obj = (Directory if stat.S_ISDIR(st.st_mode) else File)(st.st_ino)
        return obj

def scan(dirfd):
    dirobj = get_obj(dirfd)
    new_entries = {}
    child_scan = []

    pth = '/proc/self/fd/%d' % dirfd
    for name in os.listdir(pth):
        new_entries[name] = obj = stat2obj(pth + '/' + name)
        if name != obj.name or parent != obj.parent:
            print("Detected rename", obj, obj.parent, obj.name, dirobj, name)
        obj.parent = dirobj
        obj.name = name
        if isinstance(obj, Directory) and obj.children is None:
            child_scan.append(obj)

root_fd = op.open(dir, os.O_RDONLY | os.O_DIRECTORY)
root_ino = os.fstat(root).st_ino

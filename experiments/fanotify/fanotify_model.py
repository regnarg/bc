#!/usr/bin/python3

import sys,os,stat
import threading as thr
from queue import Queue
from butter.fanotify import *
import butter.fanotify
from uuid import uuid1 as uuid
import signal

# Hotfix for `butter` bug.
butter.fanotify._get_buffered_length = lambda fd: 4096

print(Fanotify.mro())

signal.signal(signal.SIGINT, lambda *a: sys.exit())

FAN_MODIFY_DIR = 0x00040000

root = sys.argv[1]
if not os.path.ismount(root): raise RuntimeError

f = Fanotify(0)
f.watch(sys.argv[1], FAN_CLOSE_WRITE | FAN_MODIFY_DIR | FAN_EVENT_ON_CHILD | FAN_ONDIR, FAN_MARK_ADD|FAN_MARK_MOUNT)

event_q = Queue()
# Queue events while scanning, kernel buffer is small
def event_reader(q):
    while True:
        ev = f.read_event()
        q.put(ev)
event_reader_thr = thr.Thread(target=event_reader, args=(event_q,))
event_reader_thr.start()

class FsObject(object):
    def __init__(self, ino):
        self.ino = ino
        self.uid = uuid()
        self.parent = None
        self.name = None

    def path(self):
        if self.parent is None or self.parent is root_obj:
            return self.name
        else:
            return self.parent.path() + '/' + self.name

    def __repr__(self, include_parent=True):
        par = ''
        if include_parent:
            if self.parent is None: par = ''
            else: par = ' parent=%d' % self.parent.ino
            
        return '<%s ino=%d%s>' % (self.path(), self.ino, par)

class Directory(FsObject):
    def __init__(self, ino):
        super().__init__(ino)
        self.children = None # not yet known


class File(FsObject):
    pass


objs = {}

def stat2obj(arg):
    if isinstance(arg, int):
        arg = os.stat('/proc/self/fd/%d' % arg)
    elif isinstance(arg, str):
        arg = os.lstat(arg)
    if arg.st_ino in objs:
        return objs[arg.st_ino]
    else:
        objs[arg.st_ino] = obj = (Directory if stat.S_ISDIR(arg.st_mode) else File)(arg.st_ino)
        return obj

def scan(dirfd):
    pth = '/proc/self/fd/%d' % dirfd
    dirobj = stat2obj(dirfd)
    print("Scan",dirfd,os.readlink(pth), dirobj)
    new_entries = {}
    child_scan = []

    for name in os.listdir(pth):
        try:
            new_entries[name] = obj = stat2obj(pth + '/' + name)
        except IOError:
            continue
        def changed(a,b):
            return a is not None and b is not None and a != b
        if changed(name, obj.name) or changed(dirobj, obj.parent):
            print("Detected rename", obj, obj.parent, obj.name, dirobj, name)
        obj.parent = dirobj
        obj.name = name
        if isinstance(obj, Directory) and obj.children is None:
            child_scan.append(os.open(pth + '/' + name, os.O_DIRECTORY))
        print("  *",name, obj)
        new_entries[name] = obj

    dirobj.children = new_entries
    for child_fd in child_scan:
        scan(child_fd)
        os.close(child_fd)

root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
root_obj = stat2obj(root_fd)
root_obj.name = '(root)'

scan(root_fd)
while True:
    ev = event_q.get()
    print("EVENT", ev)
    if ev.mask & FAN_MODIFY_DIR:
        scan(ev.fd)
    os.close(ev.fd)
    print("OBJS", objs)
print("end")


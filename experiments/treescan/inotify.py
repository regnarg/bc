#!/usr/bin/python
import pyinotify
from subprocess import check_call


def read_dataset(name):
    files = []
    with open(name, 'rb') as fd:
        for line in fd:
            ino,fn = line.strip().split(b' ', 1)
            files.append((ino,fn))
    return files

files = read_dataset('tdata.dirs.ino')
files.sort()

check_call(['free', '-m'])
mgr = pyinotify.WatchManager()
notifier = pyinotify.Notifier(mgr, lambda *a:None)


perc = 0
for idx, (ino, fn) in enumerate(files):
    mgr.add_watch(fn.decode('utf-8', errors='ignore'),
            pyinotify.IN_CREATE|pyinotify.IN_DELETE|pyinotify.IN_MOVED_TO|pyinotify.IN_MOVED_FROM)
    newperc = 100*idx // len(files)
    if newperc >= perc+5:
        print(newperc, "%")
        perc = newperc

print("Done", len(files))
check_call(['free', '-m'])
notifier.loop()

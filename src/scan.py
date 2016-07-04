#!/usr/bin/python3

"""
Usage: scan.py [options] <dir>

Options:
    --watch     Watch filesystem changes as they happen
    --watch-bg  Go to background before starting the watch phase
    --watch-backend=(fanotify|inotify)
    --rescan
"""

import sys, os, posix
import base64
import logging

import libmount
from butter.fanotify import *
from butter.fhandle import *

from utils import *
from store import Store

FAN_MODIFY_DIR = 0x00040000

log = logging.getLogger('loco.fanotify_watcher')


class FanotifyWatcher:
    MASK = FAN_OPEN | FAN_CLOSE_WRITE | FAN_MODIFY_DIR | FAN_ONDIR
    def __init__(self, dir, *, mount=False):
        if not is_mountpoint(dir) and not mount:
            err("Watched directory '%s' must be a mountpoint."
                    " The -m option might help with that." % args.dir)
        self.root = dir
        self.store = Store(self.root_fd)
        self.db = self.store.open_db()
        self.cur = self.db.cursor()
        self.root_fd = self.store.root_fd

        self.fan = Fanotify(0, os.O_RDONLY|os.O_PATH)
        self.fan.watch(None, self.MASK, FAN_MARK_ADD|FAN_MARK_MOUNT, dfd=self.root_fd)
        # Ignore events in the `.loco` metadata directory (e.g. updates of our
        # internal databases).
        self.fan.watch(None, self.MASK, FAN_MARK_ADD|FAN_MARK_IGNORED_MASK,
                dfd=self.store.meta_fd)

    def handle_exists(handle):
        try:
            fd = open_by_handle_at(self.store.root_fd, str_to_handle(handle), os.O_PATH)
        except StaleHandle:
            return False
        else:
            os.close(fd)
            return True


    def on_fanotify_event(self, event):
        try:
            if issubpath(event.filename, self.store.meta_path):
                # New files can appear in the metadata directory. We must dynamically
                # add them to the ignore mask to prevent unnecessary events.
                self.fan.watch(None, self.MASK, FAN_MARK_ADD | FAN_MARK_IGNORED_MASK
                        | FAN_MARK_IGNORED_SURV_MODIFY, dfd=event.fd)
                return
            if not issubpath(event.filename, self.store.root_path):
                # We get events for the whole mountpoint, ignore those outside our
                # tree. This is inefficient but the best we can do.
                return
            if ev.mask & FAN_NOTIFY_DIR:
                self.scan(event.fd)
        finally:
            os.close(event.fd)

    def find_inode(self, fd):
        stat = os.fstat(fd)
        handle = handle_to_str(name_to_handle_at(fd, "", AT_EMPTY_PATH)[0])
        with self.db:
            row = self.db.query_first('select * from inodes where ino=?')
            if row is not None:
                serial, old_handle = row
                if old_handle == handle or handle_exists(old_handle):
                    return row
                else:
                    # If we get here, we can be sure that the original inode ceased
                    # to exist. We can safely remove it from the database
                    # without worrying about race conditions.
                    self.db.execute('delete from inodes where serial=?', row.serial)
            # We can insert safely without any locking. Because we hold an open FD to
            # the inode, it cannot just disappear and thus we are writing correct data.
            self.db.insert('inodes', ino=ino, handle=handle)
            return self.db.query_first('select * from inodes where ino=?')


    def scan(dirfd, *, recursive=False, new_recursive=False, update_stat=False):
        dirobj = self.find_inode(dirfd)
        with self.db:
            for entry in fdscandir(dirfd):
                # Grab an O_PATH file descriptor to guarantee that all the subsequent
                # operations (fstat, name_to_handle_at, ...) refer to the same inode,
                # even if the name is replaced.
                fd = os.open(entry.name, os.O_PATH, dir_fd=dirfd)
                obj = self.find_inode(fd)

                # self.db.update('inodes', 'parent=? and name=? and ino!=?',
                #         dirobj.ino, entry.name, obj.ino, name=None, parent=None,
                #         updated=1)
                # if self.db.changes(): # A replacement took place
                #     pass

                with self.db:
                    old_obj = self.db.query_first('select * from inodes where'
                                ' parent = ? and name = ?', dirobj.ino, entry.name)
                    if old_obj is None or obj.ino != old_obj.ino:
                        if old_obj is not None:
                            self.db.update('inodes', 'ino=?', old_obj.ino, parent=None,
                                name=None, updated=1,
                                replaced_uuid=(old_obj.uuid or old_obj.replaced_uuid))
                        self.db.update('inodes', 'ino=?', obj.ino, parent=dirobj.ino,
                                        name=entry.name)



if __name__ == '__main__':
    app = FanotifyWatcher(**docopt_attr(__doc__))
    app.main()

#!/usr/bin/python3

"""
Usage: scan.py [options] <dir>
"""

import sys, os, posix, stat
import logging
import asyncio

import libmount
from butter.fanotify import *
from butter.fhandle import *

from utils import *
from store import Store

FAN_MODIFY_DIR = 0x00040000

log = logging.getLogger('filoco.scan')
logging.basicConfig(level=logging.DEBUG)

class CrossMount(Exception): pass

class FanotifyWatcher:
    MASK = FAN_OPEN | FAN_CLOSE_WRITE | FAN_MODIFY_DIR | FAN_ONDIR
    def __init__(self, dir):
        if not is_mountpoint(dir):
            err("Watched directory '%s' must be a mountpoint."
                    " The -m option might help with that." % args.dir)
        self.store = Store.find(dir or '.')
        self.db = self.store.open_db()
        self.root_fd = self.store.root_fd
        self.root_mnt = name_to_handle_at(self.root_fd, "", AT_EMPTY_PATH)[1]
        self.scan_queue = asyncio.Queue()
        self.loop = asyncio.get_event_loop()

    def handle_exists(handle):
        try:
            fd = open_by_handle_at(self.store.root_fd, str_to_handle(handle), os.O_PATH)
        except StaleHandle:
            return False
        else:
            os.close(fd)
            return True


    def on_fanotify_event(self, event):
        log.debug("Got fanotify event: %r", event)
        try:
            if issubpath(event.filename, self.store.meta_path):
                log.debug("Event on meta file %s. Ignoring.", event.filename)
                # New files can appear in the metadata directory. We must dynamically
                # add them to the ignore mask to prevent unnecessary events.
                self.fan.watch(None, self.MASK, FAN_MARK_ADD | FAN_MARK_IGNORED_MASK
                        | FAN_MARK_IGNORED_SURV_MODIFY, dfd=event.fd)
                return
            if not issubpath(event.filename, self.store.root_path):
                # We get events for the whole mountpoint, ignore those outside our
                # tree. This is inefficient but the best we can do.
                return
            if event.mask & FAN_MODIFY_DIR:
                self.scan(event.fd)
        finally:
            os.close(event.fd)

    def on_fanotify_readable(self):
        for event in self.fan.read_events():
            self.on_fanotify_event(event)

    def init_fanotify(self):
        self.fan = Fanotify(0, os.O_RDONLY)
        self.fan.watch(None, self.MASK, FAN_MARK_ADD|FAN_MARK_MOUNT, dfd=self.root_fd)
        # Ignore events in the `.loco` metadata directory (e.g. updates of our
        # internal databases).
        self.fan.watch(None, self.MASK, FAN_MARK_ADD|FAN_MARK_IGNORED_MASK,
                dfd=self.store.meta_fd)
        self.loop.add_reader(self.fan.fileno(), self.on_fanotify_readable)

    def find_inode(self, fd, *, is_root=False):
        st = os.fstat(fd)
        handle, mntid = name_to_handle_at(fd, "", AT_EMPTY_PATH)
        if mntid != self.root_mnt: raise CrossMount()
        handle = handle_to_str(handle)
        with self.db:
            obj = self.db.query_first('select * from inodes where ino=?', st.st_ino)
            if obj is not None:
                if obj.handle == handle or handle_exists(obj.handle):
                    return obj
                else:
                    if is_root or obj.iid == 'ROOT':
                        raise RuntimeError("Root replacement not supported")
                    # If we get here, we can be sure that the original inode ceased
                    # to exist. We can safely remove it from the database
                    # without worrying about race conditions.
                    self.db.execute('delete from inodes where iid=?', obj.iid)
            if is_root: iid = 'ROOT'
            else: iid = gen_uuid()
            if stat.S_ISDIR(st.st_mode):
                tp = 'D'
            elif stat.S_ISREG(st.st_mode):
                tp = 'r'
            elif stat.S_ISLNK(st.st_mode):
                tp = 'l'
            else:
                tp = 'S' # special file (socket, fifo, device)
            # We can insert safely without any locking. Because we hold an open FD to
            # the inode, it cannot just disappear and thus we are writing correct data.
            self.db.insert('inodes', ino=st.st_ino, handle=handle, iid=iid, type=tp)
            if tp == 'D': self.scan_queue.put_nowait(os.dup(fd))
            return self.db.query_first('select * from inodes where ino=?', st.st_ino)


    def scan(self, dirfd):
        log.debug("Scanning %d (%s)", dirfd, frealpath(dirfd))
        try: dirobj = self.find_inode(dirfd)
        except CrossMount: return
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

                try:
                    with self.db:
                        old_obj = self.db.query_first('select * from links join inodes '
                                    ' using (ino) where parent = ? and name = ?',
                                    dirobj.ino, entry.name)
                        if old_obj is None or obj.ino != old_obj.ino:
                            if old_obj is not None:
                                self.db.update('links', 'parent=? and name=?',
                                        dirobj.ino, entry.name, ino=obj.ino)
                            else:
                                log.debug("Linking %s into %s", frealpath(fd),
                                        frealpath(dirfd))
                                self.db.insert('links', ino=obj.ino, parent=dirobj.ino,
                                            name=entry.name)
                finally:
                    if fd is not None: os.close(fd)


    def check_root(self):
        if self.db.query_first("select * from inodes where iid = 'ROOT'") is None:
            log.debug("No root record, triggering scan")
            self.find_inode(self.root_fd, is_root=True)

    def queue_unscanned(self):
        for obj in self.db.query("select * from inodes where type='D'"):
            self.scan_queue.put_nowait(obj)

    async def scan_worker(self):
        """A coroutine that consumes the scan queue and runs scan() appropriately."""
        log.debug("scan_worker started")
        while True:
            itm = await self.scan_queue.get()
            self.scan(itm)
            # If the queue is long (e.g. during a full rescan), we need to give
            # the event loop a chance to run.
            await asyncio.sleep(0) # https://github.com/python/asyncio/issues/284

    async def fanotify_worker(self):
        pass

    def init(self):
        log.debug("init")
        self.init_fanotify()
        #self.queue_unscanned()
        self.check_root()
        self.loop.create_task(self.scan_worker())

    def main(self):
        self.init()
        self.loop.run_forever()



if __name__ == '__main__':
    app = FanotifyWatcher(**docopt_attr(__doc__))
    app.main()

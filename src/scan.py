#!/usr/bin/python3

import sys, os, posix, stat
import logging
import asyncio
from subprocess import check_call

import libmount
from butter.fanotify import *
from butter.fhandle import *

from utils import *
from store import *

FAN_MODIFY_DIR = 0x00040000

log = logging.getLogger('filoco.scan')
logging.basicConfig(level=logging.DEBUG)

class CrossMount(Exception): pass


# Filesystem Event Types (`event` column in `fslog` table)
EVENT_CREATE = 1 # new inode came into being
EVENT_LINK = 2
EVENT_UNLINK = 3
EVENT_DELETE = 4 # the inode is really gone
EVENT_MODIFY = 5 # a file's content was modified

init_debug(('queue','scan','mdupdate'))



ScanRequest = namedtuple('ScanRequest', 'prio seq action target')
# An item in the scan queue.
# prio: the sort priority (usually the inode number to faciliate sequential access,
#       but it could also be real importance-based priority)
# action: one of
SR_CHECK = 1 # do a stat and compare type/size/mtime/ctime, do rescan if necessary
SR_SCAN = 2  # do a full rescan of contents (i.e., readdir)
SR_SCAN_RECURSIVE = 3 # ...and recurse to all subdirs

class Scanner:
    # TODO: Running two scanners in parallel in the same repo might wreak havoc.
    #       Either fix or add some kind of locking.
    FANOTIFY_MASK = FAN_CLOSE_WRITE | FAN_MODIFY_DIR | FAN_ONDIR # FAN_OPEN
    SCAN_QUEUE_SIZE = 0 # TODO: do we want to limit this?
    QUEUE_MAX_FDS = 1000
    FANOTIFY_INTERVAL = 5
    # How many seconds to wait before creating a FOB for a new inode. This is necessary
    # to handle the copy-and-rewrite idiom. If the temporary copy replaces an existing
    # FOB within this timeframe, it will be considered a new version of that FOB instead
    # of creating a new FOB.
    FOB_CREATE_WAIT = 30

    def __init__(self, dir, *, watch_mode='none', init_scan=None, recursive=False):
        #if not is_mountpoint(dir):
        #    err("Watched directory '%s' must be a mountpoint."
        #            " The -m option might help with that." % args.dir)
        self.store, self.start_path = Store.find(dir or '.')
        self.db = self.store.db
        self.root_fd = self.store.root_fd
        self.scan_queue = asyncio.PriorityQueue(self.SCAN_QUEUE_SIZE)
        self.queue_fds = 0
        self.last_queue_seq = 0
        self.loop = asyncio.get_event_loop()
        self.watch_mode = watch_mode
        if init_scan is None:
            if watch_mode == 'none': init_scan = 'all'
            else: init_scan = 'pending'
        self.init_scan = init_scan
        self.recursive = recursive
        self.scan_task = None
        self.from_notify = False
        if self.start_path != Path() and not self.recursive:
            raise ValueError("Scanning a specific subtree is only supported with -r")

    def on_fanotify_event(self, event):
        fd = FD(event.fd)
        log.debug("Got fanotify event: %r", event)
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
            self.from_notify = True
            try:
                self.scan(fd)
            finally:
                self.from_notify = False

    def on_fanotify_readable(self):
        for event in self.fan.read_events():
            self.on_fanotify_event(event)

    def init_fanotify(self):
        self.fan = Fanotify(0, os.O_RDONLY)
        self.fan.watch(None, self.FANOTIFY_MASK, FAN_MARK_ADD|FAN_MARK_MOUNT, dfd=self.root_fd)
        # Ignore events in the `.loco` metadata directory (e.g. updates of our
        # internal databases).
        self.fan.watch(None, self.FANOTIFY_MASK, FAN_MARK_ADD|FAN_MARK_IGNORED_MASK,
                dfd=self.store.meta_fd)
        #self.loop.add_reader(self.fan.fileno(), self.on_fanotify_readable)

    async def fanotify_worker(self):
        fan_fd = self.fan.fileno()
        while True:
            log.debug("fanotify_worker selecting")
            await async_wait_readable(fan_fd)
            log.debug("fanotify_worker readable")
            self.on_fanotify_readable()
            log.debug("fanotify_worker sleeping")
            await asyncio.sleep(self.FANOTIFY_INTERVAL)


    def push_scan(self, action, target):
        if self.queue_fds >= self.QUEUE_MAX_FDS:
            target.release_fd()
        prio = target.ino or 0
        self.last_queue_seq += 1
        sr = ScanRequest(prio, self.last_queue_seq, action, target)
        if D_QUEUE: log.debug("Queueing %r", sr)
        self.scan_queue.put_nowait(sr)
        if target.fd: self.queue_fds += 1
        self.start_scan_worker()

    def scan_by_query(self, where, *args, action=SR_SCAN):
        log.debug('Scanning(%s) by query %s %r' % (action,where,args))
        for row in self.db.query('select * from inodes where '+where, *args):
            self.push_scan(action, InodeInfo.from_db(self.store, row))

    def check(self, info):
        if D_SCAN: log.debug('Checking %r', info)
        if not info.iid: raise ValueError("Cannot recheck object not in database")
        try:
            st = info.get_stat(True)
        except (StaleHandle, FileNotFoundError):
            self.store.delete_inode(info)
            #self.store.delete_object(info) # TODO delete from database
            return
        disk_tuple = (st.st_size, st.st_mtime, st.st_ctime, SCAN_UP_TO_DATE)
        db_tuple = self.db.query_first('select size, mtime, ctime, scan_state from inodes where iid=?', info.iid, _assoc=False)
        if disk_tuple != db_tuple:
            if D_SCAN: log.debug('Change! db tuple: %r, fs tuple: %r', db_tuple, disk_tuple)
            # Probably better to scan now than queue it because the inode is already
            # in cache.
            self.scan(info, fresh_stat=True)

    def scan(self, info, *, obj=None, fresh_stat=False, recursive=False):
        if obj is None:
            obj = self.store.find_inode(info)
            if obj is None: return
        try:
            info.get_stat(force=not fresh_stat)
        except (FileNotFoundError, StaleHandle):
            self.db.execute('delete from inodes where iid=?', obj.iid)
            return
        if info.type == 'd':
            self.scan_dir(info, dirobj=obj, fresh_stat=True, recursive=recursive)
        else:
            if info.type == 'r' and obj.fob:
                with self.db.ensure_transaction():
                    new_fcv = self.store.create_working_version(obj.fob, obj.fcv)
                    self.db.update('inodes', 'iid=?', info.iid, fcv=new_fcv, scan_state=SCAN_UP_TO_DATE,
                                size=info.stat.st_size, mtime=info.stat.st_mtime, ctime=info.stat.st_ctime)
                    obj.fcv = new_fcv
            else:
                self.db.update('inodes', 'iid=?', info.iid, scan_state=SCAN_UP_TO_DATE,
                                size=info.stat.st_size, mtime=info.stat.st_mtime, ctime=info.stat.st_ctime)

            

    def scan_dir(self, dirinfo, *, dirobj=None, fresh_stat=False, recursive=False):
        if D_SCAN: log.debug("Scanning %r", dirinfo)
        seen  = set()
        st_start = dirinfo.get_stat(force=not fresh_stat)
        if dirobj is None:
            try: dirobj = self.store.find_inode(dirinfo)
            except CrossMount: return
        assert dirobj.type == 'd'
        assert dirinfo.iid
        with self.db.ensure_transaction():
            for entry in fdscandir(dirinfo.get_fd()):
                if entry.name == '.filoco': continue
                try:
                    entry.name.encode('utf-8')
                except UnicodeEncodeError:
                    log.warning('Invalid UTF-8 name: %s/%s. Skipping.', ascii(frealpath(dirinfo.fd)), ascii(entry.name))
                    continue
                seen.add(entry.name)
                # Grab an O_PATH file descriptor to guarantee that all the subsequent
                # operations (fstat, name_to_handle_at, ...) refer to the same inode,
                # even if the name is replaced.
                fd = FD.open(entry.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=dirinfo.get_fd().fd)
                info = InodeInfo(store=self.store, fd=fd)

                obj, created = self.store.find_or_create_inode(info)
                # self.db.update('inodes', 'parent=? and name=? and ino!=?',
                #         dirobj.ino, entry.name, obj.ino, name=None, parent=None,
                #         updated=1)
                # if self.db.changes(): # A replacement took place
                #     pass
                old_obj = self.db.query_first('select * from links join inodes '
                            ' using (ino) where parent = ? and name = ?',
                            dirobj.ino, entry.name)
                if old_obj is None or obj.ino != old_obj.ino:
                    if old_obj is not None:
                        self.db.update('links', 'parent=? and name=?',
                                dirobj.ino, entry.name, ino=obj.ino)
                    else:
                        self.db.insert('links', ino=obj.ino, parent=dirobj.ino,
                                    name=entry.name)
                    if D_MDUPDATE:
                        log.debug("Linking %s into %s", frealpath(fd),
                            frealpath(dirinfo.fd))
                    self.on_link(dirinfo, dirobj, entry.name, info, obj, old_obj)
                    #self.db.insert('fslog', event=EVENT_LINK, iid=obj.iid, parent_iid=dirobj.iid,
                        #                        name=entry.name)
                if recursive and stat.S_ISDIR(info.stat.st_mode):
                    self.push_scan(SR_SCAN_RECURSIVE, info)
            to_del = []
            for obj in self.db.query('select rowid, name from links where parent=?', dirobj.ino):
                if obj.name not in seen:
                    if D_MDUPDATE:
                        log.debug("Ulinking %s from %s" % (obj.name, frealpath(dirinfo.fd)))
                    to_del.append((obj['rowid'],))
                    #self.db.insert('fslog', event=EVENT_UNLINK, iid=obj.iid, parent_iid=dirobj.iid,
                    #                        name=entry.name)
            if to_del:
                self.db.executemany('delete from links where rowid=?', to_del)
            st_end = dirinfo.get_stat(force=True)
            if stat_tuple(st_start) == stat_tuple(st_end):
                # No racy changes during scan
                self.db.update('inodes','ino=?', dirobj.ino, scan_state=SCAN_UP_TO_DATE, **stat_tuple(st_end))
            else:
                log.warn("Race condition during directory scan of %r, needs further rescan")
                self.db.update('inodes','ino=?', dirobj.ino, scan_state=SCAN_NEEDS_RESCAN)
                # TODO schedule delayed rescan (exp. backoff ideally)

    def on_link(self, parent_info, parent_obj, name, info, obj, old_obj=None):
        # To create a FLV, we need parent FOB. But it may happen because of race conditions
        # that we got linked to parent before parent got a FOB assigned. In that case,
        # on_link_to_fob will be called later when we assign FOB to parent.
        if info.type == 'r' and old_obj and old_obj.type == 'r' and old_obj.fob and not obj.fob:
            if D_MDUPDATE: log.debug("Detected replacement: fob (%s, %s, %s), inode %d(%s) -> %d(%s)",
                                    old_obj.fob, old_obj.flv, old_obj.fcv, old_obj.ino, old_obj.iid, obj.ino, obj.iid)
            self.assign_fob(info, obj, fob_id=old_obj.fob, flv_id=old_obj.flv,
                            fcv_id=self.store.create_working_version(old_obj.fob, old_obj.fcv))
        if parent_obj.fob or parent_obj.iid == 'ROOT':
            self.on_link_to_fob(parent_info, parent_obj, name, info, obj, old_obj)


    def assign_fob(self, info, obj, fob_id, flv_id, fcv_id, replace=True):
        """Pair an inode with an existing FOB, FLV and FCV. FCV can be null for directories and placeholders."""
        with self.db.ensure_transaction():
            if not replace and self.db.query_first('select 1 from inodes where iid=? and fob is not null', obj.iid):
                return
            if D_MDUPDATE: log.debug("Assigning inode %s to FOB (%s, %s, %s)", obj.iid, fob_id, flv_id, fcv_id)
            self.db.update('inodes', 'iid=?', obj.iid, fob=fob_id, fcv=fcv_id, flv=flv_id)
            obj.fob = fob_id
            obj.flv = flv_id
            obj.fcv = fcv_id
            if obj.type == 'd':
                for link in self.db.query('select * from links l join inodes i on l.ino=i.ino where l.parent=? and i.fob is null',  obj.ino):
                    child_obj = self.db.query_first('select * from inodes where ino=?', link.ino)
                    if not child_obj: continue
                    child_info = InodeInfo.from_db(child_obj)
                    self.on_link_to_fob(info, obj, link.name, child_info, child_obj)

    def create_fob(self, parent_fob, name, info, obj, replace=True):
        with self.db.ensure_transaction():
            if not replace and self.db.query_first('select 1 from inodes where iid=? and fob is not null', obj.iid):
                return
            fob_id, flv_id, fcv_id = self.store.create_fob(type=info.type, name=name, parent=parent_fob)
            self.assign_fob(info, obj, fob_id, flv_id, fcv_id)

    def on_link_to_fob(self, parent_info, parent_obj, name, info, obj, old_obj=None):
        assert parent_obj.fob or parent_obj.iid == 'ROOT'
        with self.db.ensure_transaction():
            if not obj.fob and info.type in ('d', 'r'):
                # TODO: from_notify delay
                if self.from_notify and time.time() - obj.btime < self.FOB_CREATE_WAIT:
                    pass
                # Longnames should always point to inodes created by Filoco for existing FOBs.
                # They should come with pre-created inode record in DB associated to a FOB.
                # If there is a longname without iid/FOB, it's a bug.
                if Store.LONGNAME_SEPARATOR in name:
                    log.warning("Longname without FOB: %s/%s (iid %s)", parent_obj.fob, name, obj.iid)
                    return
                self.create_fob(parent_obj.fob, name, info, obj)
            elif obj.fob:
                assert obj.flv
                logical_name = name.split(Store.LONGNAME_SEPARATOR)[0]
                # Renaming a longname object does not propagate. If you want to rename it globally,
                # rename it to a shortname.
                if Store.LONGNAME_SEPARATOR in name: return
                new_flv = self.store.create_flv(fob=obj.fob, parent_fob=parent_obj.fob,
                                                name=logical_name, parent_vers=obj.flv)
                self.db.update('inodes', 'iid=?', obj.iid, flv=new_flv)
                obj.flv = new_flv

    def queue_unscanned(self, action=SR_SCAN):
        #limit = self.scan_queue.maxsize - self.scan_queue.qsize()
        # TRICK: This query efficiently (ab)uses the (scan_state, ino)
        # index: the matching rows form a contiguous segment, which is already
        # sorted by inode number *EVIL GRIN*.
        #for row in self.db.query("select * from inodes where scan_state < ? order by ino limit ?",
        #                            SCAN_UP_TO_DATE, limit):
        for row in self.db.query("select * from inodes where scan_state < ? order by ino",
                                    SCAN_UP_TO_DATE):
            self.push_scan(action, InodeInfo.from_db(self.store, row))

    def process_sr(self, sr):
        if sr.target.fd:
            self.queue_fds -= 1
        if sr.action == SR_CHECK:
            self.check(sr.target)
        elif sr.action == SR_SCAN:
            self.scan(sr.target)
        elif sr.action == SR_SCAN_RECURSIVE:
            self.scan(sr.target, recursive=True)
        else:
            raise NotImplementedError

    async def scan_worker(self):
        """A coroutine that consumes the scan queue and runs scan() appropriately."""
        log.debug("scan_worker started")
        while True:
            with self.db:
                for i in range(500):
                    if self.scan_queue.empty():
                        self.queue_unscanned()
                    if self.scan_queue.empty():
                        break
                    sr = await self.scan_queue.get()
                    if D_QUEUE: log.debug('Popped %r', sr)
                    self.process_sr(sr)
                    # If the queue is long (e.g. during a full rescan), we need to give
                    # the event loop a chance to run.
                if self.scan_queue.empty():
                    break
            await asyncio.sleep(0) # https://github.com/python/asyncio/issues/284
        self.scan_task = None

    def start_scan_worker(self):
        if self.scan_task is None:
            self.scan_task = self.loop.create_task(self.scan_worker())

    def init(self):
        log.debug("init")
        if self.watch_mode == 'fanotify':
            self.init_fanotify()
        # Ensure there is a root record in DB, otherwise recheck would do nothing
        self.store.get_root()
        if self.init_scan == 'all':
            #self.db.update('inodes', "type='d' and scan_state=?", SCAN_UP_TO_DATE, scan_state=SCAN_WANT_RESCAN)
            if self.recursive:
                if self.start_path == Path():
                    self.push_scan(SR_SCAN_RECURSIVE, self.store.get_root()[1])
                else:
                    fd = FD.open(str(self.start_path), os.O_PATH, dir_fd=self.store.root_fd)
                    info = InodeInfo(self.store, fd=fd)
                    row = self.store.find_inode(info)
                    if row is None:
                        raise ValueError("Inode %s is not in Filoco database" % self.start_path)
                    self.push_scan(SR_SCAN_RECURSIVE, info)
            else:
                #self.scan_by_query('1', action=SR_CHECK)
                check_call([FILOCO_LIBDIR/'check_helper', self.store.root_path])
                self.queue_unscanned()
        if self.watch_mode == 'fanotify':
            self.loop.create_task(self.fanotify_worker())

    def main(self):
        self.init()
        if self.watch_mode == 'none':
            if self.scan_task:
                #with self.db: # XXX
                    self.loop.run_until_complete(self.scan_task)
        else:
            self.loop.run_forever()



if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-w', '--watch', nargs=1, choices=['none', 'inotify', 'fanotify'], dest='watch_mode',
                        default='none')
    parser.add_argument('-a', '--all', action='store_const', dest='init_scan', const='all', default=None,
                        help='Rescan all directories, even in watch mode')
    parser.add_argument('-c', '--continue', action='store_const', dest='init_scan', const='pending', default=None,
                        help='Continue an interrupted scan (if there were no intervening changes).')
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Perform a full recursive scan instead of just rechecking directory mtimes. "
                             "Useful if you suspect metadata in filoco database to be incorrect.")
    parser.add_argument('dir')
    opts = parser.parse_args()
    log.debug(opts)
    scanner = Scanner(**opts.__dict__)
    scanner.main()

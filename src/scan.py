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

# Scan state constants
SCAN_NEVER_SCANNED = 0
SCAN_NEEDS_RESCAN = 1
SCAN_WANT_RESCAN = 2
SCAN_UP_TO_DATE = 100

# Filesystem Event Types (`event` column in `fslog` table)
EVENT_CREATE = 1 # new inode came into being
EVENT_LINK = 2
EVENT_UNLINK = 3
EVENT_DELETE = 4 # the inode is really gone
EVENT_MODIFY = 5 # a file's content was modified

init_debug(('queue','scan','mdupdate'))

class InodeInfo:
    __slots__ = ('store', '_fd', 'handle', 'stat', 'ino', 'iid', 'ino', 'type')
    def __init__(self, store, **kw):
        for attr in InodeInfo.__slots__: setattr(self, attr, None)
        self.store = store # TODO: weakref?
        for k,v in kw.items(): setattr(self, k, v)
        if isinstance(self.fd, int): self.fd = FD(self.fd)
    @classmethod
    def from_db(cls, store, row):
        """Create an InodeInfo from a row in the `inodes` table."""
        return cls(store, handle=FileHandle(row.handle_type, row.handle), ino=row.ino, type=row.type, iid=row.iid)

    def release_fd(self):
        """Convert self.fd into a weakref so that we do not keep the FD open too long."""
        if isinstance(self._fd, WeakRef): return
        if self.fd is None: return
        self.get_handle() # because fd might be released unexpectedly, we must keep handle
        self._fd = WeakRef(self._fd)

    def close(self):
        if self.fd is None: return
        self.fd = None

    @property
    def fd(self):
        if isinstance(self._fd, WeakRef):
            fd = self._fd()
            if fd is None: self._fd = None
            return fd
        else:
            return self._fd

    @fd.setter
    def fd(self, fd):
        self._fd = fd

    def get_handle(self): # TODO: ,check=False
        if self.handle:
            return self.handle
        elif self.fd:
            self.handle = name_to_handle_at(self.fd.fd, "", AT_EMPTY_PATH)[0]
            return self.handle
        else:
            raise NotImplementedError # TODO: lookup from db?

    def get_fd(self):
        fd = self.fd
        if fd is None:
            if not self.handle: raise ValueError()
            fd = self._fd = self.store.open_handle(self.handle, os.O_PATH)
        return fd

    def get_stat(self, force=False):
        if force or not self.stat:
            self.stat = st = os.fstat(self.get_fd())
            self.ino = st.st_ino
            self.type = mode2type(st)
        return self.stat

    def clear_stat(self):
        self.stat = None

    def get_ino(self):
        if not self.ino: self.get_stat()
        return self.ino
    def get_type(self):
        if not self.type: self.get_stat()
        return self.type

    def __repr__(self):
        attrs = ['fd', 'handle', 'ino', 'iid']
        args = ', '.join( '%s=%r'%(attr, getattr(self,attr)) for attr in attrs if getattr(self, attr, None) )
        return 'InodeInfo(%s)' % args


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

    def do_delete_inode(self, iid):
        """Delete a given inode from database. Use when sure the original inode no longer exists.
        (e.g. when its handle cannot be opened)"""
        with self.db.ensure_transaction():
            self.db.execute('delete from inodes where iid=?', iid)
            #if self.db.changes():
            #    self.db.insert('fslog', event=EVENT_DELETE, iid=iid)

    def find_inode(self, info, *, is_root=False, create=True):
        handle = info.get_handle()
        ino = info.get_ino()
        ftype = info.get_type()
        with self.db.ensure_transaction():
            obj = self.db.query_first('select * from inodes where ino=?', ino)
            if obj is not None:
                obj_handle = FileHandle(obj.handle_type, obj.handle)
                if obj_handle == handle or self.store.handle_exists(obj_handle):
                    info.iid = obj['iid']
                    return obj
                else:
                    if is_root or obj.iid == 'ROOT':
                        raise RuntimeError("Root replacement not supported")
                    self.do_delete_inode(obj.iid)
            elif create:
                if is_root: iid = 'ROOT'
                else: iid = gen_uuid()
                st = info.get_stat()
                # We can insert safely without any locking. Because we hold an open FD to
                # the inode, it cannot just disappear and thus we are writing correct data.
                self.db.insert('inodes', ino=ino, handle_type=handle[0], handle=handle[1], iid=iid, type=ftype,
                                size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime,
                                btime=st.st_mtime, # btime currently not available b/c of missing statx userspace wrapper
                                scan_state=(SCAN_NEVER_SCANNED if ftype=='d' else SCAN_UP_TO_DATE))
                #self.db.insert('fslog', event=EVENT_CREATE, iid=iid)
                if ftype == 'd':
                    self.push_scan(SR_SCAN, info)
                ret = self.db.query_first('select * from inodes where ino=?', ino)
                info.iid = ret['iid']
                return ret
            else:
                return None

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
            self.delete_inode(info)
            #self.store.delete_object(info) # TODO delete from database
            return
        disk_tuple = (st.st_size, st.st_mtime, st.st_ctime, SCAN_UP_TO_DATE)
        db_tuple = self.db.query_first('select size, mtime, ctime, scan_state from inodes where iid=?', info.iid, _assoc=False)
        if disk_tuple != db_tuple:
            if D_SCAN: log.debug('Change! db tuple: %r, fs tuple: %r', db_tuple, disk_tuple)
            # Probably better to scan now than queue it because the inode is already
            # in cache.
            self.scan(info, fresh_stat=True)

    def scan(self, info, *, obj=None, create=False, fresh_stat=False, recursive=False):
        if obj is None:
            obj = self.find_inode(info, create=create)
            if obj is None: return
        try:
            info.get_stat(force=not fresh_stat)
        except (FileNotFoundError, StaleHandle):
            self.db.execute('delete from inodes where iid=?', obj.iid)
            return
        if info.type == 'd':
            self.scan_dir(info, dirobj=obj, fresh_stat=True, recursive=recursive)

    def scan_dir(self, dirinfo, *, dirobj=None, fresh_stat=False, recursive=False):
        if D_SCAN: log.debug("Scanning %r", dirinfo)
        seen  = set()
        st_start = dirinfo.get_stat(force=not fresh_stat)
        if dirobj is None:
            try: dirobj = self.find_inode(dirinfo)
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

                obj = self.find_inode(info)
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
                self.create_fob(parent_obj.fob, name, info, obj)
            elif obj.fob:
                assert obj.flv
                new_flv = self.store.create_flv(fob=obj.fob, parent_fob=parent_obj.fob,
                                                name=name, parent_vers=obj.flv)
                self.db.update('inodes', 'iid=?', obj.iid, flv=new_flv)
                obj.flv = new_flv

    def delete_inode(self, info):
        log.debug('Deleting inode %r from database', info)
        self.db.execute('delete from inodes where iid=?', info.iid)

    def get_root(self):
        info = InodeInfo(self.store, fd=self.store.root_fd)
        self.find_inode(info, is_root=True)
        return info

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
        self.get_root()
        if self.init_scan == 'all':
            #self.db.update('inodes', "type='d' and scan_state=?", SCAN_UP_TO_DATE, scan_state=SCAN_WANT_RESCAN)
            if self.recursive:
                if self.start_path == Path():
                    self.push_scan(SR_SCAN_RECURSIVE, self.get_root())
                else:
                    fd = FD.open(str(self.start_path), os.O_PATH, dir_fd=self.store.root_fd)
                    info = InodeInfo(self.store, fd=fd)
                    row = self.find_inode(info, create=False)
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

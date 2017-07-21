#!/usr/bin/python3

from utils import *
from store import *
from scan import InodeInfo
from butter.filesystem import *
import resource

import logging
log = logging.getLogger('filoco.mdapply')

class TooMessy(Exception):
    """Too messy data (because of race conditions or complicated conflicts). Don't know what to do."""
    pass

class UpdateTask:
    def __init__(self, fob, flv, *, parent_inode=None, parent_info=None, parent_task=None):
        self.fob = fob
        self.flv = flv
        self.parent_inode = parent_inode
        self.parent_info = parent_info
        self.parent_task = parent_task
        self.src_name = None
        self.src_dirfs = None
        self.new_flv_stamp = -1
        self.new_links = []
        self.rename_to_short = None

    def get_parent_inode(self):
        if self.parent_inode is not None:
            return self.parent_inode, self.parent_info
        elif self.parent_task is not None:
            if self.parent_task.info is None:
                raise RuntimeError("Parent inode was not created")
            return self.parent_task.inode, self.parent_task.info
        else:
            raise RuntimeError("No way to determine parent inode")
            

class MDApply:
    UPDATE_BATCH_SIZE = 1000
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.placeholder_dir = self.store.meta_path / 'placeholder-tmp'
        self.placeholder_dir.mkdir(exist_ok=True)
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        self.UPDATE_BATCH_SIZE = min(self.UPDATE_BATCH_SIZE, hard//4)
        resource.setrlimit(resource.RLIMIT_NOFILE, (4*self.UPDATE_BATCH_SIZE, hard))

    def cleanup_placeholders(self):
        for ph in self.placeholder_dir.iterdir():
            fd = FD.open(ph, os.O_PATH | os.O_NOFOLLOW)
            if ph.is_dir(): ph.rmdir()
            else: ph.unlink()
            info = InodeInfo(self.store, fd=fd)
            self.store.delete_inode(info)
            info.close()

    def get_cur_flv(self, fob_id):
        flvs = list(self.db.query('select * from flvs f join syncables s on f.id=s.id where fob=? and _is_head=1 order by created desc', fob_id))
        assert flvs
        if len(flvs) > 1:
            # TODO: translate parent_fob+name into full path for nicer UI
            log.warn('Name conflict for FOB %s; names (using first):\n%s', fob_id, '\n'.join(
                '%s/%s'%(flv.parent_fob, flv.name) for flv in flvs))
        flv = flvs[0]
        return flv

    def check_inode(self, inode):
        info = InodeInfo.from_db(self.store, inode)
        try:
            info.get_fd()
        except (StaleHandle, FileNotFoundError):
            self.store.delete_inode(info)
            return None
        return info

    def get_fob_inodes(self, fob):
        inodes = list(self.db.query('select * from inodes where fob=?', fob))
        ret = []
        for inode in inodes:
            info = self.check_inode(inode)
            if info: ret.append((inode,info))
        return ret

    def get_fob_single_inode(self, fob):
        inodes = self.get_fob_inodes(fob)
        if not inodes:
            return None, None
        elif len(inodes) == 1:
            return inodes[0]
        else:
            raise TooMessy("More inodes for %s FOB %s: %r, not sure what to do. Remove one of them and run full rescan."
                    % {'d': 'directory', 'r': 'file'}[fob.type], fob.fob, inodes)

    def get_pigeonhole_conflicts(self, flv):
        """Get a list of FLV that are pigeonhole-conflicting with this one.
        (i.e., different FOBs claiming the same name)."""
        return list(self.db.query("select * from flvs where _is_head=1 and parent_fob=? and name=? and fob!=?",
                        flv.parent_fob, flv.name, flv.fob))

    def extend_update_batch(self, fobs):
        """Take an existing update set and add all FOBs that have to be update together.

        This includes (1) all missing parents, (2) other FOBs participating in rename cycles.
        Call from within a locked transaction."""

        adding = set()
        by_fob = {}
        ret = []

        def add_fob(fob, needed_for=None, needed_role=None, ignore_cycle=False):
            if isinstance(fob, bytes):
                fob_id = fob
                if fob_id in by_fob: return by_fob[fob_id]
                fob = self.db.query_first('select rowid,* from fobs where id=?', fob_id)
                if not fob: raise TooMessy("Missing dependent FOB %s needed for %s as %s." % (fob_id, needed_for.id, needed_role))
            if fob.id in by_fob: return by_fob[fob.id]
            if fob.id in adding:
                if ignore_cycle: return
                else: raise TooMessy("Dependency cycle in FOB %s needed for %s as %s." % (fob_id, needed_for.id, needed_role))
            adding.add(fob.id)
            flv = self.get_cur_flv(fob.id)
            if flv.parent_fob is None:
                parent_inode, parent_info = self.store.get_root()
            else:
                parent_inode, parent_info = self.get_fob_single_inode(flv.parent_fob)
            if parent_inode is None:
                parent_task = add_fob(flv.parent_fob, needed_for=fob.id, needed_role='parent')
            else:
                parent_task = None
                try:
                    # Look for an existing inode in the target location (the current
                    # pigeon occupying the hole). If there is one, we have to add
                    # the corresponding FOB to the update batch because it could be
                    # participating in a rename chain or cycle.
                    pigeon_fd = FD.open(flv.name, os.O_PATH|os.O_NOFOLLOW, dir_fd=parent_info.fd)
                    pigeon_info = InodeInfo(self.store, fd=pigeon_fd)
                    pigeon_inode = self.store.find_inode(pigeon_info)
                    if pigeon_inode and pigeon_inode.fob:
                        try: add_fob(pigeon_inode.fob, needed_for=fob.id, needed_role='current pigeon', ignore_cycle=True)
                        except TooMessy as e:
                            # We can ignore this, we'll save new inode as longname
                            log.warn("%s", str(e))
                except FileNotFoundError:
                    pass
                except OSError as e:
                    raise TooMessy("Error querying for exising inode in target location (%s, %s): %s"%(flv.parent_fob, flv.name, str(e)))

            task = UpdateTask(fob, flv, parent_inode=parent_inode, parent_info=parent_info, parent_task=parent_task)
            task.new_flv_stamp = fob._new_flvs
            by_fob[fob.id] = task
            ret.append(task)
            return task

        for fob in fobs:
            try:
                add_fob(fob)
            except TooMessy as exc:
                log.error("Cannot update FOB %s because of filesystem/metadata mess: %s", binhex(fob.id), str(exc))

        return ret

    def collect_update_batch(self, start, force=False):
        with self.db.ensure_transaction():
            self.db.lock_now()
            if force: cond = ''
            else: cond = '_new_flvs>0 and '
            fobs = list(self.db.query('select rowid,* from fobs where %s rowid>=? '
                                        'order by rowid asc limit ?' % cond,
                            start, self.UPDATE_BATCH_SIZE))
            return self.extend_update_batch(fobs)

    def create_new_inodes(self, batch):
        for task in batch:
            fob = task.fob
            flv = task.flv
            inode, info = self.get_fob_single_inode(fob.id)
            if info is None:
                tmp_name = 'filoco-mdapply-placeholder-%s' % (binhex(fob.id))
                tmp_path = str(self.placeholder_dir / tmp_name)

                try:
                    # We do not have data checked out, create placeholder inode
                    if fob.type == 'd':
                        os.mkdir(tmp_path)
                    elif fob.type == 'r':
                        os.symlink(Store.PLACEHOLDER_TARGET, tmp_path)
                    else:
                        log.error("Unknown FOB type %r. Ignoring.", fob.type)
                        continue
                except FileExistsError:
                    # There might be a placeholder from an earlier interrupted mdapply.
                    pass
                new_fd = FD.open(tmp_path, os.O_PATH|os.O_NOFOLLOW)
                new_info = InodeInfo(self.store, fd=new_fd)
                new_inode, _ = self.store.find_or_create_inode(new_info, fob=fob.id, flv=flv.id, fcv=None)
                # We are running as root because of fhandle permissions, chown to correct user.
                os.lchown(tmp_path, *self.store.owner)
                task.src_dfd = AT_FDCWD
                task.src_name = tmp_path
                task.inode = new_inode
                task.info = new_info

    def rename_to_longname(self, src_dfd, src_name, dst_dfd, dst_name, fob, *, try_short=False):
        for idx in range(0 if try_short else 1, 1000):
            target_name = "%s.FL-%s-%s" % (dst_name, binhex(fob), idx) if idx else dst_name
            try: renameat2(+src_dfd, src_name, +dst_dfd, target_name, RENAME_NOREPLACE)
            except FileExistsError: continue
            else: return target_name
        raise FileExistsError()

    def rename_and_update_links(self, src_info, src_name, dst_info, dst_name, *,
                                flags=0, longname=False, fob=None, try_short=False,
                                inode=None):
        """Rename a directory entry and update the `links` table accordingly."""
        if isinstance(src_info, (FD, int)):  src_fd = src_info
        else: src_fd = src_info.get_fd()
        dst_fd = dst_info.get_fd()
        if longname:
            target_name = self.rename_to_longname(src_fd, src_name, dst_fd, dst_name,
                                                    fob=fob, try_short=try_short)
        else:
            renameat2(src_fd, src_name, dst_fd, dst_name, flags)
            target_name = dst_name
        # If the links table was not up to date, we might have renamed a different inode.
        # This does not matter because on next scan, we would consider the different
        # inode under the target name to be a replace a handle it accordingly (preserve
        # FOB), the same as if we first rescanned to fix links (which would detect the
        # same inode replace, only under the original name) and then called this function.
        # We simply perform the same rename in both filesystem and model, even though
        # they can affect different inodes.
        if isinstance(src_info, InodeInfo):
            self.db.execute('update or replace links set parent=?, name=? where parent=? and name=?',
                dst_info.get_ino(), target_name, src_info.get_ino(), src_name)
        # If the original link is missing or this is a new inode, we have to create
        # a fresh link.
        if inode and not (isinstance(src_info, InodeInfo) or self.db.changes()):
            self.db.execute('insert into links (parent,name,ino) values (?,?,?)',
                    dst_info.get_ino(), target_name, inode.ino)
        return target_name

    def get_good_links(self, fob):
        inodes = self.get_fob_inodes(fob.id)
        # This part is tricky: we want to find all links to all inodes tied to this FOB.
        # There will usually be only one inode (exept for conflicts) and one link (except
        # for race conditions, incomplete scans and other very unusual circumstances).
        # But we should deal with those.
        good_links = []
        num_shorts = 0
        for inode, info in inodes:
            ino = info.get_ino()
            links = list(self.db.query('select rowid,* from links where ino=?', inode.ino))
            for link in links:
                parent_inode = self.db.query_first('select * from inodes where ino=?', link.parent)
                parent_info = self.check_inode(parent_inode)
                if parent_info is None: continue
                try: check_fd = FD.open(link.name, os.O_PATH|os.O_NOFOLLOW, dir_fd=parent_info.fd)
                except FileNotFoundError: continue
                was_short = not Store.is_longname(link.name)
                rec = AttrDict(parent_inode=parent_inode, parent_info=parent_info, name=link.name,
                                inode=inode, info=info, short_cand=False, was_short=was_short)
                if not Store.is_longname(link.name):
                    num_shorts += 1
                good_links.append(rec)
        for glink in good_links:
            glink.short_cand = len(good_links) == 1 or (glink.was_short and num_shorts == 1)
        return good_links

    def move_to_longnames(self, batch):
        for task in batch:
            fob = task.fob
            target_inode, target_info = task.get_parent_inode()
            logical_name = task.flv.name
            if target_info is None:
                log.warning("Target inode not found for FOB %s. Skipping.", binhex(fob.id))
                continue

            conflicts = self.get_pigeonhole_conflicts(task.flv)
            if conflicts:
                log.info("Location %s/%s has a pigeonhole conflict. Keeping all files with longnames.",
                        task.flv.parent_fob, task.flv.name)

            if task.src_name:
                try_short = (not conflicts)
                target_name = self.rename_and_update_links(task.src_dfd, task.src_name, target_info,
                        logical_name, fob=fob.id, longname=True, try_short=True,
                        inode=task.inode)
                if Store.is_longname(target_name):
                    task.rename_to_short = (target_info, target_name)
            else:
                good_links = self.get_good_links(fob)
                if not good_links:
                    log.warning("No good links found for FOB %s, not renaming. "
                                "Please rescan and run mdapply again.", binhex(fob.id))
                    continue
                for glink in good_links:
                    if glink.parent_inode.iid == target_inode.iid and glink.name == logical_name:
                        continue
                    try_short = (not conflicts) and (glink.short_cand)
                    target_name = self.rename_and_update_links(glink.parent_info, glink.name,
                            target_info, logical_name, fob=fob.id, longname=True, try_short=try_short,
                            inode=glink.inode)
                    if try_short and Store.is_longname(target_name):
                        task.rename_to_short = (target_info, target_name)

    def move_to_shortnames(self, batch):
        for task in batch:
            if task.rename_to_short:
                parent_info, name = task.rename_to_short
                short_name = name.split(Store.LONGNAME_SEPARATOR)[0]
                try:
                    self.rename_and_update_links(parent_info, name, parent_info, short_name,
                            flags=RENAME_NOREPLACE)
                except FileExistsError:
                    log.warning("Cannot rename %s/%s to shortname, something is in the way.",
                                    binhex(task.flv.parent_fob), name)

    def mark_as_updated(self, batch):
        for task in batch:
            # We use stamp versioning to prevent races when clearing _new_flv stamps.
            self.db.execute('update fobs set _new_flvs=0 where id=? and _new_flvs=?',
                    task.fob.id, task.new_flv_stamp)

    def perform_one_batch(self, start, *, force=False):
        with self.db.ensure_transaction():
            self.db.lock_now()
            batch = self.collect_update_batch(start, force=force)
            if not batch: return None
            self.cleanup_placeholders()
            self.create_new_inodes(batch)
        # Synchronize all changes to disk. This is done before moving new inodes
        # because otherwise in the event of a power failure a future scan might
        # find the new inodes without a FOB associated and create a new FOB for
        # them, leading to conflicts.
        self.db.execute("PRAGMA wal_checkpoint(FULL)")
        with self.db.ensure_transaction():
            self.db.lock_now()
            # First, move all inodes to unique longnames to break rename cycles.
            self.move_to_longnames(batch)
            # Then, whenever possible, move back to a corresponding shortname (unless
            # there is a conflict or an intervening untracked inode).
            self.move_to_shortnames(batch)

            # Make sure all metadata changes go to disk before marking them as done
            # (otherwise we won'T retry renames after power failure)
            syncfs(+self.store.root_fd)
            self.mark_as_updated(batch)

        return batch[-1].fob.rowid


    def run(self, *, force=False):
        start = 0
        while True:
            log.debug("Running batch, start = %d", start)
            end = self.perform_one_batch(start, force=force)
            # We use destructors to automatically close FDs but Python's refcounting apparently
            # is not that reliable. Force a GC to free up unused FDs.
            import gc; gc.collect()
            if end is None: break
            start = end + 1




def main(store, *, force : ('f') = False):
    st, sub = Store.find(store)
    if sub != Path(): raise ArgumentError("Metadata apply must be done on whole store (%s), not a subtree." % st.root_path)
    mdapply = MDApply(st)
    mdapply.run(force=force)

run(main)

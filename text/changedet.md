Change Detection
================

The purpose of a file synchronization tool is simple: whenever a change to the
synchronized tree is made in one replica, transfer the change to the other
replicas and apply it there. From this stems a natural need for a way of
detecting filesystem changes efficiently.

There are two broad categories of filesystem change detection methods.

**Offline change detection** consists of actively comparing the filesystem
state to a previous state. The detection must be explicitly initiated by the
application at an arbitrarily chosen time, e.g. regulary (every day at midnight)
or upon user request. It can be considered a form of active polling.

But polling is the lesser evil here. The real problem is that the comparison
process usually involves recursively scanning the entire directory tree (or a
subtree), saving the results and comparing them with the previous scan. This can
be quite slow on larger trees, wherefore it cannot be done very often, leading
to an increase in change detection latency.

**Online change detection**, on the other hand, relies on specific
operating system features that allow applications to be notified of filesystem
changes immediately as they happen. Instead of polling, the application
just passively waits for change notifications. However, the notification systems
often have many limitations, issues and idiosyncrasies that will be discussed
in the subsequent sections. For example they fail to report some kinds of
operations (e.g. renames) or operations done in specific ways (e.g. writes
to a memory-mapped file).

Even with a perfect notification system, we face a serious issue. The
application monitoring the notifications must be running at all times.
Notifications of filesystem changes made when the application is not running
will be missed and forever lost to the application.

Due to both these issues, the application's idea about the state of the
filesystem can diverge from reality over time. The only way of fixing this is
with a full rescan of the directory tree. Thus while being efficient, online
change detection is usually not very robust. In contrast, offline change
detection is by definition 100% reliable, because it looks at the actual current
state of the file system and updates internal structures accordingly.

There also emerges an interesting middle ground between these two extremes,
which we shall dub **filesystem-based change detection**.  Some filesystems
offer an explicit *compare* operation that is more efficient than scanning the
whole trees. This is usually done in one of two ways.

One is to store file metadata on disk in data structures that allow for
efficient comparison. For example, btrfs<!--TODO link--> uses persistent B-trees
for its snapshots that allows comparing two snapshots in logarithmic time.  <!--
TODO Check, cite. --> The other way is to store an explicit change log as a part
of the on-disk filesystem representation. This requires more space but allows
answering change queries in time linear in the number of changes.

This last category seems to offer the best of both worlds: we get reliably and
efficiently informed of all changes. Often the comparison operation is fast
enough to be run e.g. every minute, effectively replacing online detection. The
obvious disadvantages are that most filesystems do not support such operations
and the need for a solution specifically tailored to each filesystem that does
support change detection (there is no generic API, at least on Linux).

## Offline Change Detection

### A Linux VFS Primer

### Identifying Inodes

#### Enter Filehandles

## Online Change Detection

### `inotify`

### `fanotify`

### The `FAN_MODIFY_DIR` Kernel Patch

### Other Methods

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
often have many limitations, issues and idiosyncrasies. For example they fail
to report some kinds of operations (e.g. renames) or operations done in specific
ways (e.g. writes to a file via a memory mapping).

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

The next sections will survey various ways of doing each kind of change detection
on Linux.

Offline Change Detection
------------------------

### The Anatomy of Linux Filesystems

Before diving into change detection, we have to understand a bit about the structure
of Linux filesystems and filesystem APIs. If terms like *inode*, *hardlink*,
and *file descriptor* are familiar to you, you can safely skip this section.
Most of what is being said here applies to all Unix-based operating systems,
however, some details might be specific to Linux.


<!-- Describe VFS and mounts first? -->
#### Inodes and Links

The basic unit of a Linux filesystem is an **inode**. An inode represents one filesystem
object: e.g. a file, a directory, or a symbolic link. There are a few more esoteric inode
types, which we shall mostly ignore (so-called *special files*: sockets, named pipes and
device nodes).

The inode serves as a logical identifier for the given filesystem object. It also holds
most of its metadata: size, permissions, last modification time. However, **an inode does
not store its own name.**

The names are instead stored in the parent directory inode. The content of
a directory inode can be tought of as a mapping from names to inodes of its direct
children. The elements of this mapping are called *directory entries*.

This implies that an inode can have multiple names if multiple directory entries
reference the same inode. These names are usually called *hardlinks* or simply
*links* to the given inode.

However, for practical reasons, multiple hardlinks
to a directory are not allowed. Thus while the filesystem structure is a DAG rather
than a tree, directories form a proper tree. Also, unlike all other kinds of inodes,
directories store a reference to their parent (as a special directory entry called
"`..`").

This explains many otherwise perplexing (especially for newcomers to the Unix world)
facts:

  * The syscall to delete a file is called `unlink`. It does not in fact delete
    a file (inode), but merely removes one link to it. Only when all links to
    an inode are removed, it is deleted.
  * It is possible to delete a file that is opened by a process. That process
    can happily continue using the file. This is because and opened file also
    counts as a "reference" to the inode. Only when all references to the inode
    (both links and open files) are gone, the inode is physically deleted and
    its space freed.
  * To rename or delete a file, you do not need write permissions to that file, only
    to the parent directory. These operations do not touch the file inode
    at all, they change only the parent directory inode (by adding/removing directory
    entries).
  * Renaming a file updates the last modification time of the parent directory, not
    the file, for the same reason.

\noindent
The term *inode* is actually a little overloaded. It can mean at least three related
but distinct things:

  * A purely logical conept that helps us to talk about filesystem structure and behaviour.
  * A kernel in-memory structure (`struct inode`) that identifies a file object and
    holds its metadata. These structures are kept in memory as a part of the *inode cache*
    to speed up file access.
  * An filesystem-specific on-disk structure used to hold file object metadata and usually
    also information about the location of the file's data blocks on the disk. However,
    some filesystems do not internally have any concept of inodes, especially non-Unix
    filesystem like FAT.

Each inode (in all the three senses) has a unique identifier called the **inode number**
(*ino* for short). In traditional Linux filesystems like `ext2`, the inode number directly
corresponds to the physical location of the inode structure (in the third sense) on disk. Thus
when an inode is deleted, its number may be later reused when another inode occupies the
same space. That this happens quite commonly can be shown by this simple experiment on an
ext2/3/4 filesystem:

    $ echo "first file" >first
    $ ls -i
    12 first
    $ rm first
    $ echo "second file" >second
    $ ls -i
    12 second

Both files got inode number 12 despite being completely unrelated.
In other filesystems (e.g. `btrfs`), the inode number is simply a sequentially
assigned identifier and numbers are not reused until necessary (usually never, because
inode numbers can be 64-bit so overflow is unlikely).

#### Filesystem Access Syscalls

#### Idioms

### 

### Identifying Inodes

#### Enter Filehandles

## Online Change Detection

### `inotify`

### `fanotify`

### The `FAN_MODIFY_DIR` Kernel Patch

### Other Methods

## Filesystem-Based Change Detection

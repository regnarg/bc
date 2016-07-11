Change Detection
================

The purpose of a file synchronization tool is simple: whenever a change to the
synchronized tree is made in one replica, transfer the change to the other
replicas and apply it there. From this stems a natural need for a way of
detecting filesystem changes efficiently.

There are two broad categories of filesystem change detection methods.

\D{Offline change detection} consists of actively comparing the filesystem
state to a previous state. The detection must be explicitly initiated by the
application at an arbitrarily chosen time, e.g. regulary (every day at midnight)
or upon user request. It can be considered a form of active polling.

But polling is the lesser evil here. The real problem is that the comparison
process usually involves recursively scanning the entire directory tree (or a
subtree), saving the results and comparing them with the previous scan. This can
be quite slow on larger trees, wherefore it cannot be done very often, leading
to an increase in change detection latency.

\D{Online change detection}, on the other hand, relies on specific
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
which we shall dub \D{filesystem-based change detection}.  Some filesystems
offer an explicit *compare* operation that is more efficient than scanning the
whole trees. This is usually done in one of two ways.

One is to store file metadata on disk in data structures that allow for
efficient comparison. For example, btrfs uses persistent B-trees
for its snapshots that allows comparing two snapshots in logarithmic time.
\TODO{Check, cite.} The other way is to store an explicit change log as a part
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

The basic unit of a Linux filesystem is an \D{inode}. An inode represents one filesystem
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

  * \noindent{}**Perplexing fact:** The syscall used to delete a file is called `unlink`.\
    **Explanation:** It does not in fact delete
    a file (inode), but merely removes one link to it. Only when all links to
    an inode are removed, it is deleted.

  * \noindent{}**Perplexing fact:** It is possible to delete a file that is opened by
    a process. That process can happily continue using the file.\
    **Explanation:** Inodes in kernel are reference counted. Only when all in-kernel
    references to the inode are gone *and* the inode has no links, it is physically
    deleted.

  * \noindent{}**Perplexing fact:** To rename or delete a file, you do not need write
    permissions (or in fact, any permissions) to that file, only to the parent directory.\
    **Explanation:** These operations do not touch the file inode
    at all, they change only the parent directory inode (by adding/removing directory
    entries).

  * \noindent{}**Perplexing fact:** Renaming a file updates the last modification time
    of the parent directory, not the file.\
    **Explanation:** Same as above.


\noindent
We should also clarify that the term *inode* is actually a little overloaded. It can mean
at least three related but distinct things:

  * A purely logical conept that helps us to talk about filesystem structure and behaviour.
  * A kernel in-memory structure (`struct inode`) that identifies a file object and
    holds its metadata. These structures are kept in memory as a part of the *inode cache*
    to speed up file access.
  * An filesystem-specific on-disk structure used to hold file object metadata and usually
    also information about the location of the file's data blocks on the disk. However,
    some filesystems do not internally have any concept of inodes, especially non-Unix
    filesystem like FAT.

Each inode (in all the three senses) has a unique identifier called the \D{inode number}
(*ino* for short) that can be read from userspace.

#### Filesystem Access Syscalls

Most filesystem syscalls take string paths as an argument. The inode corresponding to the
path is found in the kernel using a process called \D{path resolution}.
\TODO{link manpage}
The kernel starts at the root inode and for each component of the path walks down the
corresponding directory entry. This process is inherently non-atomic and if files are
renamed during path resolution, you might get unexpected results.

The most important syscalls include:

  * `lstat`(*path*): resolve *path* into an inode and return a structure containing its
    metadata. Among other things, it contains: type (file/directory/etc.), size, last
    modification time and inode number.
  * `unlink`(`"`*dir*`/`*name*`"`): resolve *dir* into an inode, which has to be an existing
    directory, and remove the directory entry *name* from it. *name* cannot be a directory.
  * `rmdir`(`"`*dir*`/`*name*`"`): like `unlink` but removes a directory, which must be empty.
  * `mkdir`(`"`*dir*`/`*name*`"`): create a new directory inode and link it to *dir*
    as *name*.
  * `link`(*orig-path*, `"`*new-dir*`/`*new-name*`"`)
  * `rename`(`"`*orig-dir*`/`*orig-name*`"`, `"`*new-dir*`/`*new-name*`"`): resolve
    *orig-dir* and *new-dir* to inodes. Then perform the following atomically: remove the
    *orig-name* directory entry from *orig-dir* and create a new *new-name* directory entry
    in *new-dir* that refers to the same inode as *orig-name* did. If there was already
    a *new-name* entry in *new-dir*, replace it atomically (such that there is not gap
    during the rename when *new-name* does not exist).

When desiring to access the *content* of inodes (e.g. read/write a file or list a directory),
you must first *open* the inode with an `open`(*path*, *flags*) syscall. `open` resolves
*path* into an inode and creates an \D{open file description} (OFD, `struct file`) structure
in the kernel, which holds information about the open file like the current seek poisition
or whether it was opened read only. The OFD is tied to the *inode* so that it points always
to the same inode even if the file is renamed or unlinked while it is opened.

The application
gets returned a \D{file descriptor} that is used to refer to the OFD in all subsequent
operations on the opened file. The most common operations are `read`, `write` and `close`,
with the obvious meanings, and `fstat`, which does a `lstat` on the file's inode without
any path resolution.

Apart from listing directory contents, directory file descriptors can be used as anchors
for path resolution. To this end, Linux offers so-called *at* syscalls (`openat`, `renameat`,
etc.), that instead of one path argument take two arguments: a directory file descriptor
and a path *relative to that directory*. Such syscalls start path resolution not at the root
but at the inode referenced by the file descriptor. Thus userspace applications can use
directory file descriptors as "pointers to inodes". This will later prove crucial
in elliminating many race conditions.

### Scanning Directory Trees

<!-- rename races -->
<!-- inode-sort optimizaton -->

### Identifying Inodes

#### Inode Numbers

\TODO{connect to preceding text when there is any}

For correctly detecting renames, we need to determine that an inode we are currently
exploring is the same as an inode we have previously seen. The first thing that naturally
comes to mind is to use inode numbers. But inode numbers can be reused when an inode
is deleted and a new one is later created.

This typically happens on filesystems where inode numbers correspond to on-disk locations.
When a new inode is created in the same space, it gets the same number. This happens farily
often, for example this simple experiment quite reliably reproduces inode number reuse
on ext2/3/4 filesystems:

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

Thus at least on some filesystems, including ext2/3/4, the most common filesystem in the
Linux world, inode numbers cannot be used to reliably match inodes between offline scans.
Is there a better way?

#### Enter Filehandles

There is an alternative way of identifying inodes, created originally for the
purposes of the Network File System (NFS) protocol. NFS was designed to preserve
the usual Unix filesystem semantics (e.g. that a file can be renamed while open)
over the network. It was also designed to be *stateless* on the server side.
This entails that a client should survive not only reconnection after a network
outage but even a full reboot of the server noticing nothing but a delay.

For example, the client must be able to continue using files opened before the
reboot as if nothing happened. Even if the files were renamed in-between.
Extreme case: open file on client, disconnect network, reboot server, rename the
file on server, reboot server, reconnect network, client can continue using the
renamed file.

To accomplish this, the concept of \D{file handles} was created. A file handle
is simply a binary string identifying an inode. But unlike inode numbers, a file
handle can never be resused to refer to a different inode. When a client tries
to use a handle referring to an inode that has been deleted, the server must be
able to detect that and return a "stale handle" (`ESTALE`) error.

A file handle should be treated simply as an opaque identfier, its structure
depends on the filesystem used on the server side. Most filesystems create
file handles composed of the inode number and a so-called \D{generation number},
which is increased every time an inode number is reused. Such pair should
be unique for the lifetime of the filesystem.

Not all filesystems support file handles. Those that do are called
\D{exportable} (*exporting* is the traditional term for sharing a filesystem
over NFS). Most common local filesystems (e.g. ext2/3/4, btrfs, even non-Unix
filesystems like NTFS) are exportable. On the other hand, NFS itself, for
example, is not.

File handles are usually used by the in-kernel NFS server. But they can also be
accessed from userspace using two simple syscalls: `name_to_handle_at` returns
the handle corresponding to a path or file descriptor.  `open_by_handle_at`
\TODO{link manpage} finds the inode corresponding to the handle, if it still
exists, and returns a file descriptor referring to it. If the inode no longer
exists, the `ESTALE` error is reported. These syscalls were created to
facilitate implementation of userspace NFS servers. We shall (ab)use them in
rather unusual ways.

Being non-reusable, file handles seem like a good candidate for persistent inode
identifiers. However, there is a different problem. The NFS specification does
not guarantee that the same handle for a given inode every time. \TODO{citation}
I.e., it is possible for multiple different handles to refer to the same inode,
which prevents us from simply comparing handles as strings or using them as
lookup keys in internal databases.

#### The Best of Both Worlds

We propose a reliable inode identification scheme that combines the strengths
of both inode numbers (stability) and file handles (non-reusability). It works
as follows: for every known inode, we store both its inode number and a file
handle referring to it in our internal database, with inode number usable as
a lookup key.

Whenever we encounter an inode during a scan, we look up its inode number in
our database. If a record is found, we fetch the stored handle and try to
open it with `open_by_handle_at`. If that succeeds, the original inode still
exists and thus its inode number has not been reused. At this point, we can
be sure that the inode we encountered during scan correspond the the record
just found in our database. If we found it at a different path than last time,
we can record this as a rename.

On the other hand, if we get an `ESTALE` error, we know that the original inode
has been deleted and thus we can remove it from our database. We can then
proceed with inserting a new record with a new handle for the inode encountered.

Storing file handles has other benefits, too. For example the stored handle
allows us to open the inode corresponding to an internal record in our database
at any time (e.g. when synchronizing file data) free from the race conditions
of path resolution.

We have solved the inode identification problem for two broad classes of
filesystems: exportable filesystems and filesystems that do not reuse inode
numbers. This covers most common file systems that a Linux user encounters,
with the exception of (client-side) NFS. That is rather unfortunate as it is
common practice for users to have NFS-mounted home directories in schools
and larger organizations. This issue should certainly be given attention
in further works but it seems likely that it will require kernel changes.

## Online Change Detection

### `inotify`

### `fanotify`

### The `FAN_MODIFY_DIR` Kernel Patch

### Other Methods

## Filesystem-Based Change Detection

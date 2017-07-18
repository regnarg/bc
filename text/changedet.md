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
state of the file system and updates internal structures accordingly. Actually,
that is true only if the filesystem is not changed during the scan, as we shall
see later.

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
*file descriptor*, and `openat` are familiar to you, you can safely skip this section.
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

The names are instead stored in the parent directory. The content of
a directory can be tought of as a mapping from names to inodes of its direct
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

\newcommand{\pfact}[2]{\noindent \textbf{Perplexing fact:} #1\\\textbf{Explanation:} #2}

  * \pfact{The syscall used to delete a file is called `unlink`.}
    {It does not in fact delete
    a file (inode), but merely removes one link to it. Only when all links to
    an inode are removed, it is deleted.}

  * \pfact{It is possible to delete a file that is opened by
    a process. That process can happily continue using the file.}
    {Inodes in kernel are reference counted. Only when all in-kernel
    references to the inode are gone \textit{and} the inode has no links, it is physically
    deleted.}

  * \pfact{To rename or delete a file, you do not need write
    permissions (or in fact, any permissions) to that file, only to the parent directory.}
    {These operations do not touch the file inode
    at all, they change only the parent directory contents (by adding/removing directory
    entries).}

  * \pfact{Renaming a file updates the last modification time
    of the parent directory, not the file.}
    {Same as above.}


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
  * `rename`(`"`*orig-dir*`/`*orig-name*`"`, `"`*new-dir*`/`*new-name*`"`): resolve
    *orig-dir* and *new-dir* to inodes. Then perform the following atomically: remove the
    *orig-name* directory entry from *orig-dir* and create a new *new-name* directory entry
    in *new-dir* that refers to the same inode as *orig-name* did. If there was already
    a *new-name* entry in *new-dir*, replace it atomically (such that there is not gap
    during the rename when *new-name* does not exist).
  * `link`(*orig-path*, `"`*new-dir*`/`*new-name*`"`): create a new hardlink to
    an existing inode. Unlike `rename`, this does not allow overwriting the
    target name if it already exists.

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
for path resolution. To this end, Linux offers so-called \D{at-syscalls} (`openat`,
`renameat`,
etc.), that instead of one path argument take two arguments: a directory file descriptor
and a path *relative to that directory*. Such syscalls start path resolution not at the root
but at the inode referenced by the file descriptor. Thus userspace applications can use
directory file descriptors as "pointers to inodes". This will later prove crucial
in elliminating many race conditions.

### Change Detection in a Single File

Let's start off with something trivial: detecting changes in a single file.
First we need to decide what to store as internal state. Against that internal
state we shall be comparing the file upon the next scan. One option is to store
a checksum (e.g. MD5) of the file's content. However, this makes scans rather
slow, as they have to read the complete content of each file.  This is
unfortunate as today's file collections often contain many large files that
rarely ever change (e.g. audio and video files).

A more viable alternative takes inspiration from the famous `rsync` \TODO{link}
file transfer program. It consists of storing the size and last modification
time (*mtime*) of each file and comparing those. This may be unreliable for
several reasons:

  * It is possible to change mtime from userspace (possibly to an earlier value)
    and some applications do so.
  * mtime might not be updated if a power failure happens during write.
  * mtime updates might be delayed for writes made via a memory mapping.
  * Many filesystems store mtimes with second granularity. This means that if
    the file was updated after we scanned it but in the same second, we wouldn't
    notice it during next scan.

Most of these problems should be fairly unlikely or infrequent and the massive
success of `rsync` attests that this approach is good enough for most practical
uses.

Moreover, size and mtime can be acquired atomically while computing checksums
might give inconsistent results if the file is being concurrently updated.
We can still store checksums for consistency checking purposes  but it is
sufficient to update them only when the (size, mtime) tuple changes and we
will have to deal with the race conditions. This will be discussed later.

### Scanning a Single Directory                     {#sec:singledir}

For a single directory, we can simply store a mapping from names to (size, mtime)
tuples as the state.

To read a directory, an application calls the `getdents` syscall (usually
through the `readdir` wrapper from the standard C library), passing it a
directory file dectriptor and a buffer. The kernel fills the buffer with
directory entries (each consisting of a name, inode number and usually
the type of the inode). When the contents of the directory do not fit into
the buffer, subsequent calls return additional entries.

We can hit a race condition in several places when entries in the directory are
renamed during scanning:

  * Between two calls to `getdents`. The directory inode is locked for the
    duration of the `getdents` so everything returned by one call should
    be consistent. However, a rename may happen between two `getdents`
    calls. In that case, it is not defined whether we will see the old name,
    the new name, both or neither.
    \TODO{Cite http://yarchive.net/comp/linux/readdir\_nonatomicity.html
    or a more direct LKML archive}
    The last case is particulary unpleasant because we might mistakenly mark
    a renamed file as deleted.

    This can be mitigated by using a buffer large enough to hold all the
    directory entries. This could be achieved for example by doubling the
    buffer size until we managed to read everything in one go. However,
    trying to do this for large directories could keep the inode locked
    for unnecesary long. A better solution will be proposed later using
    a combination of online and offline change detection.

  * Between `getdents` and `lstat` (or similar). Because `getdents` returns
    only limited information about a file, we need to call `lstat` for each
    entry to find size and mtime. Between those to calls, the entry might
    get renamed (causing `lstat` to fail) or replaced (causing it to return
    a different inode). Both cases can be detected (the latter by comparing
    inode number from `lstat` with inode number from `getdents`) and the
    scan can be retried after a random delay.

There is one other problem: when a file is renamed, it would be detected as
deletion of the original file and creation of a new on with the same content (or
just similar, if it was both renamed and changed between scans). Unless the data
synchonization algorithm can reuse blocks from other files for delta transfers,
this would force retransmission of the whole file.

To correctly detect renames, we would need a way to detect that a name we
currently encountered during the scan refers an inode that we know from earlier
scans, perhaps under a different name. For this to be possible, we need to be
able to assign some kind of unique identifiers to inodes that are stable,
non-reusable and independent of their names.

### Identifying Inodes

#### Inode Numbers

The first natural candidate for an inode identifier is of course the inode
number. But inode numbers can be reused when an inode is deleted and a new one
is later created. This happens farily often, for example this simple experiment
quite reliably reproduces inode number reuse on an otherwise quiet ext4
filesystem:

    $ echo "first file" >first
    $ ls -i
    12 first
    $ rm first
    $ echo "second file" >second
    $ ls -i
    12 second

Both files got inode number 12 despite being completely unrelated.
In other filesystems (e.g. btrfs), the inode number is simply a sequentially
assigned identifier and numbers are not reused until necessary (usually never, because
inode numbers can be 64-bit so overflow is unlikely).

Thus at least on some filesystems, including ext4, one of the most common
filesystems in the Linux world, inode numbers cannot be used to reliably match
inodes between offline scans.  Is there a better way?

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
over NFS). Most common local filesystems (e.g. ext4, btrfs, even non-Unix
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
be sure that the inode we encountered during scan corresponds to the record
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

### Scanning a Directory Tree                               {#sec:dirtree}

#### Internal state

When scanning directory trees, we definitely do not want to store the full path
to each object. If we did and a large directory was renamed, we would need to
individually update the path of every file in its subtree\dots and probably
transfer all those updates during synchronization, unless additional tricks are
used.

Instead, we will choose a tree-like representation that closely mimics the
underlying filesystem structure. The internal state preserved between scans
consists of:

  * A list of inodes, each storing:
      - A so-called \D{IID}, a random unique identifier assigned upon first
        seeing this inode.
      - Inode number and filehandle as dicussed above, with fast lookups
        by inode number possible.
      - Last modification time, for files also size.
  * For every directory inode, a list of its children as a mapping from
    basenames to IIDs.

This way, when a large directory is renamed, it suffices to remove one directory
entry from the original parent and add one directory entry to the new parent,
requiring a constant number of updates to the underlying store.

#### Speed

Scanning large directory trees is slow, especially on rotational drives like
hard disks. The main contributor to this is seek times. We are accessing
inodes, each several hundred bytes in size, in essentialy random order. If
you wanted to get the worst possible performance from a hard disk, you probably
could not do much better than this.

This problem is aggravated by the structure of the ext4 filesystem. In ext4,
the disk is split into equally-sized regions called \D{block groups}. Each
block group contains both inode metadata and data blocks for a set of files.
\TODO{block groups ref:
%https://git.kernel.org/cgit/linux/kernel/git/torvalds/linux.git/tree/Documentation/filesystems/ext2.txt?id=c290ea01abb7907fde602f3ba55905ef10a37477\#n85
}

![ext4 block group layout (not to scale)\label{bg}](img/blockgroup.pdf){#fig:bg}

The dark bands represent areas storing inodes, the white are data blocks. Also
note that this picture is quite out of scale. The default block group size
is 2$\,$GB,[^flexbg] so on a 1$\,$TB partition there will be approximately 500
block groups. This makes inodes literally scattered all over the disk.

[^flexbg]: The default was 128$\,$MB for ext2/3. Acutally, ext4 block groups are
still 128$\,$MB by default but they are grouped into larger units called \D{flex
groups} (16 block groups per flex group by default), with inode metadata
for the whole flex group stored at its beginning.

This layout improves performance for most of the normal filesystem access
patterns (by improving locality between metadata and data blocks). However,
scanning the whole filesystem is not one of them.

Not all filesystems are like this. For example, NTFS keeps all file metadata in
one contiguous region called the Master File Table (MFT) at the beginning of the
partition. This allows the existence of tools like SwiftSearch[^ss]  that
read and parse the whole raw MFT in several seconds (bypassing the operating
system) and then allow instantaneous searches for any file by name, no previous
indexing required.

[^ss]: \url{https://sourceforge.net/projects/swiftsearch/}

Nothing like this can be done for ext4. Just reading all the raw inode regions
will include a lot of seeks and takes tens of seconds to minutes.

In ext4 and many other filesystems, the inode number directly corresponds to
the location of the inode structure on disk. Because of the block group
structure, the mapping is not linear but it is monotonic.  Therefore, if we
access inodes in inode number order, the access will be sequential inside
each block group (with perhaps only a few gaps for recently deleted inodes).

We can for example do the scan using a breadth-first search with a priority
queue ordered by inode number. We know the inode number from `getdents`
without `stat`-ing the inode itself 

##### Faster rescans

For the second and further scans, we can do even better. Linux stores a
modification time for directories as well as for files. The modification time
of a directory is the last time a direct child is added to it or removed from
it. Thus we can simply iterate over the directory records from our database
in inode nubmer order. We open each of them using the saved file handle, which
encodes the inode number and thus the location of the inode on disk.
We can then `fstat` the opened directory, which directly accesses this location,
without any path resolution steps that would require the kernel to look up
directory entries in parent directories.

This way, we access only inode metadata blocks (the gray areas in [@fig:bg]) and
not directory content blocks, which are stored in the white data sections. This
further reduces seeking.

Order    Access by       Time [s]
-------  -------------  ---------
inode    path                74.7
         handle              82.2
scan     path               108.3
         handle             254.9
find     path               281.0
random   path               694.8
-------  -------------  ---------

: Scan times for different access strategies. {#tbl:scantimes}

[@Tbl:scantimes] shows times (in minutes and seconds) necessary to `lstat` all
the inodes on a filesystem for different access orders (*scan* is the order
of a depth-first traversal that visits children in the order returned by
`getdents`, *inode* is ascending inode number order and *random* is a completely
random shuffle of all the inodes) and different access methods (by path or
by handle). The measurement was performed on a real-world filesystem with
approximately 2 million inodes, $10\,%$ of which were directories.

We experimented with several other techniques, for example massively
parallelizing the scan in the hope that the kernel and/or hard disk controller
will order the requests themselves in an optimal fashion. However, most of
these attempts yielded results worse than a naive scan so they would not be
discussed further.


#### Race Conditions

Tree scanning presents numerous opportunities for race conditions. Some were
already discussed in [@sec:singledir]. But the most serious threat is a file
being moved from one directory to another during the scan. To be more precise,
from a directory that we have not yet scanned to one that we have. We would
completely miss such a file from the scan and might mistakenly consider it
deleted.

As the whole scan may take several minutes, it is quite easy for this to
happen.

It cannot be detected or mitigated in any easy way with offline techniques
alone. However, we can use an online detection mechanism during the scan.
Then, if any changes to the filesystem happen while scanning, the kernel will
tell us about them and we can for example rescan the few affected directories.

Even if we are not interested in long-term realtime change monitoring, it pays
to set up online change detection even if only for the duration of the scan.
It is the only way we know of of mitigating such race conditions without
support of the filesystem (e.g. in the form of atomic snapshots).

## Online Change Detection

### Inotify

Inotify\TODO{link manpage} is the most widely used Linux filesystem monitoring API.
It is currently used by virtually all applications that wish to detect filesystem
changes: synchronization tools, indexers and similar.

When using inotify, a process must first create a \D{watch list} -- a list of inodes
that it wants to monitor. Inodes are added to the watch list using paths (file descriptors
may be added using the `/proc/self/fd` trick) but once added, the kernel keeps direct
reference to the inode.

Inotify supports reporting all the usual filesystem events (writes, creations, renames,
unlink) and several less usual ones (opens, closes and reads). Events are generated
for all inodes on the watch list and (in case of directories) their direct children.
This holds true even for events that do not touch the directory inode, like writes
to a file inside a watched directory.

Inotify assigns a unique cookie called the \D{watch descriptor} to every inode on the
watch list. This watch descriptor is then returned with events concerning this inode.
In case of directory-changing events (creations, renames and unlinks), a basename
of the affected file is returned alongside the watch descriptor of the directory. We
can simply keep a mapping from watch descriptors to IIDs or some other kind of internal
identifiers. This also gives us access to the file handle if a race-free access to
the affected inode is necessary.

However, there is one catch: inotify monitoring is not recursive. If you wish
to watch a directory tree, you have to add every single directory in the tree
to the watch list. For an inode to be added to the watch list, it must be first
looked up and read from the disk. This makes creating the watch list comparably
slow to a rescan and allows us to use the same optimizations as for scans to
make it a little bit less slow.

At the first glance, an additional advantage may be seen in the fact that we
need access only directories and can simply skip over all file inodes.
However, when directories make up $10\,\%$ of the inodes[^numdirs], accessing
all directories in inode order is not even 2 times faster than a full scan.

[^numdirs]: This number is approximately true for all the various filesystems
I have access to, both system and data, destop and server.

And if we scan only directories, a file might be modified during the watch setup
phase (if it takes over a minute, it is not unlikely) and we would miss such change.
Therefore it is probably better to do a full rescan when setting up inotify watches.
We can do both the rescan and watch creation in a single pass over the inodes, as
shown in alg. \ref{alg:inotify-watch}, a slight variation on \textsc{Recheck-All}.

<!-- http://tex.stackexchange.com/questions/1375/what-is-a-good-package-for-displaying-algorithms-->

\begin{algorithm}
  \caption{Inotify watch creation with recheck
    \label{alg:inotify-watch}}
  \begin{algorithmic}[1]
    \ForEach{record \I{rec} in inode database, ordered by inode number}
        \State \I{fd} $\gets$ \verb+open_handle+(\I{rec}$.$\I{handle})
        \IIf{it fails} skip the inode and remove it from database
        \State Add \I{fd} to the inotify watch list
        \State \textsc{Recheck}(\I{rec}, \I{fd})
    \End
  \end{algorithmic}
\end{algorithm}

And the two mechanisms beautifully complement each other: the scanning tells us
about any changes that ocurred before we encountered a given dirctory, while inotify
tell us of any changes that happened during or after scanning that directory. This
way we have also solved the race condition mentioned in [@sec:dirtree]. If a file
is renamed during scan from a not-yet-scanned  directory to an already-scanned directory,
we will get an inotify event for the target directory as we have already
added it to the watch list.

When an event is received, it is fairly trivial to update our internal structures
accordingly.

Thus the first online monitoring API turned up to be more useful for offline monitoring,
whose problems it can fix with negligible slowdown.

However, for regular long-term monitoring, the requirement of a minute-long scan with
100$\,\%$ disk load (during which your system will be rather slow) will be unpleasant.
We do not want to end up like many Windows users who have to wait a minute or more after
login for all the accumulated autostart programs to load before their system becomes
usable.

An alternative would be to perform the scan slowly, at low priority, perhaps even with
short pauses. However, that would increase the time before we start synchronizing files
to perhaps 5 minutes.

Another consideration is that the inotify watch list and the watched inodes (which cannot
be dropped from the inode cache because they are referenced by the watch list) consume
non-swappable kernel memory. This would not be a problem for most users as the amount
is approximately 0.5$\,$kB per directory. It would be a problem for extremely large directory
trees (hundreds of thousands of directories and more) but in such cases the scan times
would probably be the more serious issue.

### Fanotify

### The `FAN_MODIFY_DIR` Kernel Patch


### Other Methods

## Filesystem-Based Change Detection

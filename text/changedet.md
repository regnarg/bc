Change Detection
================

The purpose of a file synchronization tool is simple: whenever a change to the
synchronized tree is made in one replica, transfer the change to the other
replicas and apply it there. From this stems a natural need for a way of
detecting filesystem changes efficiently.

There are two broad categories of filesystem change detection methods.

\D{Offline change detection} consists of actively comparing the filesystem
state to a known previous state. The detection must be explicitly initiated by the
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
can store some data about their change history as a part of their on-disk
data stractures and offer operations that query these structures to return
information about filesystem changes. Two examples of this are the `btrfs`
find-new and send-receive mechanisms.

This last category seems to offer the best of both worlds: we get reliably and
efficiently informed of all changes. Often the comparison operation is fast
enough to be run very frequently, for example every minute, effectively replacing online detection. The
obvious disadvantages are that most filesystems do not support such operations
and the need for a solution specifically tailored to each filesystem that does
support change detection (there is no generic API, at least on Linux).

The next sections will survey various ways of doing each kind of change detection
on Linux. Even methods that are not inherently filesystem-based will often depend
on the idiosynchracies of different filesystem types. In such cases, we will
consider primarily `ext4` and `btrfs`, two commonly used Linux filesystems, while
remarking how other file systems may differ. For simplicity, we shall also only
discuss change detection in trees contrained to a single filesystem volume (i.e.,
not containing any mount points within them), therefore also to a single filesystem
type.

Offline Change Detection
------------------------

### The anatomy of linux filesystems

Before diving into change detection, we have to understand a bit about the structure
of Linux filesystems and filesystem APIs. If terms like *inode*, *hardlink*,
*file descriptor*, and `openat` are familiar to you, you can safely skip this section.
Most of what is being said here applies to all Unix-based operating systems,
however, some details might be specific to Linux.


<!-- Describe VFS and mounts first? -->
#### Inodes and links

The basic unit of a Linux filesystem is an \D{inode}. An inode represents one filesystem
object: e.g. a file, a directory, or a symbolic link. There are a few more esoteric inode
types, which we shall mostly ignore (so-called *special files*: sockets, named pipes and
device nodes).

The inode serves as a logical identifier for the given filesystem object. It also holds
most of its metadata: size, permissions, last modification time. However, **an inode does
not store its own name.**

The names are instead stored in structures belonging to the parent directory. A directory
can be thought of as special kind of file whose content is a mapping from names to inodes
of its direct children. The elements of this mapping are called *directory entries*.

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

\newcommand{\pfact}[3]{\noindent \textbf{Perplexing fact \##1:} #2\\\textbf{Explanation:} #3}

  * \pfact{1}{The syscall used to delete a file is called {\tt unlink}.}
    {It does not in fact delete
    a file (inode), but merely removes one link to it. Only when all links to
    an inode are removed, it is deleted.}

  * \pfact{2}{It is possible to delete a file that is opened by
    a process. That process can happily continue using the file.}
    {Inodes in kernel are reference counted. Only when all in-kernel
    references to the inode are gone \textit{and} the inode has no links, it is physically
    deleted.}

  * \pfact{3}{To rename or delete a file, you do not need write
    permissions (or in fact, any permissions) to that file, only to the parent directory.}
    {These operations do not touch the file inode
    at all, they change only the parent directory contents (by adding/removing directory
    entries).}

  * \pfact{4}{Renaming a file updates the last modification time
    of the parent directory, not the file.}
    {Same as above.}


We should also clarify that the term *inode* is actually a little overloaded. It can mean
at least three related but distinct things:

  * A purely logical concept that helps us to talk about filesystem structure and behaviour.
  * A kernel in-memory structure (`struct inode`) that identifies a filesystem object and
    holds its metadata. These structures are kept in memory as a part of the *inode cache*
    to speed up file access.
  * An filesystem-specific on-disk data structure used to hold file object metadata and usually
    also information about the location of the file's data blocks on the disk. However,
    some filesystems do not internally have any concept of inodes, especially non-Unix
    filesystem like FAT.

Each inode (in all the three senses) has a unique (within the scope of a single filesystem
volume) identifier called the \D{inode number}
(*ino* for short) that can be read from userspace.

#### Filesystem access syscalls

Most filesystem syscalls take string paths as their arguments. The inode corresponding to the
path is found in the kernel using a process called \D{path resolution}.
The kernel starts at the root inode and for each component of the path walks down the
corresponding directory entry. This process is inherently non-atomic and if files are
renamed during path resolution, you might get unexpected results. \cite{path_resolution}

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
in the kernel, which holds information about the open file like the current seek position
or whether it was opened read only. The OFD is tied to the *inode* so that it points always
to the same inode even if the file is renamed or unlinked while it is opened.

The application
gets returned a \D{file descriptor}, a small integer that is used to refer to the OFD in all subsequent
operations on the opened file. The most common operations are `read`, `write` and `close`,
with the obvious meanings, and `fstat`, which does a `lstat` on the file's inode without
any path resolution.

One can also `open` a directory and obtain a file descriptor referring to it.
Apart from listing directory contents, this file descriptor can be used as an anchor
for path resolution. To this end, Linux offers so-called \D{at-syscalls} (`openat`,
`renameat`,
etc.), that instead of one path argument take two arguments: a directory file descriptor
and a path *relative to that directory*. Such syscalls start path resolution not at the root
but at the inode referenced by the file descriptor. Thus userspace applications can use
directory file descriptors as "pointers to inodes". This will later prove crucial
in elliminating many race conditions.

### Change detection in a single file

Let's start off with something trivial: detecting changes in a single file.
First we need to decide what to store as internal state. Against that internal
state we shall be comparing the file upon the next scan. One option is to store
a checksum (e.g. MD5) of the file's content. However, this makes scans rather
slow, as they have to read the complete contents of each file.  This is
unfortunate as today's file collections often contain many large files that
rarely ever change (e.g. audio and video files).

A more viable alternative takes inspiration from 'quick check' algorithm used
by the famous `rsync`
file transfer program. \cite{rsync_man} It consists of storing the size and last modification
time (*mtime*) of each file and comparing those. This may be unreliable for
several reasons:

  * It is possible to change mtime from userspace (possibly to an earlier value)
    and some applications do so.
  * mtime might not be updated if a power failure happens during write.
  * mtime updates might be delayed for writes made via a memory mapping.
  * While most modern file systems store mtimes with at least microsecond
    granularity, some older file systems store mtimes with only second
    granularity.  This means that if the file was updated after we scanned it
    but in the same second, we wouldn't notice it during next scan. We can
    compensate for this in several ways: for example if we get an mtime that
    is less than two seconds in the past, we wait for a while and retry.

Most of these problems should be fairly unlikely or infrequent and the massive
success of `rsync` attests that this approach is good enough for most practical
uses.

Moreover, size and mtime can be acquired atomically while computing checksums
might give inconsistent results if the file is being concurrently updated.
We can still store checksums for consistency checking purposes  but it is
sufficient to update them only when the (size, mtime) tuple changes. And we
do not even have to recalculate the checksums every time a file is changed.
Instead, we can simply remember that a file has pending changes and delay
actual checksum calculations to make them less frequent.
This is discussed in [@sec:working].

### Scanning a single directory                     {#sec:singledir}

For a single directory, we can simply store a mapping from names to (size, mtime)
tuples as the state.

To read a directory, an application calls the `getdents` syscall (usually
through the `readdir` wrapper from the standard C library), passing it a
directory file dectriptor and a buffer. The kernel fills the buffer with
directory entries (each consisting of a name, inode number and usually
the type of the inode). When the contents of the directory do not fit into
the buffer, subsequent calls return additional entries.

We can hit a race condition in several places if entries in the directory are
renamed during scanning:

  * Between two calls to `getdents`. The directory inode is locked for the
    duration of the `getdents` so everything returned by one call should
    be consistent. However, a rename may happen between two `getdents`
    calls. In that case, it is not defined whether we will see the old name,
    the new name, both or neither. \cite{readdir_nonatom}
    The last case is particulary unpleasant because we might mistakenly mark
    a renamed file as deleted.

    This can be mitigated by using a buffer large enough to hold all the
    directory entries. This could be achieved for example by doubling the
    buffer size until we manage to read everything in one go. However,
    trying to do this for large directories could keep the inode locked
    for unnecesary long.

  * Between `getdents` and `lstat` (or similar). Because `getdents` returns
    only limited information about a file, we need to call `lstat` for each
    entry to find size and mtime. Between those to calls, the entry might
    get renamed (causing `lstat` to fail) or replaced (causing it to return
    a different inode). Both cases can be detected (the latter by comparing
    inode number from `lstat` with inode number from `getdents`, which is
    unreliable because inode numbers can be reused).

However, instead of problematic workarounds for specific issues, there is one
simple solution to *all* directory-reading race conditions. The key is that directories have
mtime, just like files. The directory mtime is, as you would expect, the last time a directory
entry was added to or removed from the directory. The solution is now obvious:
we remember the directory's mtime at the beginning of the scan. After we have
enumerated all the directory entries, we once again look at the mtime. If it is
different, the directory has been concurrently updated and the scan results
may be unreliable. In such case, we simply throw them away and retry after a delay.
The same caveats about mtime granularity apply as were mentioned above for files.

There is one other problem: when a file is renamed, it would be detected as
deletion of the original file and creation of a new on with the same content (or
just similar, if it was both renamed and changed between scans). Unless the data
synchonization algorithm can reuse blocks from other files for delta transfers,
this would force retransmission of the whole file.

The problem gets even more serious when renaming a directory, perhaps one
containing a large number of files and subdirectories. Unless we can detect
that this is the same directory, we would have to recreate the whole subtree
under the new name on the target side instead of just renaming the directory
that is already there.

To correctly detect renames, we would need a way to detect that a name we
currently encountered during the scan refers an inode that we know from earlier
scans, perhaps under a different name. For this to be possible, we need to be
able to assign some kind of unique identifiers to inodes that are stable,
non-reusable and independent of their names.

### Identifying inodes

#### Inode numbers

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

#### Enter filehandles

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
depends on the filesystem type used on the server side. Many file systems
(including ext4) create
file handles composed of the inode number and a so-called \D{generation number},
which is increased every time an inode number is reused. Such pair should
be unique for the lifetime of the file system.

Not all filesystems support file handles. Those that do are called
\D{exportable} (*exporting* is the traditional term for sharing a filesystem
over NFS). Most common local filesystems (e.g. ext4, btrfs, even non-Unix
filesystems like NTFS) are exportable. On the other hand, NFS itself, for
example, is not.

File handles are usually used by the in-kernel NFS server. But they can also be
accessed from userspace using two simple syscalls: `name_to_handle_at` returns
the handle corresponding to a path or file descriptor.  `open_by_handle_at`
finds the inode corresponding to the handle, if it still
exists, and returns a file descriptor referring to it. If the inode no longer
exists, the `ESTALE` error is reported. These syscalls were created to
facilitate implementation of userspace NFS servers. We shall (ab)use them in
rather unusual ways. \cite{fhandle_man}

Being non-reusable, file handles seem like a good candidate for persistent inode
identifiers. However, there is a different problem. The NFS specification does
not guarantee that the same handle is returned for a given inode every time. \cite[p. 21]{nfs-rfc}
I.e., it is possible for multiple different handles to refer to the same inode,
which prevents us from simply comparing handles as strings or using them as
lookup keys in internal databases. Most common file systems (including ext4
and btrfs) have stable file handles. However, just for the fun of it, we will
show a solution for the general case.

#### The best of both worlds

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

#### Extended attributes

Another possibility is to use POSIX extended attributes (xattrs) \cite{xattr} to help identity
inodes. Extended attributes are arbitrary key-value pairs that can be
attached to inodes (if the underlying file system supports them; most moder
Linux file systems do). Because they are attached to inodes, they are
preserved across renames.

This offers a simple strategy: store a unique inode identifier
as an extended attribute. Whenever we encounter an inode without this
attribute, we assign it a new randomly-generated identifier and store
it into the xattr.

However, we consider the handle-based scheme superior for several reasons:

  * Not all file systems support extended attributes (probably less
    than support file handles).
  * The size of extended attributes is often severely limited. For example
    on ext4, all the extended attributes of an inode must fit into a single
    filesystem block (usually 4 kilobytes). While our identifier would be
    rather small, we cannot predict how much data other programs store
    into extended attributes.
  * We use file handles for several other purposes, such as a race-free way
    of accessing inodes and to speed up directory tree scans (as described
    in [@sec:recheck].
  * Some programs copy all extended attributes while copying a file. This
    would create two inodes with the same identifier, which is asking for
    trouble. We could partially work around this by also storing the inode
    number in the xattr and trusting its value only when it matches the
    real inode number.
  * Extended attributes cannot be attached to symlinks. This seems harmless
    at the first glance, we do not need rename detection for symlinks
    because they are cheap to delete and recreate. However, rename detection
    on symlinks will prove crucial in a surprising fashion when implementing
    a feature called *placeholder inodes* ([@sec:placeholder]).

### Scanning a Directory Tree                               {#sec:dirtree}

#### Internal state

When scanning directory trees, we definitely do not want to store the full path
to each object. If we did and a large directory was renamed, we would need to
individually update the path of every file in its subtree\dots and probably
transfer all those updates during synchronization, unless additional tricks were
involved.

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
    names (without path) to IIDs.

This way, when a large directory is renamed, it suffices to remove one directory
entry from the original parent and add one directory entry to the new parent,
requiring a constant number of updates to the underlying store.

#### Speed

Scanning large directory trees is slow, especially on rotational drives like
hard disks. The main contributor to this is seek times. We are accessing
inodes, each several hundred bytes in size, in essentialy random order. That
is actually not true as file systems contain many optimizations that do a good
job at clustering related inodes together but these are far from perfect and
seek times are still a major concern.

This problem is aggravated by the structure of the ext4 file system. In ext4,
the disk is split into equally-sized regions called \D{block groups}. Each
block group contains both inode metadata and data blocks for a set of files.
\cite{blockgroups}

![ext4 block group layout (not to scale)\label{bg}](img/blockgroup.pdf){#fig:bg}

[@Fig:bg] shows the on-disk block group layout.
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
scanning the whole file system is not one of them.

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

##### Faster rescans                                    {#sec:recheck}

For the second and further scans, we can do even better. Linux stores a
modification time for directories as well as for files. The modification time
of a directory is the last time a direct child is added to it or removed from
it. Thus we can simply iterate over the all inode records in our database,
files and directories alike, in inode number order. We open each of them using
the saved file handle, which
encodes the inode number and thus the location of the inode on disk.
We can then `fstat` the opened directory, which directly accesses this location,
without any path resolution steps that would require the kernel to look up
directory entries in parent directories.

This way, we access only inode metadata blocks (the gray areas in [@fig:bg]) and
not directory content blocks, which are stored in the white data sections. This
further reduces seeking.

<!--
Order    Access by      Time (all inodes)   Time (directories only)
-------  -------------  ------------------  ------------------------
inode    handle         1:40                0:23       
         path           1:56                1:52       
scan     handle         4:41                0:41       
         path           4:23                4:21
find     path           4:41          
random   handle         > 1 h               > 1h
         path           > 1 h               > 1h
-------  -------------  ------------------  ------------------------

: Scan times (mm:ss) for different access strategies. {#tbl:scantimes}
-->

\hypertarget{tbl:scantimes}{}
\begin{longtable}[]{@{}llllll@{}}
\caption{\label{tbl:scantimes}Scan times (mm:ss) and throughputs (inodes/min) for different access
strategies. }\tabularnewline
\toprule
Order & Access by & \multicolumn{2}{c}{All inodes} & \multicolumn{2}{c}{Directories only}\tabularnewline
& & time & inodes/min & time & inodes/min \tabularnewline
\midrule
\endfirsthead
\toprule
Order & Access by & \multicolumn{2}{c}{All inodes} & \multicolumn{2}{c}{Directories only}\tabularnewline
& & time & inodes/min & time & inodes/min \tabularnewline
\midrule
\endhead
inode &  handle &  1:40 &  \:1.4$\,$M  & 0:23 & 530$\,$k \tabularnewline
      &  path   &  1:56 &  \:1.2$\,$M  & 1:52 & 110$\,$k \tabularnewline
scan  &  handle &  4:41 &  490$\,$k  & 0:41 & 300$\,$k \tabularnewline
      &  path   &  4:23 &  530$\,$k  & 4:21 & \:\:47$\,$k \tabularnewline
find  &  path   &  4:41 &  490$\,$k  & 4:27 & \:\:46$\,$k   \tabularnewline
random & handle & \textgreater{} 1 h && \textgreater{} 1h&\tabularnewline
& path & \textgreater{} 1 h & & \textgreater{} 1h &\tabularnewline
\bottomrule
\end{longtable}


[@Tbl:scantimes] shows times necessary to `lstat` all
the inodes on a filesystem for different access orders and access methods
on a real-world ext4 filesystem with approximately 2 million inodes
($10\,\%$ of which were directories).

The experiment has been performed as follows: first, we performed a normal
depth-first scan of the directory tree to obtain a flat list of all the inodes
in the file system containing inode number, file handle and full path of each inode
(this is similar to what Filoco metadata would look like, only we use paths
instead of parent/child relationships).

Then we the complete inode list was loaded into memory and sorted in one of
the following ways:

  - *inode* is ascending inode number order
  - *scan* is the original order in which we encountered the inodes during
    the recursive scan (that is, DFS order where children were visited in
    the order returned by `getdents`)
  - *random* is a completely random shuffle of the inode list

Then we clear the filesystem cache (using the `sysctl -w vm.drop_caches=3` command)
to prevent it from unpredictably distorting the results.
Only after that, we start measuring time. Then we try to `stat` all the inodes
in the given order in one of two ways:

  - *handle* means an `open_by_handle_at` syscall on the saved handle
    followed by an `fstat`.
  - *path* means simply `lstat`-ing the saved path, which triggers the path
    resolution process in the kernel.

For comparison, there is a row labelled *find*, which shows how long usual
DFS traversal of the directory tree (i.e. intermixed `getdents` and `lstat`
calls) would take (as performed by the `find -size +1` command; the `-size`
argument is needed to force `stat`-ing every inode).

The results confirm our predictions:

  * Accessing inodes in inode number order is faster (about two times)
    than accessing them in DFS order.
  * Accessing inodes using handles is faster than using paths.
    For all inodes the difference is rather slight, for a smaller subset
    (such as directories only) it can be up to five-fold.

All of this applies to an ext4 or similar file system on a rotational hard
drive. For btrfs and SSD, the differences will probably be negligible if any.
The results were produced using scripts in the `experiments/treescan2`
directory in attachment 1.

We experimented with several other techniques, for example massively
parallelizing the scan in the hope that the kernel and/or hard disk controller
will order the requests themselves in an optimal fashion. However, most of
these attempts yielded results worse than a naive scan so they would not be
discussed further.

#### Race Conditions                        {#sec:tree-races}

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

Inotify\cite{inotify} is the most widely used Linux filesystem monitoring API.
It is currently used by virtually all applications that wish to detect filesystem
changes: synchronization tools, indexers and similar.

When using inotify, a process must first create a \D{watch list} -- a list of inodes
that it wants to monitor. Inodes are added to the watch list using paths (file descriptors
may be added using the `/proc/self/fd` trick) but once added, the kernel keeps a direct
reference to the inode.

Inotify supports reporting all the usual filesystem events (writes, creations, renames,
unlink) and several less usual ones (opens, closes and reads). Events are generated
when anything happens to any inode on the watch list or a direct child of a directory
on the watch list.
This holds true even for events that do not touch the directory inode, like writes
to a file inside a watched directory.

However, the watching is not recursive. Thus if we want to watch a whole directory
tree, we need add all the directories in the tree to the watch list one by one.
In the previous section, we have shown an efficient way of doing that. Namely we
load a list of directory inodes from our internal database (presumably using an
index on the inode type column to make this fast), get a file descriptor to each
using `open_by_handle_at` and add the corresponding inode to the inotify watch list.
As per [@tbl:scantimes], this can be up to ten times faster than a naive recursive
directory traversal (but we need to add some time for reading our database). But
even 30 seconds of 100\% disk load on every startup is rather inconvenient.

Inotify assigns a unique cookie called the \D{watch descriptor} to every inode on the
watch list. This watch descriptor is then returned with events concerning this inode.
In case of directory-changing events (creations, renames and unlinks), a basename
of the affected file is returned alongside the watch descriptor of the directory. We
can simply keep a mapping from watch descriptors to IIDs or some other kind of internal
identifiers. This also gives us access to the file handle if a race-free access to
the affected inode is necessary.

Another consideration is that the inotify watch list and the watched inodes (which cannot
be dropped from the inode cache because they are referenced by the watch list) consume
non-swappable kernel memory. This would not be a problem for most users as the amount
is approximately 0.5$\,$kB per directory. For our example file system with $200\,000$
directories, this would constitute $100\,$MB of wasted memory.

Inotify can efficiently be used as an anti-race-condition aid during offline scans (as
discussed in [@sec:tree-races]). Because we
have to visit all the directories during the scan anyway, we can add inotify watches to
them at little extra cost (except for memory usage). To prevent all kinds of races, we
have to first add the inotify watch for a directory and only then read its contents.


### Fanotify

Fanotify \cite{fanotify} is the newest change notification API added to the Linux kernel.
Like inotify, it supports watching individual inodes but unlike inotify, it also supports
watching whole mount points. Note that this is not the same as watching one filesystem
volume because (1) one volume may be accessible via several mountpoints, (2) a mount point
may show only a part of a volume's directory tree. For example, after invoking the commands:

    mount -t ext4 /dev/sdb1 /mnt/hdd
    mount --bind /mnt/hdd/music/bob /home/bob/music

\noindent
there are two mount points:

  * `/mnt/hdd`, which shows the whole directory tree of the file system on  the `/dev/sdb1`
    device
  * `/home/bob/music`, which shows the `/music/bob` subtree of the file system on `/dev/sdb1`

When you create a fanotify watch for the `/home/bob/music` mount point, you get events for
filesystem changes made via this mount point. For example, if some program writes to
`/mnt/hdd/music/bob/test.mp3`, you will not get an event, even though the file
`/home/bob/music/test.mp3` now has different content than before.

This could be (ab)used to make fanotify watch only a given directory subtree. For example
when you issue the command `mount --bind /home/alice /home/alice`, the directory `/home/alice`
becomes a separate mount point (although it contains the same files as before), which can be
separately watched by fanotify. However, this has a drawback: it is not allowed to move files
between mount points, even if the two mount points refer to the same filesystem volume.

Another interesting property of fanotify is that you get a file descriptor to the affected
inode along with any event. From it we can determine inode number and file handle and look
up the corresponding object in our internal database.

However, fanotify has two important limitations:

  * Its use requires root permissions (because otherwise there is no easy way for the kernel
    to determine which events a user should be allowed to see).
  * More importatnly, it does not report directory-changing events (creates, renames,
    and unlinks).

### The `FAN_MODIFY_DIR` kernel patch

We have implemented an extension to fanotify that enables it to report directory change
events. This extension is available as a series of two kernel patches (currently against Linux 4.10, but
they should apply to any 4.x version with trivial modifications) in the
`src/fanotify/` directory in attachment 1.

Such an extension is useful not only in context of file synchronization but also
for example to filesystem indexers.

There are two main reasons why fanotify currently does not support directory events.
Let's look at how we deal with each of them.

#### Directory event semantics

The first problem is that it is not clear how to represent directory events and
what semantics should they have in order to be useful.

Inotify reports them in a rather complicated way that involves passing watch
descriptors of parent directories and string names of their children. Because
of race condition, by the time you receive the event, these names may already
refer to a completely different inode than the one the event was about. There are also
issues with regard to what is or is not guaranteed about ordering of these
events, especially in cases such as concurrent cross-directory renames. In
general, inotify directory events are hard to interpret correctly.

In contrast, the fanotify event interface is beautifully simple. You get a
fixed size structure with one event type, one file descriptor, no need to
allocate space for any strings and no need to worry about what they mean.

Our solution to this conundrum lies in the filesystem-watching wisdom that we
have already encountered several times: names are useless (and paths are even
more useless), inodes and file descriptors are great. So instead of passing any
names to userspace, we generate a simple event called `FAN_MODIFY_DIR` every
time the contents of a directory are changed in any way (a directory entry is
added or removed), i.e. exactly when the directory's mtime would be updated.

As with all fanotify events, you get a file descriptor -- one referring to the modified
directory, i.e. the parent of the created, renamed or unlinked file. This makes
directory modification events completely analogous to file modification events.
In case of a cross-directory rename, you get two `FAN_MODIFY_DIR` events for both the
old and new parent directory.

This scheme is based on a suggestion made on the Linux kernel mailing list back in 2009.
\cite{fanotify_dirchange} Since then, nobody has attempted to implement it.

This scheme has one more advantage (pointed out to me by kernel developer Jan Kára in
personal communication): when there are more events of the same type queued for an inode
before they are read by userspace, kernel automatically merges them. So for example when
moving 100 files from one directory to another, you would get only a few events instead
of 200.

We have expanded this idea into a more general trick. You can actually purposefully stall
reading fanotify events, to (1) give kernel more chance for merging repeated events,
(2) read events in larger chunks to reduce number of context switches, even when there
is a little delay between them. The realization is rather simple, as shown in algorithm
\ref{alg:fan_delay}.

\begin{algorithm}
  \caption{fanotify event grouping\label{alg:fan_delay}}
\begin{algorithmic}[1]
    \Repeat
      \State wait 5 seconds
      \State wait for a fanotify event to be available
      \State read all pending events into one large buffer and process them
    \EndRepeat
\end{algorithmic}
\end{algorithm}

This has to be done carefully because waiting too long might cause the kernel queue
to overflow and events to be dropped.

#### Which mount point?

The second problem lies in the fact that fanotify watches are tied to a specific
mount point. Thus to generate a fanotify event in reaction to a filesystem operation,
we need to know through which mount point the operation was performed. Two important
in-kernel structures are relevant to understanding this:

A `struct dentry` represents one directory entry. It contains the following information:

  - A reference the inode to which the directory entry refers (the child)
  - The name of the entry
  - A reference to the parent dentry. It really is the parent dentry, not
    the parent inode; this allows reconstructing full paths by walking the
    dentry parent chain. However, there can also be so-called *disconnected
    dentries* that do not know their parent or name, so they should be rather
    considered to represent an inode than a directory entry. These can be created for example
    when opening file handles, because in that case the inode is directly accessed
    without path resolution so its parents cannot be known.

A `struct path`, despite its name, does not represent a string path but rather the result
of path resolution. It contains references to:

  * The dentry represented by this path.
  * The mount point to which the original path belongs.

The kernel's open file description structure stores a `struct path` representing the
path using which the file was opened. This allows, among other things, (1) showing
full paths of files open by a process by tools such as `lsof` or `ls -l /proc/<pid>/fd`,
(2) generating correct fanotify events because the mount point is known.

However, most kernel-internal filesystem APIs, including the ones dealing with directory
changes, operate on inodes and dentries and do not get the mount information contained
in a `struct path`.

Here is how an `unlink` syscall is currently processed in the Linux
kernel:

1. The syscall implementation (`SYSCALL_DEFINE1(unlink)` in `fs/namei.c`) gets
   string path from userspace.
2. It calls helper function to resolve the path into a `struct path`.
3. It passes the dentry from the `struct path` to a kernel-internal function
   `vfs_unlink`.
4. `vfs_unlink` carries out the operation and generates an inotify event for
   the parent inode. It does not generate a fanotify event because it does not
   know the mount point.

The `vfs_unlink` function (and other similar functions like `vfs_rename`) is
a stable kernel API that is used on many places in the kernel so it is not easy
to change its signature.

Instead, we opted to generate the fanotify event directly in the syscall code,
and many other places that call the `vfs_*` functions, for example the in-kernel
NFS server. Scattering fanotify calls at several places across the kernel is probably
not a good long-term solution but practically it works.

Our patch has been submitted to the Linux kernel mailing list as a RFC but
it sparked little interest at the time. \cite{lkml-submit}

### Amir Goldstein's fanotify patches

Another solution to the fanotify directory events problem has appeared recently,
in parallel with ours. \cite{amir}

Amir Goldstein's patches are a much more comprehensive (and complex) solution
to the directory event reporting problem. They offer the following features:

  * Report separate fanotify event types for the individual kind of directory
    entry manipulations (create, rename, unlink).
  * Optionally report names of the affected directory entries in addition to
    the parent directory file descriptor. When this flag is disabled, the result
    is rather similar to our patch.
  * Allow attaching fanotify watches to filesystem volumes as a whole as opposed
    to specific mount points.
  * Allow reporting events about an arbitrary directory subtree of a file system,
    although this is subject to reliability issues. Specifically, as it filters
    events by walking dentry parents, it does not report events for disconnected
    dentries (because the kernel  simply does not know whether they belong to
    a given subtree).
    
The last point seems particularly interesting because this might
in the future allow file systems to generate fanotify events from within,
which are not related to any specific mount point. This could be used for
example by distributed or network file systems to report server-side changes.

<!--
### Other Methods

## Filesystem-Based Change Detection
-->

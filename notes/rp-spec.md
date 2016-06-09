The Synchronizer: specification
===============================

The Synchronizer (TS, TODO: better name) is[^1] a file synchronization tool --
it keeps several copies of a directory tree, located on different computers, in
sync. Changes made to one copy are automatically propagated to all the other
copies as soon as possible (unless configured otherwise). Concurrent changes to
different copies can give rise to conflicts. Some kinds of conflicts can be
resolved automatically, the rest is left up to the user. Once a conflict is
resolved on one node, the resolution automatically propagates to all the other
nodes.

[^1] Er, will be. Hopefully.

Stores and Groups                                                   (#sg)
-----------------

Each copy of the synchronized tree resides in a so-called *store*. A store
can be:

  * A computer running the TS software (called a **node**), or more precisely,
    a directory on such a computer.
  * A "dumb" external store
      - On-line
          * A cloud storage account (Dropbox, Google Drive, etc.)
          * A (S)FTP server or similar
      - Off-line
          * A USB hard drive

Individual store drivers should be pluggable for easy extensibility, in a manner
similar to Linux kernel filesystem drivers (each driver can implement a set of
operations, some operations have a default "dumb" implementation on top of
simpler operations). External stores can be protected with full encryption
(including file names) and strong git-like integrity checks, in case you don't
consider Google, Microsoft or the NSA the kind of people you want to share your
data with.

The stores do not have to hold a complete copy of the synchronized tree. To this
end, each file is classfied into exactly one *group* based on rules in a
configuration files. Each rule specifies a condition and the group for files
matching it. Supported conditions:

    * regex on basename/pathname
    * size range (bigger/smaller than X bytes)
    * perhaps other metadata (e.g. user xattrs)

Each store can then specify which groups it wants to hold. Synchronization of
file data from a group than happens (mostly, see below for exceptions) between
all stores subscribed to this group.

While file data is distributed in this manner, metadata is always centralized.
This means that each store contains at least the following information about
every file in the tree:

  * name
  * size
  * group
  * which stores have the file's contents

This way, we can transparently offer access to files not stored locally because
of space constraints (for example, using symlinks to a FUSE filesystem that
transparently fetches the file from another node; more on that in the
[Filesystem Presentation][#fspres] chapter).

In fact, a store doesn't even have to store the whole of a group. Some groups
can be made *distributed*, i.e. their content is split among multiple stores.
This is different from just making two separate groups in that you do not
have to manually decide which files to put in which (sub)group. For example,
you can split your movie collection between several USB hard drives. The files
are automatically assigned to the individual stores (1) to balance space
usage, (2) to keep related files (e.g. in the same directory, with a similar
name, etc.) together. You can even specify that for each file in a distributed
group, you want to hold e.g. two copies on two different stores from some set.
This is a bit like RAID.

More precisely, for each distributed group G, you can specify one more directives
in the configuration file with the format: "store a copy of each file from G on
at least K stores from some store set S". This way, you can for example specify
that you want at least one local AND one cloud copy of every file in the group.

(Meta)Data Model, History and Conflict Resolution                   (#model)
-------------------------------------------------

The basic unit of synchronization is an "object", in a similar sense to git.
An object usually represents a file or directory but other kinds of objects
are possible.

However, unlike in git, objects are mutable. Each object is identified by a
unique randomly-generated ID assigned during its creation. This allows us to
track the history of each individual object. Unlike git, history is always
tracked and conflicts resolved on a per-file basis. This is necessary because,
as opposed to a programming project, your home directory will probably contain a
bunch of mostly unrelated files. There can be a few clusters of related files
for which it would be meaningful to have shared history (e.g. projects) but for
this it is better to use tools dedicated to this purpose, i.e., git.

In future versions of TS, we might even add some support for treating git
repositories as indivisible entities and synchronizing them in a smart way
(basically just automatic pushes/pulls with per-node branches + somehow syncing
uncommited changes), but that is outside the scope of the thesis version. For
now, you can simply make TS ignore git repositories and use git features to sync
their content between machines.

Each object has a type (e.g. file, directory, symlink, ...) and a list of versions.

## A Version

Each version of an object contains:

  * A parent object reference
  * A name
  * In case of file objects
      - Cryptographic hash of the file's contents
      - Information about which stores hold the file's contents
  * A pointer to the parent verion, as described below
  * Possibly a cryptographic signature of the node where the version
    originated

You can see that unlike git and the Linux VFS inode/dentry concept, it is the
child who carries both its name and relation to the parent, not some entry in
the parent. This, among other things, simplifies the handling of rename-rename
conflicts, which now become a conflict on file objects, which can be resolved
on a file-by-file basis. If the conflicts were between different versions of
a directory object with different entries, the user would have to resolve all
the conflicts at once in order to bring the directory object into a consistent
state. However, this might bring more issues than it's worth and may be changed.

## Version Structure and Conflict Resolution                    (#histtrees)

Each version holds a pointer to its parent version (the one from which it was
created). This way, the versions of an object naturally form an oriented forest.
The versions might be identified with git-like cryptographic hash chains to
protect the integrity of history but this is yet to be decided.

Let's start with a simple linear history (capital letters denote versions,
numbers version trees/forests):

     B
     |
     A

    (1)

Now, let's suppose that on two nodes, concurrent changes are made, resulting
in two different histories.

     C          D
     |          |
     B    and   B
     |          |
     A          A

    (2)        (3)

A great property of these forests is that they can always be uniquely merged
by unifying identical vertices and adding the rest from both sides. For example,
merging (2) and (3) yields a new valid version forest:

    C   D
     \ /
      B
      |
      A

     (4)

This can clearly be done for arbitrary two forest, given that no two identically
labelled versions arise independently that are different or have different
parents. When using git-like hashes to identify versions, this clearly cannot
happen.

The forest (4) has the unusual property that it has two heads (versions with
no descendant), therefore we cannot determine which version to consider "the
newest" or "current". We call this state a conflict.

An important thing about conflicts is that they do not require immediate
action. Files with conflicts can be synchronized as easily as files without
them. The user can resolve the conflict whenever they see fit. When this
happens, a new version is created, with all the current heads as parents:

      E
     / \
    C   D
     \ /
      B
      |
      A

     (5)

Another important thing to notice is that once a conflict is resolved on
one node, the resolution automatically propagates to all the other nodes
using the overlay-unification process described above. I.e., merging
(5) with (4) cleanly yields (5) without any user interaction.

This is somewhat similar to the git concept of parents and merging, but
there are no branches and the idea of a conflict is very different
(in our world, mere existence of more than one "branch" is considered
a conflict, whereas true git-like "merge conflicts" do not exist because
we don't do any automatic content merging).

Whenever a conflict occurs, the user is notified somehow and the verison
that is currently in the user's filesystem is left intact. Other versions
are stored under different names.

A final note on history: only metadata history is guaranteed to be kept
by this version of TS. Some adaptive keeping of data history would be
nice but is a big topic that would warrant its own thesis [TODO: link
to (Er, should I say cite? This will take some getting used to.) Jirka's
work on icremental backups?]

The Filesystem Presentation Layer                              (#fspres)
---------------------------------

Each node stores a local database of objects with all the metadata described
above. All synchronization operations are directed by information from this
database. The database is automatically updated when changes are made to
TS-managed files, either online (by a mechanism like inotify) or offline
(by scanning the whole directory tree and comparing it with the database).
Unlike in git, the database contains only metadata, no data (for obvious
reasons, when you sync most your data, the 2x space overhead would be
unacceptable). This brings some interesting challenges that will be dealt
with later.

The details of the interaction between the TS database and the synchronized
directory tree are governed by a so-called *Filesystem Presentation Layer*.
There are different fspres layers, which differ in:

  * The way online/offline changes are detected.
  * The way renames are detected.
  * The way versioning is handled (when the user edits a file, whether
    and how we preserve the original version for history-keeping purposes).
    Mostly out of scope but should be considered for further extension
    possibilities.
  * The way in which atomicity of reading/overwriting files is guaranteed.
  * The way in which transparent acess to files not available locally
    is guaranteed (if it is).

### The individual FSP layers

## The basic FSP layer

# Change Detection

  * Online change detection is done by either of:
      (a) inotify. This has an advantage of detecting renames but a huge
          disadvantage that each (sub)directory must be monitored separately
          (no recursive monitoring). This results in huge watch tables that
          waste kernel memory.
      (b) fanotify. This has the disadvantage that fanotify does not report
          renames. At all. Not even as delete + create new file. Not sure
          whether this can be worked around. Perhaps fanotify could be
          extended to support renames but that may well be outside the
          scope of this thesis.
      (c) It can be completely disabled. The user than has to manually trigger
          offline scanning of all or only some files as she sees fit.

  * Offline change detection is done by comparing (size,indode,mtime,ctime)
    tuples (with old values stored in the database)
      - With some measures to resolve +/-1 errors resulting from
        second resolution of the times.
      - With some speed optimisations (e.g. queue stats ordered by inode
        number || start a lot of threads that scan in parallel and let
        the kernel/hard drive order the reads for us)
      - Renames are detected heuristically, see below.

# Race Condition Handling

NOTE: These are *very* rough ideas and probably subject to change. Where
possible, optimistic approaches to race conditions are followed (i.e., we
optimize for the case that no race condition occurs, on the assumption than in
general most files are updated infrequently).

  * When a file is to be overwriten by a newer version from a different node

      1. We write the new contents to a temporary file.
      2. We check that is is not opened read-write and that is has the contents
         of the parent revision. We note the (size,inode,mtime,ctime) tuple.
      3. We rename the original out of the way and check that the tuple has not
         changed because of a race condition. If it has not, we will try to put
         the new temporary file under the original name. If it has, we will try
         to return the old file to its original name.
      4. In either case, this will be done with link() to prevent overwriting
         any new file that might have been created under the original name. If
         such file has been created, we assume that it contains a new version of
         the original file (saved using a copy-and-replace method) and thus
         abort the whole operation and leave the newly-created file in place.
         This new version will then imported into our database, resulting in a
         conflict with the version from the other node.

  * When a file change is detected (NOTE this is just one proposal, there are
    several alternatives, including temporarily making the file read only and
    similar; this one prefers an optimistic approch to race conditions):

      1. If it was detected online, we wait for a short interval to ensure it is
         not a part of a group of related changes that we want to record as a
         whole.
      2. We mark the file as scheduled for synchronization. (NOTE: This does not
         keep any local history when synchronization does not happen, this can
         be easily changed, however, history-keeping is currently out of scope
         except where required for synchronization and conflict resolution)

  * When a file marked as changed should be read during synchronization and sent
    to other nodes:

      1. We copy the whole file to a temporary location, noting the (size,
         mtime, ctime, inode) tuple. This might be problematic for large files.
         Actually, we might look at an index describing block hashes of the
         parent version (which might be stored in our database) and save
         unchanged (albeit perhaps moved) blocks as references to the original
         blocks in an rsync-like manner. We will need something like that for
         delta-transfers anyway.
      2. We check that the metadata tuple hasn't changed while we were reading.
         If it has, we drop the copied data and schedule a retry afrer a random
         wait.

## The btrfs FSP layer

This layer (ab)uses some of the advanced features of the btrfs file system. This
main disadvantage is that last time I tried, btrfs was unusably slow on
rotational hard drives (e.g. five seconds to save a short text file in vim). I
suspect fragmentation, in which case it might work fine on SSDs.

  * The whole synchronized tree will probably be stored in a separate btrfs
    subvolume.

  * Offline changes are can be found by a hackish usage of the btrfs
    send-receive mechanism. It apparently is able to report things like renames
    and unlinks. Or perhaps find-new but that does not report metadata changes
    (renames, creation of empty files/dirs, unliks), only data writes. Both are
    root-only so they would require a suid helper or suchlike.

  * Online change detection can be done by invoking the offline detection e.g.
    every minute. Find-new is a cheap operation, not sure about send/recv. This
    rids us of the inotify long watch list overhead. However, if we can ever get
    fanotify to report renames, this might be a better option here.

  * The replacement process is similar to the basic FSP (not much more options
    here).

  * When reading files, we can use btrfs clone ioctl (cp --reflink). It *might*
    be atomic but I an hour of googling did not yield an authoritative answer
    (wikipedia says so but without citation, others are silent on the matter).
    Will have to dig in the sources someday. Snapshots are another option to
    play with but more complicated.


## The FUSE FSP layer

In FUSE mode, the backend storage is not directly accessible to the user.
Instead, the user accesses all her files thru a FUSE filesystem that is a part
of TS. Such filesystem can then trivially track all changes and prevent race
conditions. Another advantage is that no root privileges are required for the
full feature set, not even during installation (suid helpers and the like). This
allows the FUSE FSP to be used on shared computers. The main drawback is, as
always with FUSE, speed.

  * Online change detection is for free. Offline changes do not exist.

  * When a file is changed while we are reading it, we immediately know it,
    abort and retry later.

  * When replacing a file, we can block opening the original read-write until we
    are done. This doesn't much help with copy-and-replace updates, which will
    still have to be handled heuristically as in the basic FSP.

### Heuristic rename detection

In some contexts (e.g. offline change detection in basic FSP) a rename is is not
detected as a rename but as "one file disappeared, another appeared". However,
TS, unlike git, would like to record all renames explicitly. Therefore, whenever
renames cannot be detected natively, a heuristic is applied to all newly
appeared/disappeared to see whether some appear/disappear pairs can be
identified as renames. NOTE: This wouldn't catch for example swapping the names
of two files but this seems to be a rather weird thing to do. We can also look
at all changed files but that comes at the expense of speed (some of the
heuristics are quite expensive).

The main issue is that the file might have been renamed AND changed between two
scans, so merely looking for identical files won't do.

The individual heuristics (we will probably use a combination of some of those,
as per results of performance /reliability testing):

  * Look at the inode number of all changed files and compare it with some
    inode/file mapping in our database. If we find that some inode changed name,
    this is a rename candidate. We should probably do some content similarity
    comparison in addition to that.

  * Use an rsync-like approach. For each file in the suspect set, compute a
    strong hash (e.g. md5) and a weak rolling hash (e.g. crc32) for every
    aligned block of a fixed size. Put all the weak hashes from all the files
    into a data structure with very, very fast lookup. Then, go over the suspect
    files again, and now compute the fast rolling hash for unaligned blocks, too
    (i.e., try every byte as possible block beginning) and use the aformentioned
    DS to find matches. For every file Y count how many blocks it contains from
    each other file X. If the DS query is O(1) this is O(total size of suspect
    set). We cannot hope for much better but constants will matter here. We can
    notice that almost all queries to our DS will yield negative results and
    optimize for that. The use of Bloom filters (each for some different subsets
    of the CRC's bits) might help here.

  * For each of the files from the suspect set, take several rolling hash
    functions and compute the minimum of each over the whole file, yielding a
    vector of such minimums. Similar files will tend to have similar minimum
    vectors. The more hash functions we use the more accurate, at the expense of
    speed.

  * We split the file into blocks with content-based boundaries (e.g. when a
    rolling hash function gets below a certain value, we create a new block
    boundary, with limits on min/max block size). This way, similar files should
    end up containing a lot of identical blocks. Then we simply put all block
    hashes into a big hashtable and count matches.


The Synchronization Process
---------------------------

### Store Discovery

Before nodes can exchange data, they have to discover other nodes (and other
kinds of stores). There are several discavery mechanisms and, as usual, more
are pluggable:

  * Node discovery
      - Some nodes can have static hostname/IP adress set in the configuration.
      - Nodes that change location can register with a registration server.
      - Discovery of nodes in the local ethernet segment (which for most
        people is the same as "the local network") using multicast dicovery
        protocols such as mDNS-SD.
  * External stores
      - Most are statically configured
      - External disks might be accessed with the help of UDisks (allows
        unprivileged mounting and more)

### Establishing Communication

The communication model is as follows: each node is connected to some network(s)
(where as far as we can tell, a "network" is either a local internet segment
or the whole Internet) with some bandwidth. It knows about other nodes in the
same  network (thru discovery, static configuration or from a registration server),
knows its own rough bandwitdth estimate (which may be statically configured for
non-migratory nodes, guessed from connection type (hint: EDGE is slow) or updated
dynamically during data transfers). Not all nodes connected to a "network" might
be able to reach each other (e.g. two internet-connected nodes behind NATs).

When nodes have discovered their neighbours, we want to select a master node for
each network that will control the whole synchronization process in that
network. We do this per-network because it can easily happen that you have two
devices connected by a fast network with a slow path to the internet (e.g. every
other KSP camp). Each device has a "master priority" that will probably be
statically configured for a sake of simplicity but could be also computed based
on some "goodness" parameters. What makes a good master (in order of
importance): (0) being reachable by every node (in case of the internet master,
this means having a public IP), (1) low-latency connection to the network, (2)
stability (e.g. being up most of the time, not changing location too often), (3)
high-bandwidth connection to the network.

As nodes discover other nodes, they form connected components and their idea
of who is the master changes. We have to ensure that at any time, each connected
component has exactly one master. We should also make sure that master selection
converges rather than oscillates.

Nodes can be in one of three states: master (has at least one confirmed slave),
slave (has an established master) or new (not yet decided). When two nodes (A
and B) connect, several options may occur:

  * A is a slave with master M, B is new -> A redirects B to M and ends
    connection.
  * A is a master, B is new -> they compare their priorities. If p(A) > p(B),
    B simply becomes a slave of A. Otherwise B should replace A as master.
    This probably does not have to happen immediately, e.g. if there are
    currently some transfers in progress directed by A. Until this happens
    B can consider itself a temporary slave of A.
  * A is master, B is a slave with a different master -> a message is sent
    to one of the masters to initiate a component merge with the other master.
    The initial connection terminates and we wait for one of the masters to
    redirect its clients to the other master.
  * A and B are slaves with different masters -> A redirects B to its master,
    thus converting this to the previous case.
  * A and B are slaves with the same master -> connection ends, they are
    already in the same component.
  * A and B are masters. They compare their priorities, one of them becomes
    a slave and sends a redirect to all of its former slaves to the new master.
    This joins two connected components into one.

This forms a digraph with slave->master edges. No node is willing to be
a master and a slave at the same time, thus there are no paths of length
greater than 1. No node is also willing to be a slave to two masters, therefore
all nodes have an outdegree of 1. From this is apparent that each connected
component will have one well-defined master. As nodes discover each other,
these components will join-up in a DFU-like manner.

We do not start exchanging data until master selection stabilises for some
period of time (perhaps with some fallbacks when this seems impossible, e.g.
because one high-prio node keeps disconnecting and reconnecting, in which
case it probably should not have high priority anyway ;-)).

### Metadata Synchronization

When master selection has stabilised, each node should have a connection
to the master. Then each node will do a full metadata exchange with the
master using some delta-transfer algorithm (most of the time, there will
be only small differences in the rather large metadata database). One
option is for example to have a hash that describes the state of all the
objects, another two that describe the first and second half of the
Object ID space (we assume object IDs are uniformly distributed), and
so on. These hashes can be imagined to form a binary tree. The nodes
can exchange these hashes layer by layer from the top and when they
find they have a common subtree, they skip anything inside it. We should
be able to cover each unchanged object id range with O(log n) such hashes.
And if we assume the number of changes to be small (almost O(1)) compared
to the total metadata size, we will get close to O(log(total unchanged)
+ total changed) pieces of information to be exchanged for a full
metadata sync. This is just one idea of how it *could* be done, not
a definitive choice.

Metadata sync is first done from all slaves to the master and then from
the master to all slaves. This ensures that at the end, everyone has
all the data. The receiving party always merges the metadata history
trees as described in [Version Structure and Conflict Resolution][#histtrees].

### Data Synchronization

After the metadata phase, the master has full information about who has
what data and who should have what data. It also decides where to store
any new files in distibuted groups. Then it uses some heuristic algorithms
(to be invented) to plan how to get all the data where it should be in
a short enough time, while respecting group priorities (Groups with highest
priority should have lowest latency, i.e., finish syncing first. This
can be "hot"/temporal data like emails, TODOs and shopping lists).

The planning algorithm will take into account the rough bandwidth estimate
of each node and the public reachability of each node. Some simple cases
are trivial to solve: e.g. if you have one piece of data to distribute
to all the other nodes, it is optimal to upload it to the highest-bandwidth
node first (as then (1) this will be finished fastest, allowing the next
round of transfer to start soonest, (2) will maximize the distribution
bandwidth in the next round). With a lot of pieces of data and priorities
an exact solution probably won't be found but a good enough approximation
/ practically usable heuristic might be.

Then master issues commands to slaves to exchange data. When two slaves
are behind NATs, they have to do this using and intermediary node (while
NAT-T support would be nice, it was deemed too hard for the scope of this
thesis).

Implementation and Micellaneous Notes
-------------------------------------

TS will likely be written in a mixture of Python (high-level logic) and C
(low-level, performance-intensive parts). Or should we ditch Python for Perl?
Not sure yet, especially in terms of robustness. Where reasonably possible,
individual components will be split into separate processes for realiability,
debuggability and hackability.

The whole thing should be highly configurable but work out of the box with
little to no configuration. This is very important to lower the entry barrier
for users willing to try a new software but with only limited time on their
    hands. They should first get to use and like it and only then invest time
    into fine-tuning.

As much code as possible (e.g. algorithmic parts) should be written as
platform-independent libraries/modules. However, the whole resulting product is
expected to run on Linux only (at least the thesis version, support for more
platforms might be added in later versions).

The project will we open source (probably using MIT license because it is the
only one I actually understand after reading it, however, not completely decided
yet). It will be developed publicly on github from the start.

Future Ideas
------------

All out of scope for the thesis, but could be considered for future
development:

  * It would be great to offer an API for applications to synchronize structured
    data and do meaningful conflict resultion. This could replace stuff like
    Firefox Sync, and more importantly, numerous web services in a decentralized
    manner. Having your data / settings / contacts / messages / calednars /
    whatever available everywhere is great but that does not have to mean giving
    up control over them. Let's build a competitor to the cloud fad!
  * Some limited support for other platforms. Namely Android and Win32 would be
    nice to start with.
  * Get fanotify rename support to the mainline kernel.

# Metadata Synchronization

## Metadata Model

\TODO{Move this to a more general intro section?}\
First some terminology. Filoco synchronizes data among a set of *stores*. A store
is a directory that holds the synchronized files, along with some Filoco-specific
metadata. The whole group of stores that are synchronized with each other is called
a *world*.

Filoco follows the philosphy of ``centralized metadata, decentralized data''
espoused by git-annex:\TODO{link} each store contains metadata about every
file in the world but only stores the content of some files. Each store can
configure which files it wants to have copies of.

Decentralization of data allows you to choose a compromise between storage
requirements, redundancy, and availability. You can configure some small,
important or often-used files (emails, writings, notes, own source code) to be
always replicated everywhere and bigger and less important files (movies you
will probably never watch again) to have only one copy distributed among
several slow external hard drives in your closet. And there is of course the
middle ground of keeping some files on say 3 out of 10 available stores for
some redundancy.

Currently, you need to manually configure which stores store which files
(using filters based on file path, type or size, as \TODO{described later}).

On the other hand, centralization of metadata allows you to always keep track
of all your files, no matter where they are stored, even if it is an external
hard drive in your safe deposit box. The synchronized metadata contain
a complete directory tree (i.e. file/directory names and parent-child
relationships) of all the files in the world, which is shared among all the
stores.

This means that is is not possible to have a file stored under
a different name in one store that in another. If a file or directory is
moved or renamed on one store, this change is replicated to all stores, even
those not hosting the file's content. The rename can even be initiated from
such a store. You can completely reorganize your directory hierarchy from
the comfort of your laptop, even though some of the files are physically
located only on offline external drives. The next time you connect such drive
and perform synchronization, these renames will be applied there.

Apart from the directory tree and some basic metadata like file sizes, the
centralized metadata contains two important pieces of information:

  * Data location, that is, a list of nodes that have the file's content.
  * Data checksum. This allows detecting media failure or tampering and
    using another data replica if available.

### Synchronized metadata

Now we shall examine the metadata structure in more detail. Metadata is modelled
as a set of immutable *objects* of several different types (described below).
Each object has a unique 128-bit identifier, generated etither randomly or using
a cryptographic hash function. The result of complete synchronization between two
stores is always the set union of their objects, although partial synchronization
is also possible (e.g. restricted to a directory subtree).

How can we represent changing entities (e.g. files with changing content) using
immutable objects? In exactly the same ways as git commits are organized\TODO{link?
https://git-scm.com/book/en/v2/Git-Internals-Git-Objects}.
We create a new object for each version of the file, which contains references
to the parent version(s). As in git, the version 

As synchronization never deletes objects, we are currently forced to keep
indefinite metadata revision history. A cleanup mechanism might be introduced
in the future.

The following types of objects currently exist:

  * A **filesystem object (FOB)** represents one file or directory (special
    inodes like sockets or devices are currently not supported). It serves
    primarily as an identifier for the filesystem object that is stable across
    renames. It also carries immutable metadata like inode type (file or
    directory). Its ID is randomly generated.

  * A **filesystem object version (FOV)** contains all the mutable metadata
    about a FOB, namely:
      - name (without path)
      - ID of parent directory FOB
      - timestamp (when this version was created)
      - identifier of store where this version originated
      - \TODO{a signature of the originating store (see the Security chapter)}
      - for files:
          * size
          * content hash (except for working revisions, \TODO{see below}),
            \TODO{What exactly? Hash of whole file? Some Merkle-tree-hash
            of blocks?}
      - a list of parent versions (see section on conflict resolution below)

    The ID of a FOV is a cryptographic hash of all the above fields. Because
    those also include parent FOV IDs (which are in turn hashes of parent FOVs),
    the FOVs form a Merkle tree\TODO{link?}. This ensures integrity of revision
    history and prevents a compromised node from rewriting it without notice.

  * \TODO{A **storage record (SR)**.}

Please note that the versioning of FOBs is there only to facilitate synchronization,
conflict resolution (see below), and auditing. We do not try to systematically
keep the content of old file versions. Except for when conflicts occur, each store
only keeps the newest version of any file known to it. Because of synchronization
delays, old versions can be present in the world for quite some time but this is
a byproduct and users should definitely not rely on that. However, the architecture
is intentionally designed such that (optional) versioning can be implemented in the
future.

### Versioning and conflict resolution

Wherever there is bidirectional synchronization, there looms the threat
of conflicts. Imagine that two stores $A$ and $B$ have the same version $v$ of
a file. Then the user makes changes to the file in store $A$ (perhaps on a laptop),
creating a new version $w_A$. Later they modify the file in store $B$,
which still has the old version $v$ (perhaps it is on their work computer,
because they forgot the laptop at home). They make some other, independent changes,
creating a new version $w_B$.

When they synchronize $A$ with $B$ later, both stores will have both versions
$w_A$ and $w_B$ in their metadata database. But which of these versions should
be considered ``current'', which version of the file should be written to the
file system? Clearly, it is incorrect to replace $w_A$ with $w_B$ on $A$
(even though $w_B$ has a newer timestamp), because the changes made from $v$
to $w_A$ would be lost. It is also incorrect to just keep $w_A$ and ignore
$w_B$, for the same reason.

This situation is called a *(version) conflict* and is familiar to most readers
from revision control systems like git. While in some simple scenarios, conflicts
can be resolved automatically using techniques such as three-way merge or git's
recursive merge, they often require user intervention.

In Filoco, we decided to leave all conflict resolution up to the user, for three reasons:

  * Conflicts should be much less common than in revision control systems. Most
    RCS conflicts are caused by multiple people working on one project simultaneously.
    Because our primary focus is managing personal data, we usually expect only one
    person making changes to files in a Filoco world. But conflicts certainly can
    happen, e.g. because of delayed synchronization and offline stores, as suggested
    by the scenario above.

  * We are not limited to source code or plain text and have to handle all kinds of
    files including binary (LibreOffice documents, images, archives, databases\dots).
    There is no universal conflict resolution strategy for such a wide variantion of
    file types.

  * As we do not systematically keep the content of old file versions, the common
    parent of the two conflicting versions is not guaranteed
    to be available at the time of resolution, which precludes using most
    classical conflict-resolution strategies based on three-way merge and it variants.

Thus when a conflict occurs, we simply present the user with both the conflicting
versions and they have to somehow merge their content, either manually or by using
some specialized tools.

The following additional requirements have been set for conflict handling in Filoco:

1.  Conflicts must be automatically and realiably detected, so that we can
    apply all non-conflicting changes without user intervention on the one hand
    and inform the user of any conflicts on the other.

2.  The user should not be forced to resolve conflicts immediately (e.g. as a part
    of the synchronization process). When a conflict occurs, the synchronization
    should finish completely, synchronizing all the other changes, conflicting or
    not. The user should be able to resolve any conflict locally at a later time
    (for example when the user wants to access the affected file).

3.  Conflicts should not impede further synchronization. For example, if store
    $A$ has conflicting versions $v_1$, $v_2$ of a file and later synchronizes
    with a store $C$ that has neither, it should transfer both versions there.
    The user can then resolve the conflict in any of the stores.

4.  Once a conflict is resolved in one store, the resolution should spread to all
    other stores. This makes the previous requirement much more useful. Of course,
    if there were independent changes that were not part of the resolution, this
    can create more conflicts. 

We shall present a simple solution that fulfills ale these requirements. It is in
large part based on how branching and merging works in git.

Each FOV has a list of parent FOVs. Usually (except for when resolving conflicts),
this list contains just a single item: the logically preceding version. When you
have a version $v$ of a file on your file system and modify it, a new version $w$
is created with a single parent $v$. The parent-child relationship signifies that
$w$ is based on $v$, that it incorporates all the content from $v$ that the user
did not purposefully remove, that it supersedes $v$. Whenever a store has version
$v$ checked out\TODO{define checkout earlier} (and not locally modified) and
acquires version $w$ through synchronization, Filoco automatically replaces
the checked out version with $w$.

### Working revisions


### Local filesystem metadata

### Metadata storage

## The Set Reconciliation Problem

Our metadata is modelled as a set of immutable objects identified by unique IDs.
In order for two repositories $A$ and $B$ to synchronize their metadata, A should send
to B exactly the objects $A$ has but $B$ does not (an vice versa, if we want bidirectional
synchronization). If $O_A$ is the set
of object IDs possesed by $A$ (analogously $O_B$ for $B$), $A$ should transmit the set
difference $O_A \setminus O_B$.

The only problem is, $A$ does not know $O_B$. In a cenralized client-server setup, the
client can keep track of which objects it has already sent to a server and send only
new ones during synchronization. In this case, the client is essentially keeping track
of the intersection $O_C ∩ O_S$ without knowing the whole $O_S$, which is enough for
computing the set difference. The server can do the same for every client.

This is indeed what most centralized synchronization tools do. However, such approach
does not translate to a distributed setting. For example, assume node $A$ has a lot
of new objects compared to node $C$ and keeps track of this. Now we synchonize node
$A$ with another node $B$ and then $B$ with $C$. Now $C$ has all the extra objects from
$A$ but $A$ is not (and cannot) be aware of that. If we synchronize $A$ with $C$ at this
point, it will send all those objects all over again.

Instead, we will use a stateless approach. We want a protocol that allows two nodes to
efficiently compute the intersection $O_A ∩ O_B$ without any prior mutual information
($A$ knows only $O_A$ and $B$ knows only $O_B$ at the start of the exchange).

This is a known problem called the *Set Reconciliation Problem*. We will present several
existing solutions (in roughly historical order) and one original solution to this problem.
We will focus mostly on the properties and key ideas of individual protocols than on
details of their implementation or proofs of correctness.

We will be interested not only in the total amount of data exchanged by the protocols
but also the number of exchanges (network roundtrips) required. This is interesting
because often, especially on mobile networks, latency is a much greater issue than
bandwidth.

Furthermore, we shall assume that the set differences are usually small compared to
the complete sets. This seems to be the typical case in file synchronization (you
can have a million files on your disk but you usually touch only a few dozen in one
day). Also, if for example 

### Reconciliation Trees

### Invertible Bloom Filters

### Our Approach

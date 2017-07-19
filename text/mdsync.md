# Metadata Synchronization

## Metadata Model

As stated in the introduction, in Filoco, every store keeps a complete copy
of the metadata about all files in the realm but only stores actual data
of a subset of the files.

This concept was used for example by git-annex
\cite{annex}, where, as the name suggests, metadata is stored in a git
repository (with actual file contents stored externally in a distributed
fashion).

The user can configure which
files should be replicated to which stores -- either on a per-file basis
or using filters depending on file name, path, type or size.
This allows them to choose a compromise between storage
requirements, redundancy, and availability. 

For example you can configure some small,
important or often-used files (emails, writings, notes, own source code) to be
always replicated everywhere, while bigger and less important files (movies you
will probably never watch again) will have only one copy distributed among
several slow external hard drives in your closet.

Currently, you have to manually configure which old movies should be stored
on which slow external drives. In the future, there should be the option of
automatically distributing a given set of files over a given set of stores.
So you could classify five stores as *movie disks* a thousand files as *movies*
and Filoco would automatically spread the files across the drives.
There could be even more advanced option, for example configure some files
to be stored at two out of four backup drives and one out of two server stores.

On the other hand, global metadata allows you to always keep track
of all your files, no matter where they are stored, even if it is an external
hard drive in your safe deposit box. The synchronized metadata contain
a complete directory tree (i.e. file/directory names and parent-child
relationships) of all the files in the realm, which is shared among all the
stores.

This means that is is not possible to have a file stored under
a different name in one store than in another. If a file or directory is
moved or renamed on one store, this change is replicated to all stores, even
those not hosting the file's content. The rename can even be initiated from
such a store.

You can completely reorganize your directory hierarchy from
the comfort of your laptop, even though some of the files are physically
located only on offline external drives. The next time you connect such drive
and perform synchronization, these renames will be applied there.

Apart from the directory tree and some basic metadata like file sizes, the
centralized metadata contains two important pieces of information:

  * Data location information, that is, a list of stores that have the
    file's content (and in what version, as described below). This allows
    you to ask Filoco for the content of a file and if it is not locally
    available, it will either fetch it from a reachable store that has it
    or at least inform you on which stores host the file. You can then take
    the right external disk out of your closet or turn on your secondary
    laptop as neccesary.
  * Data checksum. This allows to detect media failure or tampering and when
    detected, use another replica if available.

### Detailed metadata structure

Now we shall examine the structure of the globally replicated metadata in more detail.
Metadata is modelled
as a set of immutable *objects* of several different types (described below).
Each object has a unique 128-bit identifier, generated etither pseudorandomly or using
a cryptographic hash function from the object's content.
The result of complete synchronization between two
stores is always the set union of their objects, although partial synchronization
is also possible (e.g. restricted to a directory subtree).

How can we represent changing entities (e.g. files with changing content) using
immutable objects? In exactly the same ways as git commits are organized \cite{gitobj}.
We create a new object for each version of the file, which contains references
to the parent version(s).

As synchronization never deletes objects, we are currently forced to keep
indefinite metadata revision history (just as is the case for git). A cleanup
mechanism might be introduced in the future.

The following types of objects currently exist:

  * A **filesystem object (FOB)** is the basic unit Filoco works with. It
    represents a single file or directory (other inode types, including
    symlinks, are currently not supported, though support should be trivial
    to add). It serves
    primarily as an identifier for the filesystem object that is stable across
    renames. It also carries immutable metadata like inode type (file or
    directory).
    
    A filesystem object has three important conceptually mutable
    properties: (1) content hash (files only), (2) location in the directory tree, (3)
    storage information (a list of stores that host the file's content).
    As suggested above, the values of these properties are not stored inside the FOB
    object but instead as separate version objects (FCVs, FLVs and SRs) described
    below.

  * A **file content version (FCV)** contains information about one version
    of a file's content. It stores the ID of the relevant FOB and, similartly to a
    git commit, the content hash and a list of parent versions (FCVs) of the given file.
    The parent list is used to establish an ordering on the versions. This is
    necessary because the FCVs are stored in an otherwise unordered object set.
    It also helps conflict handling. See [@sec:versioning] for a\ precise explanation
    of parent version semantics.

  * A **FOB location version (FLV)** describes the location of a filesystem object as
    a versioned property. Location is represented by the tuple (*parent*, *name*),
    where *parent* is the ID of the parent FOB. This format was chosen for three
    reasons: (1) It allows us to efficiently rename or move directories that contain
    a large number of files and subdirectories (which would be impossible if we stored
    full path for each file). Each such move costs only one new FLV for the directory
    being moved. (2) While maintaining a list of child FOBs for each directory would also
    allow for efficient renames and would be closer to Unix tradition, a parent
    pointer is a scalar value whose versioning is conceptually much easier than trying
    to define semantics for versioning child lists. (3) It corresponds to my personal
    intuition that name and parent directory are logically properties of the file (for
    name it should be quite clear, directory could be considered a kind of category
    tag attached to a file). Similarly to FCVs, a FLV carries a list of parent FLVs.

  * **Storage records (SR)** describe storage events. A storage event consists of
    a store beginning or ceasing to host a given FCV. The fields of a SR
    are (1) store identifier, (2) FCV ID, (3) event type (start or end of object hosting) and (4)
    a list of parent SRs, just as with other versioned objects. To determine whether
    a store has the contents of a FCV available, one has to look at the event type
    of the last (by parent-child ordering) SR for the given FCV and store ID (while
    remembering that this information is not necessarily up to date, so we have to
    be wary about deleting a file independently on two stores because each of them
    thinks the other has a copy).

There are also a few attributes common to all the object types:

  * An identifier of the store which created the object.
  * A creation timestamp.

Please note that the versioning of FOB properties is there only to facilitate synchronization,
conflict resolution (see below), and auditing. We do not try to systematically
keep the content of old file versions. Except for when conflicts occur, each store
only keeps the content of the newest version of any file. Because of synchronization
delays, old versions can be present in the realm for quite some time but this is
a byproduct and users should definitely not rely on that. However, the architecture
is intentionally designed such that (optional) versioning can be implemented in the
future.

### Versioning and conflict resolution                          {#sec:versioning}

Wherever there is bidirectional synchronization, there looms the threat
of conflicts. Imagine that two stores $A$ and $B$ have the same version $v$ of
a file. Then the user makes changes to the file in store $A$ (perhaps on a laptop),
creating a new version $w_A$. Later they modify the file in store $B$,
which still has the old version $v$ (perhaps it is on their work computer,
because they forgot the laptop at home). They make some other, independent changes,
creating a new version $w_B$.

When they synchronize $A$ with $B$ later, both stores will have both versions
$w_A$ and $w_B$ in their metadata database. But which of these versions should
be considered ``current'', which version of the file should be *checked out*
(i.e., written to the file system)? Clearly, it is incorrect to replace $w_A$ with $w_B$ on $A$
(even though $w_B$ has a newer timestamp), because the changes made from $v$
to $w_A$ would be lost. It is also incorrect to just keep $w_A$ and ignore
$w_B$, for the same reason.

This situation is called a *(version) conflict* and is familiar to most readers
from revision control systems like git. While in some simple scenarios, conflicts
can be resolved automatically using techniques such as three-way merge[^3w]
or git's recursive merge, they often require user intervention.

[^3w]: This technique is now virtually ubiquitous. It originated in the GNU `diff3`
       program developed by Randy Smith in 1988\cite{threeway}.

In Filoco, we decided to leave all conflict resolution up to the user, for three reasons:

  * Conflicts should be much less common than in revision control systems. Most
    RCS conflicts are caused by multiple people working on one project simultaneously.
    Because our primary focus is managing personal data, we usually expect only one
    person making changes to files in a Filoco realm. But conflicts certainly can
    happen, e.g. because of delayed synchronization and offline stores, as suggested
    by the scenario above.

  * We are not limited to source code or plain text and have to handle all kinds of
    files including binary (LibreOffice documents, images, archives, databases\dots).
    There is no universal conflict resolution strategy for such a wide variety of
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

4.  Once a conflict has been resolved in one store, the resolution should spread to all
    other stores. This makes the previous requirement much more useful. Of course,
    if there were independent changes that were not part of the resolution, this
    can create more conflicts.

5. It should be possible to rename or move a file in one store and edit it in
   another without this being considered a conflict.


We shall present a simple solution that fulfills ale these requirements. It is in
large part based on how branching and merging works in git. First, we shall look
into content versioning and then briefly mention location versioning and storage
record relationships.

Each FCV has a list of parent FCVs. Usually (except for when resolving conflicts),
this list contains just a single item: the logically preceding version. When you
have a version $v$ of a file on your file system and modify it, a new version $w$
is created with a single parent $v$. The parent-child relationship signifies that
$w$ is based on $v$, that it incorporates all the content from $v$ that the user
did not purposefully remove, that it supersedes $v$. Whenever a store has version
$v$ checked out (meaning that the contents of the corresponding file in the
user's local file system corresponds to version $v$) and
acquires version $w$ through synchronization, Filoco automatically replaces
the checked out version with $w$.

The parent-child relation (or more precisely, its transitive closure) describes
a partial ordering on the versions. As long as you keep your replicas up to date
and always edit only the chronologically newest version of the file, the ordering
is linear (the version graph is a path) and there is a unique maximum ("newest
verson").

However, when you make changes to an older version of the file (in a store that
is not up to date) and later synchronize them, the history branches, as shown
in [@fig:branch]). Now the version ordering has multiple maximal elements (we
call these *heads*). This we shall consider the definition of a conflict state,
which will be announced to the user.

After the user resolves the conflict, a new version (marked $z$ in the figure)
is created with all the previously conflicting versions as parents. Now there
is again a unique head and thus no conflict. After another resynchronization,
the resolution is spread to $B$, which automatically checks out $z$ instead
of either $w_1$ or $w_2$.

As noted above, this is very similar to git branching and merging, with several
differences:

  * Versioning is done per file instead of the whole repository. This allows
    resolving conflicts individually and leaving some unresolved for a later
    time.
  * Branching is implicit. It works as if whenever you were trying to do
    a non-fast-forward push in git, instead of the remote rejecting it, a new unnamed
    branch would be automatically created. This allows synchronization in
    the presence of conflicts and delayed conflict resolution.

![History branching during a conflict\label{branch}](img/branch.pdf){#fig:branch}

File locations are versioned independently from content, so that one can edit the
file in one store and rename it in other without this constituting a conflict
(this fullfills requirement \#5).

Two kinds of conflicts can arise when dealing with FLVs:

  * An **identity crisis** conflict happens when the FLV graph for a given FOB
    has multiple heads (i.e., we try to assign multiple different locations to
    a file). This is similar to a FCV conflict but less severe because it cannot
    lead to data loss. Currently we just give precedence to the FLV with the newest
    timestamp and output a warning.
  * A **pigeonhole conflict** happen when two head FOBs try to claim the same
    location. This is currently resolved by appending a unique suffix to each
    of the file names.

Storage records use the same parent-version mechanism but with differrent semantics.
Whenever a new SR is created, its parents are all the current SR heads of a given
FOB (regardless of from which store they are). This gives a partial ordering on
the SRs. This is purely for informative purposes. SR heads have no special meaning,
multiple SR heads are not considered a conflict or in any way an unusual state.

### Alternative versioning: vector clocks

As an alternative to explicit git-like parent version pointers, we could use
*vector clocks* for partially ordering versions.
This is a now almost universally known mechanism for versioning in distributed
systems, discovered independently by two teams in 1988 \cite{vclock1}\cite{vclock2}.

Their main
advantage is that we do not need to maintain information about previous versions.
Instead, it suffices to remember a vector of $s$ integers (where $s$ is the number
of stores in the realm) for each head version. The partial ordering between any
two versions can be determined by just looking at their vectors, without any additional
information. As we expect $s$ to be small and infrequently changing, this seems
to be fairly efficient.

The main reason to use for an explicit version graph is to keep a permanent record
of changes made to a file for auditing purposes.
This is useful when dealing with potentially compromised stores. When a file
contains unexpected data, you can look up which stores modified
it and when. The version graph can be made into a Merkle DAG (which works exactly
the same as a Merkle tree \cite{mtree}, only it is a generic DAG instead of a tree)
to prevent anyone from rewriting history. This is exactly the same thing that git does
with commits. \cite{gitobj}

### Working versions

Creating new FCVs is expensive. Not only additional versions increase metadata storage
requirements but we also have to compute a hash of the file's content, which is slow
and creates unnecessary I/O and CPU load on the system. If some process writes a few kB
into a $4\,$GB file every second (think disk images and large databases), we definitely
do not want to read the whole file and compute a hash every time. Not to mention that
computing a hash of a file that can change at any moment is riddled with race conditions,
which have to be handled, increasing the price even more.

To overcome this, whenever a local change to a file is detected, a so-called *working FCVs*
is created. This is a special FCV with the content hash field left empty. This version
normally participates in metadata synchronization, to let the other stores know you have
a new version of the file.

Whenever you want to synchronize the contents of the file with another store
(see chapter \ref{chap:datasync} for details on that),
a full FCV is created with the working FCV as a parent. This is rather cheap because
during data synchronization, we have to read the whole file and deal with race conditions
anyway.

Storage records are never created for working FCVs. The only store that can ever have
the data for a working FCV is the one that created it. Whenever the data is transferred
to another store, a full FCV is created to represent the transferred version of the file.

A working FCV never has another working FCV as a parent. When the local head already is
a working FCV and the file is further modified, no new FCVs are created, the current working
FCV is simply re-used to represent the newer modified version. A consequence of this is
that one cannot reliably determine the file's last modification time from a working FCV
timestamp because the FCV is created upon the first of a series of local modification.
While a last modified timestamp would be a nice information to have in the metadata, we
consider this a small price to pay for less version bloat.

### Placeholder inodes

One of the major goals of Filoco is to present the user with an unified view of their
data, no matter where they are physically stored. This means first and foremost
a\ unified directory tree. This begs the question of how to represent files for which
we have no data in the local file system.

We could omit them completely and offer some specialized tools (called perhaps `filoco-ls`,
`filoco-tree`, etc.) to list the locally missing files. However, this seems rather inconvenient.
We opted for a different method, and that is to represent them with a special kind of
inode. The best choice seems to be a broken symlink, i.e., one with a nonexistent target
(`/!/filoco-missing`) in our case.

This has several advantages:

  * The user can see the missing files with all the filesystem access tools they are
    used to, from CLI tools to graphical file managers, search tools, shell scripts, etc.
    All of them will give the same consistent view of the global directory tree.
  * The user can manupulate (especially move, rename and delete) locally missing files
    using any tools of their chosing: command-line `mv`, file managers, mass rename
    tools, shell scripts or custom programs in any language.
  * Many programs visualize broken symlinks in a way that symbolizes the concept of
    "missing". `ls` shows it in red, some GUI programs will show a cross mark or warning
    icon, etc.
  * When a program shows symlink targets (as `ls -l` does, or some file managers in the
    status bar), the user sees the informative string "filoco-missing".
  * When trying to access the file programmatically, one gets the correct error code,
    namely `ENOENT` ("No such file or directory"), the same error as returned for nonexistent
    file names.
  * The chosen target `/!/filoco-missing` offers one more advantage: the `/!`\ directory
    is unlikely to exist on anyone's file system. Thus when one tries to open the symlink
    for writing (e.g. using `echo x >some-missing-file`), they also get an error because
    the target cannot be created. If we used a relative target such as
    `filoco-missing`, a file named `filoco-missing` would be silently created upon the
    write attempt, turning the symlink into a non-broken one.

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
point, it will send all those objects all over again. We will call this the *indirect
synchronization problem*.

Instead, we will use a stateless approach. We want a protocol that allows two nodes to
efficiently compute the intersection $O_A ∩ O_B$ without any prior mutual information
($A$ knows only $O_A$ and $B$ knows only $O_B$ at the start of the exchange).

This is a known problem called the *Set Reconciliation Problem* \cite{setrec}. It could
be formally stated as follows. Let $U=\{0,1\}^\ell$ be a universe of $\ell$-bit strings
for some fixed $\ell$. Alice has an arbitrary set $A ⊆ U$. Bob likewise has a set $B ⊆ U$. 
At the beginning, they know nothing about each other's sets. We want to find a\ protocol
that allows Alice to compute the set difference $A \setminus B$ and Bob to compute
$B \setminus A$.

For our use case, we shall assume that both sets $A$ and $B$ similar in size:
$n := |A| ≈ |B|$, and significantly larger than their respective differences,
which we shall for the sake of simplicity also consider similar in size:
$n \gg c := |A \setminus B| ≈ |B \setminus A|$. The asymmetric case is not much
more interesting. The number $c$ represents the number of "changes" (represented
by new objects being created) made on one node that need to be synchronized
to the other.

There are several ways of measuring the efficiency of different protocols, all
expressed as a function of $n$, $c$, $\ell$, and any parameters of the protocol.

  * Communication complexity, i.e., the total number of bits transferred in both
    directions.
  * Number of rounds of communication. This is important because it determines
    the number of network round trips required. And especially in mobile networks,
    latency is often a greater concern than bandwidth -- the RTT on a 3G connection
    with suboptimal reception can be $500\,$ms or more.
  * Computational time on each side. Without any precomputation, this would
    have to be at least $Ω(n)$ because of the need to at least read the input sets.
    As $n$ is presumed to be large compared to $c$ and the sets will probably be
    stored on disk, we would prefer to have a data structure that can efficiently
    answer queries about the set needed by the reconciliation protocol -- ideally
    in a time dependent only on $c$ and not $n$ (or maybe on something like
    $\log n$ at worst).

We will be primarily interested in the expected (as opposed to worst-case) values
of these complexities. This is because the elements in our sets are random
(either pseudorandomly generated or cryptographic hashes) and we only communicate
with authorized peers so we do not have to worry about adversarial inputs.

### Divide and conquer

A rather obvious solution to the set reconciliation problem
and one of the first described \cite[alg. 3.1]{setrec} is a simple
divide-and-conquer approach. First, let's assume that the elements in the sets
to be from a uniform probability distribution. If they are not, we first process
them by a hash function and apply the rest of the protocol on the result.

First, we need a way to compare two sets $X$ and $Y$ possessed by Alice and Bob,
respectivelly. This is simple: Alice computes a value $\textsc{Digest}(X)$
representing the set. This value should be the same for equal sets and with high
probability different for inequal sets. A simple implementation of \textsc{Digest}
would be to compute a\ cryptographic hash of the concatenation of all the elements
of $X$. Now simply Alice sends $\textsc{Digest}(X)$ to Bob and Bob sends
$\textsc{Digest}(Y)$ to Alice. If they get a value equal to what they sent, the
sets are the same.

From this, a divide-and-conquer reconciliation algorithm is glaringly obvious
(alg. \ref{alg:recon1}).

\begin{algorithm}
  \caption{Basic divide-and-conquer algorithm for set reconciliation
    \label{alg:recon1}}
  \begin{algorithmic}[1]
    \Procedure{Recon1}{$A, i=0$}
      \State $D_A \gets \textsc{Digest}(A)$
      \State \textsc{Send}($D_A$)
      \State $D_B \gets \textsc{Recv}()$\Comment{The other side's digest}
      \If{$D_A=D_B$}
        \State \Return{$∅$}
      \ElsIf{$A = ∅$}
        \State \Return{$∅$}
      \ElsIf{$D_B = \textsc{Digest}(∅)$}
        \State \Return{$A$}\Comment{Other side's set is empty, need to send everything}
      \ElsIf{$i=\ell$}
        \State \Return{$A$}
      \Else
        \State $A_0 \gets \{ x ∈ A \:|\: x_i = 0 \}$\Comment{All the elements with $i$-th bit zero}
        \State $A_1 \gets \{ x ∈ A \:|\: x_i = 1 \}$
        \State \Return{$\textsc{Recon1}(A_0, i-1) ∪ \textsc{Recon1}(A_1, i-1)$}
      \EndIf
    \EndProcedure
  \end{algorithmic}
\end{algorithm}

This can be easily visualized if we look at the strings of each side as an (uncompressed)
binary trie. If $v_s$ is a vertex of the trie representing the prefix $s$, let $A_s$
and $U_s$ denote the subsets of $A$, resp. $U$ restricted to elements with this prefix.

Recursion then simply walks this trie. Both parties start in the root $v_ε$. If $A_ε=B_ε$,
the sets are the same and algorithm ends. If $A_ε$ or $B_ε$, one side's set is empty and
the other party has to send the whole set. In this case, recursion also stops at both sides.
If $A_ε$ and $B_ε$ are nonempty and different, both sides recurse to $v_0$ and $v_1$. The
same is repeated for every vertex visited. Only in case of a leaf, no recursion is done
because each set contains at most one element so the set difference can be computed
trivially.

From this description it is also clear that the recursion tree looks exactly the same
on both sides: Alice and Bob visit the same trie vertices in the same order; Alice
recurses exactly when Bob recurses and stops recursion if and only if Bob stops recursion.
Because of this, it is sufficient to send only the subset digests in the vertex visit order,
without any further labelling.

#### Complexity

##### Communication complexity

How does the protocol fare on the different complexity measurements? We recurse from
vertex $v_s$ iff (1) there is at least one new leaf under this vertex in Alice's trie,
(2) there is at least one leaf of any kind (new or old) under this vertex in Bob's trie
(or vice versa). These events are independent. Let's examine the probability of the
first condition $p_1 := \P[|A_s \setminus B_s| ≥ 1]$. Because leaves are uniformly
distributed, the expected number of new leaves under $v_s$ is $\E[|A_s \setminus B_s|]
= c·|U_s|/|U| = c/2^d$, where $d$ is the depth of the vertex. By Markov's inequality,
$p_1 = \P[|A_s \setminus B_s| ≥ 1] ≤ \min(\E[|A_s \setminus B_s|], 1) = \min(c/2^d, 1)$.

Similarly, we can estimate $p_2 := \P[|B_s| ≥ 1] ≤ \min(n/2^d, 1)$. Therefore, the
probability of recursing from a vertex is $p ≤ 2 p_1 p_2 ≤ 2\min(c/2^d, 1)\min(n/2^d, 1)$.
We multiply by two because the new leaf can be on either side and we use the union bound.

For the first $\lg c$ levels of the tree (which we shall call *slice I*), the estimated
value of $p$ is 2, which we shall cap to 1. We expect the recursion tree in this slice
to be very close to a full binary tree. The total expected number of vertices recursed
from in the slice thus is $\E[K_{\mathrm{I}}] ≤ 2^{\lg c+1} = 2c$.

For the next $(\lg n - \lg c)$ levels (slice II), our estimate is $p ≤ 2 c/2^d$.
The expected number of vertices visited on each of these levels is $\E[k_d] =
2^d·p ≤ 2^d · 2c/2^d= 2c$.  Thus in total we expect to recurse from
$\E[K_{\mathrm{II}}] ≤ 2c(\lg n - \lg c)$ vertices in total on these levels.

For the remaining $\ell - \lg n$ levels (slice III) at the bottom of the tree, we estimate
$p ≤ 2cn/2^{2d}$. Thence again, $\E[k_d] ≤ 2cn/2^d = 2c/2^{d'}$, where $d' := d - \lg n$
is vertex depth measured from top of the slice. Totalling over the slice
we get $\E[K_{\mathrm{III}}] =  2c(1 + 1/2 + 1/4 + \dots) < 4c$. In this slice even
elements common to $A$ and $B$ are becoming increasingly sparse so any recursion
soon dies out because it hits an empty set on the other side.

The total expected number of vertices recursed from is simply $\E[K] = \E[K_{\mathrm{I}}] 
+ \E[K_{\mathrm{II}}]  + \E[K_{\mathrm{III}}]  ≤ 2c + 2c(\lg n - \lg c) + 4c
= 6c + 2c(\lg n - \lg c)$. The total number of vertices visited is simply twice this
number, i.e., $12c + 4c(\lg n - \lg c)$ and the total number of bytes transmitted
is $cg(3 + \lg n - \lg c)$, where $g$ is the digest size (we send two $g/8$-byte
digests per visited vertex).

##### Communication rounds

Now we would like to estimate the number of communication rounds. If the algorithm
were implemented as described in algorithm \ref{alg:recon1}, each visited vertex would
cost us one round. However, the algorithm can be easily modified to perform
a breadth-first traversal of the original recursion tree. Then we can send
digests from all active vertices on a given level in a single round and the number
of rounds needed is exactly the depth of the recursion tree.

This modification
is shown as algorithm \ref{alg:recon1b}. It should be easy to see that this algorithm
straightforwardly maps to the original.

\begin{algorithm}
  \caption{Breadth-first modification of the divide-and-conquer reconciliation algorithm
    \label{alg:recon1b}}
  \begin{algorithmic}[1]
    \Procedure{Recon1-BFS}{$A$}
      \State $active \gets [\,ε\,]$\Comment{ordered list of active vertices on cur. level}
      \State $C \gets ∅$\Comment{the local changes ($A \setminus B$)}
      \While{$active ≠ [\,]$}
        \State $d_A \gets \left[\, \textsc{Digest}(A_s) \,|\, s ∈ active \,\right]$
        \State \textsc{Send}($\|\,d_A $)\Comment{concatenation of all active vertices' digests}
        \State $d_B \gets \textsc{Recv}()$ split into digest-sized chunks
        \State $next \gets [\,]$
        \For{$0 ≤ i < |active|$}
          \If{$d_A[i]=d_B[i]$}
            \State do nothing
          \ElsIf{$A_s = ∅$}
            \State do nothing
          \ElsIf{$D_B = \textsc{Digest}(∅)$}
            \State $C \gets C ∪ A_s$
          \ElsIf{$i=\ell$}
            \State $C \gets C ∪ A_s$
          \Else
            \State append $s\,\|\,0$ and $s\,\|\,1$ to $next$
          \EndIf
        \EndFor
        \State $active \gets next$
      \EndWhile
      \State \Return{$C$}
    \EndProcedure
  \end{algorithmic}
\end{algorithm}

We know an upper bound on the expected number of vertices $\E[k_d]$ visited on each
level of the tree. From this, we can once again use Markov's inequality to estimate
the probability as least one vertex is visited on that level. The expected number
of rounds is then simply the expected number of levels on which ve visit at least
one vertex. We will do this again per slice.

For slice I, we expect to visit all levels, i.e. $r_{\mathrm{I}} ≤ \lg c$.
For slice II, $\E[k_d] = 2c > 1$, so again we expect to visit all levels,
$\E[r_\mathrm{II}] ≤ \lg n - \lg c$. With slice III, we are finally getting
somewhere. We have shown that $\E[k_d] ≤  2c/2^{d'}$, where $d'$ is vertex
depth relative to the top of slice III. Thus the probability
of visiting at least one vertex on a level is bounded by $\min(2c/2^{d'}, 1)$.
For $d' ≤ 1+\lg c$, this bound is equal to one. For all the subsequent levels,
the probabilities form the geometric sequence with sum $1+1/2+1/4+… < 2$. Thus
the expected number of levels visited $\E[r_\mathrm{III}] ≤ 3+\lg c$.

When we put this together, we can bound the expected number of communication
rounds by $\E[r] = \E[r_\mathrm{I}] + \E[r_\mathrm{II}] + \E[r_\mathrm{III}] ≤
3 + \lg n + \lg c$.

Note that our protocol is not a request-response protocol. Instead, communication
in both directions happens at the same time. The message we recieve in round $i$
is not a reply to the message we sent in round $i$ but the one we sent in round
$i-1$. This means that the number of network round trips required is half the number
of cummunication rounds, as shown in [@fig:rounds].

![Communication rounds vs network roundtrips](img/rounds.pdf){#fig:rounds}

We should also realize the importance of the breadth-first optimization here.
The naive recursive implementation would require as many rounds as vertices visited, 
$12c + 4c(\lg n - \lg c)$. This would require hundreds to thousands of roundtrips
for moderate values of $c$, which would result in a total time of several seconds
to several minutes(!) depending on network quality.

##### Computational time

If we use the naive digest function suggested in above, computional complexity
will be simply too horrendous to be even worth estimating, definitely at least $Ω(n)$.
Instead, we can make the trie on each side into a Merkle tree \cite{mtree}: we define
the digest of any nonempty set $A_s$ corresponding to vertex $v_s$ as a cryptographic
hash of the digests of two child sets in the trie ($\|$ is the string concatenation operator):
$$\textsc{Digest}(A_s) := \begin{cases}
0\dotsm0 &\text{if }A_s = Ø\\
\textsc{Hash}(\textsc{Digest}(A_{s\, \| \,0})\, \| \,
              \textsc{Digest}(A_{s\,\|\,1}))&\text{otherwise}
\end{cases}$$

We can store digests for all non-empty vertices on disk.
This allows us to get any digest in $\OO(1)$ expected time if we use a hashed store 
or $\OO(\lg n)$ worst-case time if we use a tree-based structure (e.g. a typical
SQL database with B-tree based indices, which is the case for the SQLite database
used by Foloco). For an tree-based database, we get total computational time
$\OO(c \lg^2 n)$ for one reconciliation.
When adding a new object to the set, we must update the hashes of all of its $\ell$
ancestors, which can be done in time $\OO(\ell\lg n)$.

### Divide and conquer with pruning

From the estimates given in the previous section, we can infer that the recursion
tree looks approximately as shown in [@fig:rectree] for $c=4$ (4 new objects on
each side, 8 changes total).

![Recursion tree of \textsc{Recon1} (algorithm \ref{alg:recon1})](img/rectree.pdf){#fig:rectree}

Slice I is (close to) a full binary tree, slice II consists of mostly of separate
non-branching paths (except for dead-end side branches that immediately terminate
because they contain no changes), one for each change. Slice III contains
short tails of these paths (expected length bounded by a constant) before recursion
terminates.

This seems rather wasteful. Most of the algortihm is spent walking along the paths
in slice II, always comparing digests of sets that differ by only one element.

What we would like is to be able to immediately detect that two sets differ only
in one element and ideally also reconstruct that element. The XOR function immediately
springs to mind. We can define the digest as
$$\textsc{Digest}(\{a_1, …, a_k\}) := h(a_1) ⊕ \dotsm ⊕ h(a_k),$$
where $h$ is a cryptographic hash function. We need $h$ because if we XORred the original
strings
(which determine trie location), digests of neighbouring nodes would be highly correlated.

Now when we have two sets $A$ and $B$ such that $A ∆ B = \{e\}$ then $δ :=
\textsc{Digest}(A) ⊕ \textsc{Digest}(B) = h(e)$. However if $|A ∆ B| > 1$, the
$δ$ a useless number. We need to determine which of these cases ocurred. The
party with the extra element can simply look up $δ$ in a reverse lookup table
$h(x) → x$.

However, this might yield a false positive. What is the probability of that happening?
Because we presume values of $h$ to behave as independent uniformly distributed random
variables, the digests of any two sets differing in at least one element should behave
as independent random uniformly distributed random variables. Thus the probability of
a accidental collision of $δ$ for a nontrivial difference with one specific element
is close to $1/2^g$, where $g$ is the digest size. The probability of collision with any
element can be estimated using the union bound as $p ≤ n/2^g$. If we want this to be
as collision-resistant as a $g$-bit hash function, we need to use a longer hash, specifically
one with $g' := g + \lg n$ bits.

If the extra element is on the other side, we must recurse for now and the other party
will inform us in the next round that we should stop any further recursion.

There is an alternative to simply using a longer hash function, and that is to add
a checksum to each element's hash as follows:
$$\textsc{ElemDigest}(x) := h(x) \,\|\, h(h(x)),$$
$$\textsc{Digest}(\{a_1, …, a_k\}) := \textsc{ElemDigest}(a_1) ⊕ \dotsm ⊕ 
\textsc{ElemDigest}(a_k).$$


This brings the same level of fake positive resistance (probability $1/2^g$ per
comparison) at the cost of more extra bits ($g$ instead of $\lg n$). However, now
both parties can independently detect that $|A ∆ B|=1$ by checking if
$h(δ_1) = δ_2$ (where $δ_1$ and $δ_2$ are the two halves of the $δ$ string)
and stop recursion immediately.
This saves one roundtrip and simplifies implementation. It is not clear which approach
is better, both have their mertis.

The second variant (with another checksum hash) is summarized as algorithm
\ref{alg:recon2} and implemented in Filoco.

\begin{algorithm}
  \caption{Divide-and-conquer set reconciliation with pruning
    \label{alg:recon2}}
  \begin{algorithmic}[1]
    \Procedure{Digest}{$A$}
      \State \Return{$h(A)\,\|\,h(h(A))$}
    \EndProcedure
    \Procedure{Recon2}{$A, i=0$}
      \State $D_A \gets \textsc{Digest}(A)$
      \State \textsc{Send}($D_A$)
      \State $D_B \gets \textsc{Recv}()$
      \State $\delta \gets D_A ⊕ D_B$
      \State split $\delta$ into two halves $\delta_1$ and $\delta_2$
      \If{$D_A=D_B$}
        \State \Return{$∅$}
      \ElsIf{$A = ∅$}
        \State \Return{$∅$}
      \ElsIf{$D_B = \textsc{Digest}(∅)$}
        \State \Return{$A$}\Comment{other side's set is empty, need to send everything}
      \ElsIf{$h(δ_1) = δ_2$}\Comment{$|A △ B| = 1$}
        \If{$∃ x ∈ A$ with $h(x)=δ_1$}\Comment{we have the extra element}
          \State \Return{$\{x\}$}
        \Else\Comment{they have the extra element}
          \State \Return{$∅$}
        \EndIf
      \ElsIf{$i=\ell$}
        \State \Return{$A$}
      \Else
        \State $A_0 \gets \{ x ∈ A \:|\: x_i = 0 \}$\Comment{All the elements with $i$-th bit zero}
        \State $A_1 \gets \{ x ∈ A \:|\: x_i = 1 \}$
        \State \Return{$\textsc{Recon2}(A_0, i-1) ∪ \textsc{Recon2}(A_1, i-1)$}
      \EndIf
    \EndProcedure
  \end{algorithmic}
\end{algorithm}

A similar approach has been independently discovered earlier by Minsky and Trachtenberg
\cite{partrecon}. They use a scheme based on polynomials over finite fields for pruning
branches where the symmetric difference is small. \cite{basic-recon} Our solution achieves
comparable asymptotic bounds and practical results (even though perhaps with worse constant factors)
and is much simpler both conceptually and to implement. They also describe using XOR
for the case of $|A △ B| = 1$. \cite[protocol 1]{basic-recon} However, they XOR the
original bitstrings from the set instead of their hashes, which makes this technique
unsuitable for branch pruning because all the elements under a vertex with depth $d$
have the first $d$ bits in common and therefore the first $d$ bits of the XOR are all
ones or all zeroes, depending on the number of elements.

#### Complexity

Intuitively, pruning should cut off all the boring branches in slices II and III and
leave us with $\OO(\lg c)$ expected depth of the recursion tree, which corresponds to
communication complexity $\OO(c\lg c)$. Let's prove that.

<!-- In the pruning version, we recurse from a vertex only if there are at least two changes
(in total on both sides) underneath it. There are a few other conditions (for example,
recursion stops if the subset on one side is empty, even if the other party has two
changes), which we shall ignore because we are doing an upper bound.

The probability at least two changes are found under a vertex can again be estimated
using Markov's inequality: $\P[|A_s △ B_s| ≥ 2 ] ≤ \min(\E[|A_s △ B_s|]/2, 1) =
\min(2c/2^d/2, 1) = \min(c/2^d, 1)$.
The arguments are mostly the same as in the previous section. There are $2c$ total changes,
we assume them to be independent and uniformly distributed, wherefrom the expected count
is straightforward. Just as a reminder, $d$ is the depth of the examined vertex from the root.
-->

##### Communication complexity

We will have to use a slightly different estimation method. The recursion tree
has at most $c$ leaves, with one change under each. For each change $w$ (a trie
leaf present on one side but not other), we shall estimate the length of the
recursion branch leading to that node.  We shall consider all the trie
ancestors of the change and for each of them compute the probability that we
recursed from that vertex.

We recurse from a vertex only if there are at least two changes (in total on
both sides) underneath it. There are a few other conditions (for example,
recursion stops if the subset on one side is empty, even if the other party has
two changes), which we shall ignore because we are doing an upper bound. This
means that at least one of the $2c-1$ changes other than the one we are
currently examining must lie under this vertex. Because we consider changes to
be independent and uniformly distributed, the probability of this happening
can be easily estimated, once again using Markov's inequality:
$$p_d := \P[|A_s △ B_s|- \{w\}] ≤ \min(\E[|A_s △ B_s|- \{w\}],1)
= \min(\E[|A_s △ B_s|]- 1, 1) =$$
$$= \min(c/2^d - 1, 1) ≤ \min(c/2^d, 1),$$
where $d$ is the depth of the ancestor.

Let's look at the values of $p_d$ by slice. In slice I, $2^d ≤ c$ and the estimate
maxes out at 1. For slices II and III, we get $p_d \ge c/2^d = 1/2^{d'}$, where
$d' := d - \lg c$ is depth relative to the top of slice II. Now we can easily estimate
the length of a recursion tree branch:
$$\E[L_w] ≤ ∑_{d=0}^\ell p^d = \lg c + 1 + 1/2 + 1/4 + \dots ≤ 2 + \lg c.$$
The expected number total number of vertices recursed from is bounded by the sum
of the recursion branch lengths (we count many vertices several times), which we
can estimate using linearity of expectation:
$$\E[K] ≤ \E\left[∑_{w ∈ A △ B} L_w\right] = ∑_{w ∈ A △ B} \E[L_w] ≤ 2c·(2 + \lg c) = 4c + 2c\lg c.$$ This corresponds to $2gc + gc\lg c$ transferred bytes for a $g-bit$ hash
(we send two hash values per vertex, there are at most twice as many vertices visited
as recursed from).

##### Communication rounds

In a similar manner, we can estimate the number of communication rounds,
again presuming this algorithm is first transformed to a breadth-first
version in a manner similar to algorithm \ref{alg:recon1b}. The modified
version is not shown here but can be found in attachment 1, both as
a standalone experiment (in the file `experiments/mdsync/hybrid.py`) and as a part
of Filoco proper (classes `TreeMDSync` in `mdsync.py` and `SyncTree` in `store.py`).

For each level of the trie, we will examine the probability recursion gets to this
level. We have at most $c$ recursion branches, each of them traversing level $d$
with probability $p_d < c/2^d$. Using the union bound, the probability of at least
one branch traversing this level is $q_d < c^2/2^d$. For $d < 2\lg c$, this bound
is larger than one. For further levels, it forms a geometric sequence. Thus the expected
depth of the recursion tree is $\E[r] = 1 + 2\lg c + 1 + 1/2 + 1/4 + \dots < 3 + 2\lg c$.
This is the number of communication rounds required by the breadth-first variant of
algorithm \ref{alg:recon2}.

##### Computational time

As for computational time, we can once again organize the digests into
a Merkle-like tree stored on disk and incrementally updated. Only this time each
vertex computes a XOR instead of a cryptographic hash. Thus we get the same
$\OO(\lg n)$ query time and $\OO(\ell \lg n)$ update time. The total
computational time is then $\OO(c\lg c\lg n)$.

As a further optimization, we notice that if the $\ell$ is larger than $2 \lg n$,
the bottom levels will be rarely ever used during synchronization. We can thus
further optimize by only storing the to $α\lg n$ for an empirically chosen constant
$1 ≤ α ≤ 2$. The missing levels can be computed on-the-fly (for example if
the set items are stored in a SQL database that supports range queries, we can simply
enumerate all the elements under a vertex because they form a contigous segment).

This changes storage requirements to $\OO(n\lg n)$ and update time to $\OO(\lg^2 n)$.

For clarity, we summarize the efficiency of both algorithms in [@tbl:setrec-comp]
(for 128-bit digests).

Experimental results were produced by the
`experiments/mdsync/prune.py` script in attachment 1.

The "total roundtrip time" and "total transfer time" are synchronization time
estimates based on roundtrip numbers and transfer total from experimental
protocol simulations (no actual time measurements were performed). These are
computed for a hypothetical low-quality network with $1\,$Mbps symmetric
throughput and $500\,$ms RTT (for example a 3G connection with subpar
reception).  The total synchronization time will be probably be close to the
maximum of the two estimates. For any network with significantly better
parameters the times will become imperceptible.

Metric                                              Naive D\&C              Pruning D\&C
---------------------------------  ---------------------------  ------------------------
total bytes transferred            $128c(3 + \lg n - \lg c)$    $128c(2 + \lg c)$
$\quad$ for $n=2^{20}$             $128c(23 - \lg c)$           $128c(2 + \lg c)$
$\qquad$ for $c=16$ (theor.)       $38\,$kB ($1.2\,$kB p.ch.)   $12\,$kB ($0.3\,$kB p.ch.)
$\qquad$ for $c=16$ (exper.)       $51\,$kB ($1.6\,$kB p.ch.)   $11.1\,$kB ($0.3\,$kB p.ch.)
$\qquad$ for $c=1024$ (theor.)     $1.6\,$MB ($0.8\,$kB p.ch.)  $1.5\,$MB ($1.5\,$kB p.ch.)
$\qquad$ for $c=1024$ (exper.)     $1.6\,$MB ($0.8\,$kB p.ch.)  $650\,$kB ($0.3\,$kB p.ch.)
communication rounds               $3 + \lg n + \lg c$          $3 + 2\lg c$                       
$\quad$ for $n=2^{20}$             $23 + \lg c$                 $3 + 2\lg c$                       
$\qquad$ for $c=16$ (theor.)       27                           11
$\qquad$ for $c=16$ (exper.)       8                            4
$\qquad$ for $c=1024$ (theor.)     33                           23
$\qquad$ for $c=1024$ (exper.)     9                            8
computational time                 $\OO(c\lg^2 n)$              $\OO(c\lg c\lg n)$
disk storage                       $\OO(n\lg |U|)$              $\OO(n\lg |U|)$
update time                        $\OO(\lg |U|\lg n)$          $$\OO(\lg^2 n)$$
total roundtrip time (proj.)
$\qquad$ for $c=16$                $2\,$s                       $1\,$s
$\qquad$ for $c=1024$              $2.25\,$s                    $2\,$s
total transfer time (proj.)
$\qquad$ for $c=16$                $0.4\,$s                     $0.08\,$s
$\qquad$ for $c=1024$              $12.8\,$s                    $5.2\,$s
---------------------------------  ---------------------------  ------------------------

: Comparison of described set reconciliation algorithms {#tbl:setrec-comp}

In the model situation of $n=2^{20}$ and $c=16..1024$, both algorithms seems comparable
within a factor of two, both theoretically and experimentally. However, the theoretical
bounds are not tight enough to distinguish between the two algorithms for these
parameter values, thus we should give more credence to the experimental results.

Please note that all the transfer and time estimates cover only the process of determining
which objects each side is missing. After this, we must transfer the serialized objects
themselvers; this is not included in our estimates.

Both algorithms seem usable for our
application. However, the pruning algorithm performs better, is only slightly
more complex and offers much greater scalability because its communication complexity
and number of rounds do not depend on $n$.

## Per-Origin Sequential Streams

Upon further reflection, we actually do not need to solve the fully general set
reconciliation problem. Our instance is rather special in one key factor: each
object is only ever created once, in one store. Therefore, the assumption of set
reconciliation that there is no prior communication between the parties is not
true. If two stores share an object, there must have been prior communication
between each of them and the object's originating store, albeit possibly indirect.

There are two more important special properties: (1) we always perform full
synchronization (unless the synchronization process is interrupted), partial
synchronization is not supported; (2) the number of stores in a realm is expected
to be small (in the order of tens at most).

If we put all these facts together, we can devise a synchronization scheme
both simpler and more efficient than the described reconciliation algorithms.

The idea is simple: instead of considering all the object a store has as one
big set, we will split them into several sets based on their originating
stores and solve the reconciliation problem for each of these sets separately.

Now we have a different task: several nodes have copies of a set, which
they synchronize with each other in a disorganized peer-to-peer fashion. But only
one node ever adds new elements to the set! All other nodes must have got their
elements from this originating node, directly or indirectly.

This is rather simple to solve: the originating node will assign created objects
sequence numbers as they are created. All nodes will keep their sets sorted by
these seqence numbers, essentially transforming the problem into one of sequence
reconciliation.

Whenever Alice and Bob want to reconcile their sequences, they simply compare
their maximum sequence numbers $m_A$ and $m_B$. If $m_A > m_B$, Alice sends all
her objects with sequence number greater than $m_B$ to Bob (who clearly does
not have them) in increasing sequence number order (this is important). Bobs
appends them to his sequence in the order he recieves them and sends nothing to
Alice.  If $m_A < m_B$, the same happens in the opposite directions.

We claim this is sufficient to synchronize their sets/sequences. Why? A simple
invariant $I$ holds: the sequence of objects possesed by any node is always a
prefix of the originating node's sequence. We can prove $I$ by induction. At
the beginning, all sequences are empty and $I$ holds trivially. Two kinds of events
can happen:

  * The originating store adds an element at the end of the seqence. This
    clearly preserves $I$.
  * Two nodes $A$ and $B$ (for which $I$ holds) synchronize their sequences
    $s_A$ and $s_B$.
    We can assume without loss of generality that $m_A ≥ m_B$. At the beginning
    $s_A$ and $s_B$ are prefixes of the originator's sequence $s_O$. Because
    $s_B$ is a shorter prefix, it is also a prefix of $s_A$. After each object
    transferred, $s_B$ becomes a longer prefix of $s_A$, and thus still a prefix
    of $s_O$. $s_A$ is unchanged.
    
This yields a simple synchronization algorithm for complete metadata synchronization:

\begin{algorithm}
  \caption{Reconciliation using per-origin sequential streams
    \label{alg:originseq}}
  \begin{algorithmic}[1]
    \Procedure{RecvObjects}{}
      \While{other side has not signalled EOF}
        \State $o \gets \textsc{RecvSerialized}()$
        \State add $o$ to the local database (at the end of $originator(o)$'s sequence)
      \EndWhile
    \EndProcedure
    \Procedure{SendObjects}{$M_A,M_B$}
      \For{every store $s$ present in both $M_A$ and $M_B$}
        \If{$M_A[s] > M_B[s]$}
          \For{every object $o$ with $originator(o)=s$ and $seq(o)>M_B[s]$}
            \State \textsc{Send}(\textsc{Serialize}(o))
          \EndFor
        \EndIf
      \EndFor
    \EndProcedure
    \State $M_A = \{(id(s), maxseq(s)) \,|\, s\text{ store}\}$
    \State \textsc{Send}($M_A$)
    \State run \textsc{SendObjects}($M_A$, $M_B$) and \textsc{RecvObjects}()
           in parallel
  \end{algorithmic}
\end{algorithm}

Thus we can perform synchronization with only one roundtrip and
$\OO$(\#stores) bytes overhead in addition to whatever is required to transfer
the acual objects missing on the other side.

This leaves the question of why we bother with set reconciliation when a simpler
and more efficient solution exists. There are several reasons:

  * We actually discovered it much later than the general set reconciliation
    algorithms. This seems strange because at first sight, the idea seems rather
    trivial. But it is probably somehow evasive. Not only did we almost miss it;
    for example the leading open source synchronization tool Syncthing also uses
    a sequence numbering scheme but one that is slightly different and suffers
    from the indirect synchronization problem. \cite{bep}

  * Set reconciliation is an interesting problem by itself. That should be enough
    reason for anyone. It also has numerous other applications, both within
    file synchronization an elsewhere. For example, it has been used for delta
    transfer of files as a replacement of the established rsync algorithm
    \cite[sec. 4.1.2]{gentili}.

  * Assigning sequential numbers has some reliability issues described bellow.

Currently, both approaches are implemented in Filoco, with sequence numbering
being the default. The main reason is surprisingly not the difference in
reconciliation times but the need to keep the reconciliation trie on disk,
increasing both storage overhead and slowing down database updates.

### The problem with sequence numbers

Any attempt to assign sequential numbers is potentially problematic. It can happen
that Alice creates an object $o_1$, assigns it a sequence number $s$ and transfers it to Bob.
Then Alice suffers from a power loss before $o_1$ has been flushed to disk.
After reboot, she creates a completely unrelated object $o_2$, which nevertheless
gets assigned the same sequence number $s$, because the information about $s$ being
already taken has been lost. Now when Alice synchronizes with Bob, their maximum
sequence numbers for Alice-originated objects will be the same, namely $s$. Thus they
will mistakenly think their object sets are identical, despite Alice missing $o_1$
and Bob missing $o_2$.

Several things can be done about this. The simplest is to
flush (`fsync`) local changes to disk before every synchronization.

If we do not want to do that or do not trust the disk to reliably fullfil the
request (which is known to happen at times), we can instead check that the
common prefix is really the same on both sides.

For example we can store for each prefix of the local object sequence a XOR
of its object IDs. Upon synchronization, Alice and Bob exchange XORs of their
complete sequences, $x_A$ and $x_B$, in addition to their maximum sequence numbers
$m_A ≥ m_B$. Now Alice can compare $x_B$ to her prefix XOR of the corresponding
prefix ending with seqence number $m_B$. If they match, Bob really has the same
objects as are in her prefix and it is sufficient to send the remaining suffix.

Otherwise a different synchronization scheme must be used. For example, we could
perform a binary search on the sequence numbers to find the longest common prefix
by comparing corresponding prefix XORs. Then both sides can simply exchange the
remaining suffixes and merge them into their sequences, updating the neccesary
prefix XORs. Presumably the error has occured recently so the suffexes than need
to be fixed should not be long.



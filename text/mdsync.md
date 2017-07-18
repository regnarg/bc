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
    the FOVs form a Merkle tree \cite{merkle}. This ensures integrity of revision
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

5. It should be possible to rename or move a file in one store and edit it in
   another without this being considered a conflict.


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
    a non-fast-forward push in git, instead of rejecting it, a new unnamed
    branch would be automatically created. This allows synchronization in
    the presence of conflicts and delayed conflict resolution.

![History branching during a conflict\label{branch}](img/branch.pdf){#fig:branch}

### Working revisions

### Placeholders

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
  * Computational complexity on each side. Without any precomputation, this would
    have to be at least $Ω(n)$ because of the need to at least read the input sets.
    As $n$ is presumed to be large compared to $c$ and the sets will probably be
    stored on disk, we would prefer to have a data structure that can efficiently
    answer queries about the set needed by the reconciliation protocol -- ideally
    in a time dependent only on $c$ and not $n$ (or maybe on something like
    $\log n$ at worst).

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

For a realistic example with $n=2^{20}$, $c=1024$, and $g = 128$, we would get
$128(3 + 20 - 10) = 1664\,\mathrm{kB}$  transferred, that is approx. 800 bytes per change
(remember there are really $2c$ total changes).

Now we would like to estimate the number of communication rounds. If the algorithm
were implemented as described in algorithm \ref{recon1}, each visited vertex would
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
      \State $active \gets [ε]$\Comment{ordered list of active vertices on cur. level}
      \State $C \gets ∅$\Comment{the local changes ($A \setminus B$)}
      \While{$active ≠ []$}
        \State $d_A \gets \left[\, \textsc{Digest}(A_s) \,|\, s ∈ active \,\right]$
        \State \textsc{Send}($\|\,d_A $)\Comment{concatenation of all active vertices' digests}
        \State $d_B \gets \textsc{Recv}()$ split into digest-sized chunks
        \State $next \gets []$
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
3 + \lg n + \lg c$. With the example parameters above, this would be 33 roundtrips.
On a higher-latency network with RTT $250\,$ms, this would make the synchronization
time at least 8 seconds. Aven for a silngle change, this will still be 23 roundtrips
or almost 6 seconds (though this is only an upper bound).

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

We can store digests for all non-empty vertices on disk in a key-value database.
Then computing a digest is a constant time operation (we simply read it from
disk) and the expected computational complexity is the number of vertices visited,
i.e., $\OO(c\log n)$.
Adding a new object to the set takes time $\OO(\ell)$ because we need to update
the stored digests of all its ancestors.

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


#### Complexity

Intuitively, pruning should cut off all the boring branches in slices II and III and
leave us with $\lg c$ expected roundtrips. Let's prove that.

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


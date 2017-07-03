# Metadata Synchronization

## Metadata Model

### Local Filesystem Metadata

### Synchronized Metadata

### Metadata Storage

## The Set Reconciliation Problem

Our metadata is modelled as a set of immutable objects identified by unique IDs.
In order for two repositories $A$ and $B$ to synchronize their metadata, A should send
to B exactly the objects $A$ has but $B$ does not (an vice versa). If $O_A$ is the set
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

This is a known problem called the *Set Reconciliation Problem*.

### Reconciliation Trees

### Invertible Bloom Filters

### A Hybrid Solution

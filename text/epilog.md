\chapter*{Conclusion}
\addcontentsline{toc}{chapter}{Conclusion}

We set out with a goal of designing and implementing an efficient, scalable, robust,
flexible, and secure file synchronization (tool)kit for advanced users. Unsurprisingly,
we have stopped quite far from this mouthful of a goal, due to mainly time constraints.
We present a collection of solutions to some important subproblems lying on the way
to our target rather than a finished piece of software.

Despite the limited scope, we have touched on a fairly broad range of topics, from
filesystem architecture to kernel development and randomized algorithms.
The following are the most important original contributions of this work, in roughly
the order they appear in the text:

  * A mechanism for reliable rename detection during file system scanning, based on
    inode numbers and file handles. This in turn allows efficient synchronization
    of directory trees when directories with large subtrees are moved or renamed.
    A proof of concept implementation of this mechanism has been provided.

  * A mechanism for speeding up incrementally scanning a file system for changes
    about 2 times by accessing inodes using file handles in inode number order,
    including experimental measurements.

  * Use of the same mechanism to speed up recursive inotify watch setup up to
    10 times.

  * A patch to the Linux kernel that extends the fanotify change notification interface
    with the ability to report directory modification events (creating, renaming, moving
    and deleting directory entries), a feature for which there has been great demand
    for since the creation of fanotify in 2009 but almost no solution attempts.

  * The concept of placeholder inodes to represent files not available locally in a way
    that allows seeing and manipulating them with arbitrary file management tools.
    A partial proof of concept implementation has been provided.

  * An independently discovered simpler version of the \textsc{Partition-Recon} set reconciliation
    algorithm (called *divide and conquer with pruning* in our text) first described by
    Minsky and Trachtenberg in 2002 \cite{partrecon}. Elementary proofs of some complexity
    bounds, experimental simulations of the algorithm and a full implementation with on-disk
    storage of the digest tree have been provided.

  * A simple algorithm for peer-to-peer synchronization among a small set of nodes
    with single roundtrip overhead using per-origin sequence numbers.

From among our stated priorities, we ended up focusing mostly on efficiency, scalability
and robustness, especially with regard to the change detection and metadata synchronization
aspects of file synchronization, while touching only briefly and indirectly on file content
synchronization and security.

There is a lot of room for future work. Partly in putting all of the techniques described
here together into a complete tool suitable for daily use, partly in future research into
areas neglected here, especially content synchronization and security.

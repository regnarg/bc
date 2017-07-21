# Content Synchronization               {#chap:datasync}

After metadata synchronization, each store knows which files need to be updated.
Namely any files that the store hosts (or wants to host) and for which there
is a head FCV that the store does not have. Storage records will contain information
about where the data can be obtained.

When a store that can provide the right data is found and contacted, the transfer
itself can begin. There are many ways of transferring incremental file updates
over a network; this is a fairly well-researched problem.

The trivial solution is to simply send over the new version of the file. However, that
is fairly inefficient for when small changes are done to large files. As with metadata
synchronization, ideally, we would like the amount of transferred data to depend more
on the size of the change than on the size of the whole file. Techniques to achieve
this are generally called *delta transfer* algorithms.

Most delta transfer methods work by somehow splitting the file into blocks on both
sides. The blocks may be of fixed or varying size, aligned, unaligned or even
overlapping, the splitting may be identical or different on the two sides. Then
we the sending party must somehow learn which blocks are already present on the
receiver side as part of the old version of the file -- or in some cases, even blocks
from different files are reused.

That is especially useful in the absence of rename
detection because then after a rename, the target file can be reconstructed from
blocks of the source file without retransmitting the data over the network. However,
since we have reliable rename detection, we opted for doing delta transfers of each
file separately, isolated from the others. While cross-file block reuse might
still provide some optimizations because file systems often contain similar files,
these are less important and given the number of files we have to deal with,
considering all the blocks in all the files would be quite hard (although definitely
not impossible) to do efficiently.

When it is determined which blocks the receiving party is missing, we simply send 
them along with any instructions necessary to reassemble the whole file from both
old and new blocks.

Now the key questions are: (1) how exactly to split files into blocks, (2) how to
determine which blocks the other party already has.

## The Rsync Algorithm

The trivial solution to (1) is to always split the files into fixed-size, aligned
blocks. This technique breaks whenever contents is inserted to or deleted from
the file. Then, block boundaries shift and none of the blocks will match. Some
file snychronization tools nevertheless use this approach, for example the already
mentioned Syncthing. \cite{bep}

The trivial solution to (2) is for the receiving side to simply send checksums
of its blocks to the sending size. This reduces the transfer requirements by at
most a constant factor because we need to send $\Theta(\text{file size})$ checksums.
In practice, however, this is often sufficient, as demonstrated by the success
of the rsync algorithm \cite{rsync}, now a de facto standard for delta transfers.

Rsync splits the old file on the receiving side into fixed-size aligned blocks.
For each of these blocks, two different checksums are computed: a "slow" checksum
(a cryptographic hash, which is reliable but expensive to compute) and a "fast"
checksum (that is unreliable but cheap to compute). Now comes the key trick: the
fast checksum is computed using a rolling hash function. That means when we know
the hash for a $w$-byte substring of the file starting at position
$i$, we can efficiently (in constant time) use it to compute the hash for a $w$-byte
string starting at position $i+1$. This is often called a "sliding window" algorithm:
we imagine having a "window" $w$ bytes byte wide that we are moving over the file.
Each time we can move it one byte to the right and efficiently recompute the hash
of the string now in the window.

This property does not seem useful when computing hashes of aligned blocks on the
receiver side. However, the sender uses the sliding window property to compute
the fast checksum of $w$-bytes blocks starting at every possible byte offset in the
file. This allows finding shifted and unaligned blocks.

Now the sending party transfers instructions for reconstructing the file. Each instruction
is either (a) write a given block from the original file to the new file at a given offset,
(b) write these bytes to the new bytes at a given offset (used for parts of the file
not covered by any old blocks, these can be of varying sizes from a few bytes to the whole
file).

The cannonical implementation of the rsync algorithm is a part of the `rsync`
program \cite{rsync_man}.  However, `rsync` has its own protocol and semantics for establishing
connections, authentication, dealing with multiple files, dealing with file
paths, etc., that do not fit well into our design. A more promising
implementation of the algorithm is available in the `librsync` library. \cite{librsync}
This
library implements only the pure rsync algorithm and leaves all the other
aspects, including the logistics of network communication and filesystem
access, up to the application, which makes it very flexible.

It is the `librsync` library that was intended to be used for implementing content
synchronization in Filoco, although the implementation was never finished.

## Set Reconciliation Based Methods

We might notice that the structure of block-based synchronization problem is
rather similar to the set reconciliation problem: both sides have some blocks
and Alice wants to send Bob exactly the blocks he does not have.

However, in order for them to use set reconciliation, both of them must split
the file into blocks *independently* to create the sets to be reconciled. Therefore
we cannot use the rsync trick where Alice's splitting is dependent on knowledge
of Bob's blocks.

This can of course be accomplished by the already mentioned fixed aligned block
splitting, which has numerous problems.

An alternative is to determine block boundaries based not on file offsets but
on content. For example we can once again use a rolling hash and make a block
boundary whenever the hash value is smaller than some fixed value. Now when the
two files share a segment that has at least two block boundaries in it, the block
between the boundaries will be split indentically on both sides, reagardless
of the offset. If we consider the hash values to be essentially random, this
gives us expected block length $\ell/m$, where $\ell$ is the fixed limit and
$m$ is the maximum value of the hash function. To prevent extreme cases, we should
also bound the minimum and maximum length of the block and if necessary, cut
in non-standard places.

The use of set reconciliation and content-dependent block splitting for file
synchronization has been thoroughly examined by Marco Gentili in his
bachelor thesis. \cite{gentili}

## Filesystem Access

A general-purpose file synchronizer, possibly running in the background, has
to deal with the file system being concurrently changed. For metadata changes
(creates, renames and deletes), this has been already tackled in chapter 1.

However, file contents can also change, and do so in two ways. Some programs
write to directly to the destination file, while others first create a temporary
file, write the new version to it and then replace the original with an
atomic `rename`.

When a file is changed while it is being synchronized, the synchronization will
probably not give meaningful results. This is not only because of race conditions
involved in the synchronization algorithms (for example we compute a checksum
of a block and then the block changes). Even a simple whole-file copy is riddled with
possible race conditions. For example, after you have copied half the file, someone
may concurrently make one change and the beginning and then one change at the end.
Your copy will contain the unchanged beginning and the changed end, a version of the
file that was never present in the original.

This is far less far-fetched than it may seem. For example, a program might first
change some area in the file's header to remove a pointer to some records at the
end of the file and then physically remove the records at the end, perhaps overwriting
them with something else. In your copy, the pointer in the header would be still
present, now pointing to garbage data.

There is probably no way to recover from such situations. The only way to correctly make
a copy of a file is when it does not change during the copy/transfer process.
There are two ways this might be achieved: (1) lock the file in some fashion to prevent
other programs from accessing it, (2) make the copy in a transactional fashion.
The kind of locking needed for (1) is more or less impossible for locking.

As for (2), we can reuse the oldest trick in our book, namely comparing before/after
mtimes. First we remember the mtime of the source file, then we perform the copy/transfer
into a temporary file, then we check the source mtime again. If it has not changed, we
consider the copy correct, otherwise we start over.

When synchronizing over a network, we can perform
the complete synchronization protocol reading from the original files, while the receiver
saves the result into a temporary file. At the end, if the files on neither side changed
according to mtime comparisons, the parties agree to commit the transaction, otherwise
they retry the whole protocol again.

A different kind of race condition might occur on the receiver side. Between the moment
that we decided the original files have not changed and we should commit the transaction
and the moment we actually replace the target file using an atomic rename, the target file
might be changed. In this case, we would replace the target file with a consistent
version but lose some recent changes. With a normal `rename`, this cannot be prevented.
However, on Linux we can use the extended `renameat2` syscall with the `RENAME_EXCHANGE`
flag. This causes the kernel to atomically exchange the temporary file with the target
file. If any concurrent modifications were made during the brief window, they will now
be available in the location of the temporary file. We could record this as a normal
version conflict and store both verions of the file.

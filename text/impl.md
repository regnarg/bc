# Implementation

A sketch of Filoco implementation is provided as attachment 1 in the electronic version
of this thesis. Due to a limited amount of time, the implementation is in many regards
incomplete and currently serves more as a proof of concept of some of the techniquest
discussed here than a piece of software that would be actually useful for synchronizing
one's files.

Most of the implementation is a rather straightforward application of the algorithms
and methods described. Here we will pinpoint only a few interesting technical aspects.

## Metadata Storage

Stores are odinary directories that contain a special subfolder named `.filoco`.
This contains all the Filoco-specific metadata. Most of the metadata is stored
in and SQLite \cite{sqlite} database called `.filoco/meta.sqlite`. This database is composed
of two logical parts:

  * Local filesystem metadata, used to store the last known filesystem state
    to compare against when scanning the file system for changes.
      - The `inodes` table contains information about every inode known
        to Filoco, including its inode number, file handle (used to identify
        inodes as discussed in [@sec:dirtree]) and the FOB, FLV and FCV currently
        associated with this inode, if any.
      - The `links` table stores information about directory entries, with
        parent inode identifier, child parent identifier and name for each.
  * Sychronized metadata, that model more or less one-to-one the structure
    described in [@sec:objects].
      - The `syncables` table holds information common to all object types,
        such as originating inodes and sequence numbers (for per-origin
        sequence number synchronization) or position synchronization trie
        key (for set-reconciliation-based synchronization).
      - The `fobs`, `flvs`, `fcvs` and `srs` tables hold information specific
        to the individual object files.

We heavily rely on SQLite's atomic transaction support. For example, when
a file's mtime has changed, we record the new mtime and the newly created
working FCV in the same transaction so that a power failure does not cause
changes to be forgotten. We also update the synchronization digest trie
in the same transaction as inserting a new object if the set reconciliation
scheme is used. This ensures that the precomputed subset digests are never
out of date.

SQLite is often accused of being "slow". This however depends heavily on
the way one uses it. By default, it performs an `fsync` at the end of
every transaction, which definitely causes issues. This can be helped
by grouping updates to large enough transactions. As most operations we
do are bulk anyway (filesystem scanning, metadata synchronization), it
is not a problem to make transactions for say every 5000 scanned/transferred
items.

Where this does not help is online change detection, because there changes
come separately rather than in bulk. In this case, switching SQLite to
the so-called WAL (Write-Ahead Log) mode \cite{wal} supported by newer version
that implements transactions in a different way and does not require a sync
after every signle transaction.

Another big scalability issue is with filesystem scanning, because it has
to read the file system and update the metadata database, causing seeks between
scanned inodes and database blocks and thus rendering all our precious scan
optimizations useless. This can be partially helped by forcing SQLite to cache
more changes in memory and increasing its cache size.

However, SQLite's cache management is not perfect and it starts to
lose scalability with large enough databases. What we would really want it to
interleave periods of pure filesystem scanning with periods of pure database
updates, each at least tens of seconds long.

The easiest way to do this is to cache scan results in separate in-program
data structures and only give them to SQLite in batches. However, during
scan, we also need to read the old inode data from sqlite to compare against,
which can also be slow for larger databases.

The easiest solution seems to be to load all inode information from the database
to memory on start, then scan the file system and make SQLite updates in batches.
This is more or less what our `check_helper` program does.

## Basic Structure

The implementation comprises of several independent programs. They currently have to
be run manually every time the user wishes to perform an action such as rescanning the
file system or synchronizing two stores. Nothing happens automatically in the background,
with the exception of fanotify-based filesystem online filesystem watching in `scan.py`.

All of the programs directly access the underlying SQLite database and though SQLite
performs some locking, it is not recommended to run any two of them at the same time
(with the exception of `scan.py` in fanotify live watch mode).

These are:

  * `init.py` -- creates a new Filoco store
  * `scan.py` -- performs online and/or offline scanning of
    the file system and updates metadata accordingly
  * `check_helper.c` is a helper program in C used by `scan.py` to make incremental
    rescans faster
  * `mdsync.py` -- performs metadata synchronization between two stores
  * `mdapply.py` -- applies metadata updates received from remote stores
    (moves and renames) to the local file system
  * `dsync.py` -- performs file content synchronization (unfinished)

More information about installing and using these programs can be found in the
`README.md` file in attachment 1.

<!--
\TODO{Optimizations:}

  * in general: prevent seeks between scanned inodes and db
  * try fully in-memory db (tmpfs, cache changes?)
  * WAL + synchronous=normal
  * checkpoint interval
  * larger transactions
  * sqlite page cache size

-->

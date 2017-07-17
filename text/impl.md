# Implementation
### Local filesystem metadata

### Metadata storage

\TODO{Optimizations:}

  * in general: prevent seeks between scanned inodes and db
  * try fully in-memory db (tmpfs, cache changes?)
  * WAL + synchronous=normal
  * checkpoint interval
  * larger transactions
  * sqlite page cache size


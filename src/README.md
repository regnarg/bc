## Dependencies and building

  * A C compiler and related necessities
  * The SQLite library including header files, version >= 3.8
  * Python 3.5 interpreter
  * Several Python libraries listed in `requirments.txt`, all installable via `pip`.
  * A **modified** version of the `butter` python library from our `3rd/butter` directory.
    It will not work with upstream `butter`.

All Python dependencies (including custom `butter`) can be installed with
`pip install -r requirements.txt`.

Run `make` to build the C/C++ modules.

Everything was tested on current Arch Linux with Python 3.6. It should probably run
on Debian 9 with Python 3.5, although this was not tested.

## Usage

### init.py

Create a new Filoco store in a directory.

Usage: `init.py [--synctree] <dir>`

Options:

  * `--synctree` -- use "divide and conquer with pruning" set reconciliation instead
    of the default "per-origin sequential streams". For two repositories to be able
    to sync, they must use the same reconciliation method.

### scan.py

Scan the file system for local changes. Requires root privileges.

Usage:
  * `sudo scan.py [-w fanotify] [-c] [-r] [-a] <store>`
  * `sudo scan.py -r <store>[/dir]`

By default, it rechecks the all inodes in the store (using saved file handles from
metadata db) for mtime changes. On file mtime change, it creates a new working FCV
(unless one is already the head). On a directory mtime change, that directory is
re-read (non-recursively) and the links database updated. Whenever completely new
inodes are discovered, they are added to db and scanned (for directories this means
reading their content).

With `-c`, only inodes explicitly marked in meta db as needing rescan will be checked.
This allows continuing a previously interrupted scan of a newly added directory tree
(for example the initial big scan of the entire store).

With `-w fanotify`, `scan.py` runs forever, listening for fanotify events and updating
metadata db on the fly. This requires the `FAN_MODIFY_DIR` kernel patch (see below).
When using fanotify, a complete recheck is not done on the start, only explicitly marked
inodes are rescanned (as with `-c`). If you are diligent and keep the fanotify scanner
running at all times, you can completely avoid full disk rescans and still have up to
date metadata. `-a` can be used to override this and trigger a full scan at the start
even when fanotify is used.

With `-r`, a classic recursive directory traversal is performed instead of direct inode
access using handles. This allows to rescan only a small subtree.

Incremental rescans when not much has changed are incredibly fast. However, the initial
scan is currently slow (a few minutes for 100k files, several hours for millions). This
is mostly due to the costs of updating the SQLite database. Performance drops superlinerly
as we cross database size that no longer fits into SQLite page cache (set to 256M by
Filoco). However, the big initial scan needs only be done once and then you have
blazingly fast rescans.

### mdsync.py

Synchronize metadata between two stores.

Usage:

  * `mdsync.py <dir> <dir>`: synchronize two local stores
  * `mdsync.py <dir> <ip>:<port>`: connect to remote mdsync
  * `mdsync.py <dir> --listen <port>`: start server
  * `mdsync.py <dir> -`: sync on stdio, for example for syncing via SSH or openssl tunnel.
    You need to connect stdio both ways between the commands, shell pipes are not enough,
    they are only unidirectional. You can use the `dpipe` command from the `vde2` project:

        dpipe mdsync.py <dir> - = ssh somehost mdsync.py <remote-dir> -

### mdapply.py

Requires root privileges (due to use of file handles).

Usage: `sudo mdapply.py [-f] <store>

Apply remote metadata changes to local file system. This creates placeholder inodes for
any new FOBs and applies remote renames.

`-f` forces applying metadata updates to all FOBs, not only those marked in the database
as remotely updates. This recreates any missing placeholder inodes and renames all files
according to the metadata database. This can be used to resolve inconsistncies caused
by interrupted operations, etc. Inconsistencies can also be solved in the other direction
(updating db according to the fs) by running a full rescan. If everything else fails,
one can run both `scan.py` and `mdapply.py  -f` in some order.

### info.py

Explore Filoco metadata database (diagnostic tool).

Usage:
  * `init.py [opts] <store>/<file>`
  * `init.py [opts] <store> <object-hex-id>`

By default, it shows information about a given inode or object, including its relations
to other objects.

Options:

  * `--flv-graph`: output the graph of all FLVs of a given FOB (specified by ID or file
    name) in GraphViz dot format
  * `--fcv-graph`: the same for FCVs

## Fanotify patch

To use the fanotify patch, you must build your own kernel, this requires some Linux
expertise not covered here. The patch is against Linux 4.10 but should work with any
4.x verision at least, possibly with minor changes.

Example setup:

    wget https://cdn.kernel.org/pub/linux/kernel/v4.x/linux-4.12.3.tar.xz
    tar xvf linux-4.12.3.tar.xz 
    cd linux-4.12.3/
    patch -p1 <.../src/fanotify/0001-fanotify-new-event-FAN_MODIFY_DIR.patch 
    patch -p1 <.../src/fanotify/0002-fanotify-emit-FAN_MODIFY_DIR-on-filesystem-changes.patch 
    make menuconfig # configure to your liking
    make modules zImage # build
    # and then install, configure bootloader and boot

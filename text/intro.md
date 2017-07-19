\chapter*{Introduction}
\addcontentsline{toc}{chapter}{Introduction}

<!-- *Required knowledge:
   - Basic Unix knowlegde at Intro to Unix level
   - Intermediate knowledge of the Python and C programming languages
   - Basic OS principles at the Principles of Computers course level
     (e.g. know what a syscall is, have an idea about disk caching)
-->

This thesis describes the design and implementation of a decentralized
file synchronization tool called Filoco[^1]. Filoco intends to be
an alternative to commercial tools like Dropbox or Google Drive but
one specifically tailored to advanced users, computer enthusiasts,
matfyzák's[^2], the paranoid and everyone else with specific needs not met
by mainstream tools.

[^1]: Short for \underline{Fil}e \underline{Loco}motive (because it pulls your files around).
[^2]: The term *matfyzák* is colloquially used to refer not only to the students of Faculty of
      Mathematics and Physics but also to anyone bearing the personality traits typical
      for such students. In that sense, one does not become a matfyzák, one is born one.
      \cite{matfyzak}

A prototypical Filoco user has a laptop, a backup laptop, a home computer,
a work computer, a bedroom computer, several phones and tablets, 8 terabytes'
worth of external hard drives, a home server, a NAS, and a VPS. A prototypical Filoco
user has on the order of a few million files scattered across all these
places: software, music, movies, books, audiobooks, notes, scripts,
configuration files\dots

At different times, they need to use the same files on different devices.  They
usually transfer them between storage locations on an as-needed basis using
ad-hoc methods such as thumb drives, rsync, scp, e-mail, personal git
repositories, a `tmp` directory on their web server\dots This leads to several
copies of each file, some of them temporary, some of them serving as backups,
not all of them kept regularly up to date.

With such a setup, it is easy to lose track of all the places where a file is
stored, let alone which of these places contains the current version. It is
also easy to get to situations where you need a specific file, which is
currently only stored for example on your home computer (currently turned off
and far away) because you forgot to copy it to a server.

File synchronization tools try to alleviate these issues by automatically copying
files between machines and keeping these copies up-to-date. However, most common
synchronization tools have at least some of the following limitations, which make
them less suitable for the user group described:

  * These tools usually try to sync everything everywhere. This is a problem for
    users that have much more data in total than any single one of their computers
    or disks can hold.
  * They often have limited scalability, especially with regard to the total number
    of files. There are many technical reasons for this that will be discussed in
    further chapters.
  * They often require using proprietary software and/or cloud services (where
    files are often stored unencrypted). This can be hard to accept for people
    with a bit of healthy mistrust and paranoia or anyone unhappy about sharing their
    data with American three-letter agencies.
  * Synchronization often must be performed via a centralized server in the cloud,
    even when devices are able to communicate directly. This causes problems
    if one wants for example to synchronize their phone with their laptop while
    travelling, with only a slow and/or expensive mobile internet connection
    available.
  * It is usually not possible to use external drives as synchronization replicas.

A more detailed survery of existing tools is given in chapter \ref{related-works}.

Filoco tries to overcome these limitations. Its basic task is to synchronize data
among a set of *stores*. A store is simply a directory containing ordinary files
plus some additional Filoco-specific metadata. A store can be physically located
on a desktop computer, mobile device, server, external drive or anything else with
a file system. We call all the stores that are synchronized among each other
a *realm*.

It follows the philosophy of *global metadata, distributed data*. This means that
while each store has copies of only
some files, it has information about all the files in the realm. This
metadata describes a single logical directory tree containing all the files
in the realm that is consistent across all the stores.

The metadata also contains information about where each file is physically
stored (an in what version). Thus that when a file
is not available locally, Filoco knows where to fetch it from. If this is an
offline store (e.g. an external drive or a powered down laptop), Filoco can
ask the user to connect it and/or turn it on.

Upon request, any two stores can be synchronized, either via network or locally
if the other is on an external drive connected to the same computer. All stores
are equal, there is no special master store. The user can configure which files
should be kept by which stores.

Apart from the basic concept outlined above, Filoco has the following design
goals (in order of importance):

  * *Scalability and efficiency.* We shall optimize specifically for the common
    case when the user has a lot of data, most of which changes only infrequently.
    Small incremental updates should be fast even when the total number of
    files is large (a few million). Ideally, the time complexity of most operations
    should not depend on the total number of files at all, only on the number/size
    of changed/affected files. This will not always be possible but we should try
    to get close to this ideal.


  * *Robustness.* This means not only that it should not eat your data but also
    resilience to things like interrupted transfers, power failures or race conditions
    with other processes accessing the files managed by Filoco.

  * *Flexibility.* Rather than a one-size-fits-all solution, Filoco should be
    a framework that each user can adapt to fit their unique needs and workflows.
    It should be both configurable and easy to integrate with shell scripts.
    Where possible, the user should be put in control. We should make as little
    policy decisions as possible.

  * *Security.* This of course includes transport encryption and mutual authentication
    during network communication (nowadays taken for granted). It also includes the
    ability for designating *untrusted stores*, which (1) only store and exchange
    encrypted data (and metadata), without ever having access to the cleartext, (2)
    can only relay updates made by trusted stores (and cryptographically signed
    by them), not make their own changes to the data. Any (meta)data received from
    and untrusted store must be cryptographically verified to have been originally
    created by a trusted store.
    
    Otherwise, untrusted stores should be able to participate
    in normal synchronization, exchanging encrypted (meta)data with other stores,
    both trusted and untrusted. This exchange should ideally be as efficient as
    (or close to) the unencrypted exchange between two trusted stores, including
    efficient incremental updates to encrypted files if possible (although this seems
    like a hard problem).
    
    This would allow using any untrustworthy cheap cloud storage
    provider for additional storage, or as an intermediary for exchanging data
    between nodes behind NAT. The untrusted store will not be able to
    read nor modify your data. The only damage it can do is to delete your data,
    which can be alleviated by redundant storage on different stores.

Explicit non-goals include fancy GUIs and beginner-friendliness. Users are expected
to have at least a basic understanding of Filoco's internals to make full use of it.
The same is true for example for git.

Filoco runs only on Linux and there are currently no plans to support other operating
systems (with the possible exception of Android, which is basically Linux).

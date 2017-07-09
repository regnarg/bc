---- LOCAL FILESYSTEM STATE ----

create table inodes (
    ino integer primary key on conflict replace,
    handle text unique,
    iid text unique,
    type text, -- 'r', 'd', 'l', etc.
    scan_state integer default 0,
    size integer,
    mtime integer,
    ctime integer,
    oid integer references obects(oid)
);

-- including 'ino' in the index helps sorting
create index inodes_type on inodes (type, scan_state, ino);

create table links (
    parent integer references inodes(ino) on delete cascade,
    name text,
    ino integer references inodes(ino) on delete cascade,
    unique (parent, name)
);

create table fslog (
    serial integer primary key,
    event integer,
    --- The following two are IIDs but have no `refereces` clasuse because
    --- they can refer to already-dead inodes.
    iid text,
    parent_iid text,
    name text
);

---- SYNCHRONIZED METADATA ----

create table syncables (
    tree_key integer,
    id text unique,
    kind text
);


-- Used when querying subtrees (represented by consecutive key intervals).
create index syncables_tree_key on syncables (tree_key);

create table synctree (
    pos integer primary key,
    xor blob,
    chxor blob
);

create table fobs (
    id text unique references syncables(id),
    type text
);

create table fovs (
    id text unique references syncables(id),
    fob text references fobs(id),
    parent_versions text
);



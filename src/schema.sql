---- LOCAL FILESYSTEM STATE ----

create table inodes (
    ino integer primary key on conflict replace,
    handle text unique,
    iid text unique,
    type text, -- 'r', 'd', 'l', etc.
    scan_state integer default 0,
    size integer,
    mtime integer,
    ctime integer
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
    id text unique,
    kind text
);

create table synctree (
    pos text unique,
    xor text,
    chk text
);

create table objects (
    oid text unique references syncable(id),
    type text
    
);

create table versions (
    vid text unique references syncable(id),
    oid text references objects(oid),
    parent_versions text

);



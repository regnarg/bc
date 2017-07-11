---- LOCAL FILESYSTEM STATE ----

create table inodes (
    ino integer primary key on conflict replace,
    handle_type integer,
    handle blob,
    iid text unique,
    type text, -- 'r', 'd', 'l', etc.
    scan_state integer default 0,
    size integer,
    mtime integer,
    ctime integer,
    oid integer references obects(oid)
);

-- including 'ino' in the index helps sorting
create index inodes_type_state on inodes (type, scan_state, ino);
create index inodes_state on inodes (scan_state, ino);
create unique index inodes_handle on inodes (handle_type, handle);

create table links (
    parent integer references inodes(ino) on delete cascade,
    name text,
    ino integer references inodes(ino) on delete cascade,
    unique (parent, name)
);

-- create table fslog (
--     serial integer primary key,
--     event integer,
--     --- The following two are IIDs but have no `refereces` clasuse because
--     --- they can refer to already-dead inodes.
--     iid text,
--     parent_iid text,
--     name text
-- );

-- This maps store IDs (fingerprints) to short numeric IDs.
-- Index 0 is always the local repo!
create table stores (
    idx integer primary key,
    fingerprint text unique
);

---- SYNCHRONIZED METADATA ----

create table syncables (
#if sync_mode == 'synctree'
    insert_order integer primary key autoincrement,
#else
    serial integer,
#endif
    origin_idx integer default 0 references stores(idx),
#if sync_mode == 'synctree':
    tree_key integer,
#endif
    id text unique,
    kind text,
    sig blob -- RSA signature of origin repo
);

#if sync_mode == 'serial'
create unique index syncables_origin_serial on syncables (
    origin_idx, serial
);
create view syncables_local as select * from syncables where origin_idx=0;
create trigger syncables_local_insert instead of insert on syncables_local begin
    insert into syncables (origin_idx, serial, id, kind, sig)
        values (0, coalesce(new.serial, (select max(serial) from syncables_local)+1, 1), new.id, new.kind, new.sig);
end;
#endif

#if sync_mode == 'synctree'
-- Used when querying subtrees (represented by consecutive key intervals).
create index syncables_tree_key on syncables (tree_key);
#endif

# if sync_mode == 'synctree':
create table synctree (
    pos integer primary key,
    xor blob,
    chxor blob
);
# endif

create table fobs (
    id text unique references syncables(id),
    type text
);

create table fovs (
    id text unique references syncables(id),
    fob text references fobs(id),
    parent_versions text
);



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
    btime integer, -- creation time (if available), otherwise discover time
    fob text references fobs(id),
    flv text references flvs(id),
    fcv text references fcvs(id)
);

-- including 'ino' in the index helps sorting
create index inodes_type_state on inodes (type, scan_state, ino);
create index inodes_state on inodes (scan_state, ino);
create unique index inodes_handle on inodes (handle_type, handle);
create index new_inodes on inodes (ino) where fob is null;

create table links (
    parent integer not null references inodes(ino) on delete cascade,
    name text not null,
    ino integer not null references inodes(ino) on delete cascade,
    unique (parent, name)
);

--create index links_by_location on links (parent, name);
create index links_by_ino on links (ino);

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
    id text unique not null
);

---- SYNCHRONIZED METADATA ----

create table syncables (
    insert_order integer primary key autoincrement,
#if sync_mode == 'serial'
    serial integer not null,
#endif
    origin_idx integer not null default 0 references stores(idx),
#if sync_mode == 'synctree':
    tree_key integer not null,
#endif
    id text unique not null,
    kind text not null,
    created integer not null
);

#if sync_mode == 'serial'
create unique index syncables_origin_serial on syncables (
    origin_idx, serial
);
create view syncables_local as select * from syncables where origin_idx=0;
create trigger syncables_local_insert instead of insert on syncables_local begin
    insert into syncables (origin_idx, serial, id, kind, created)
        values (0, coalesce(new.serial, (select max(serial) from syncables_local)+1, 1), new.id, new.kind, new.created);
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
    id text unique not null references syncables(id),
    type text,
    _new_flvs integer not null default 0,
    _new_fcvs integer not null default 0,
    _has_inode integer not null default 0
);

create index fobs_new_flvs on fobs (_new_flvs);
create index fobs_new_fcvs on fobs (_new_fcvs);

create table flvs (
    id text unique not null references syncables(id),
    fob text not null references fobs(id),
    parent_fob text references fobs(id),
    name text,
    parent_vers text,
    _is_head integer default 1
);
create index flvs_fob on flvs (fob, _is_head);

create table fcvs (
    id text unique not null references syncables(id),
    fob text not null references fobs(id),
    content_hash text,
    parent_vers text,
    _is_head integer default 1
);
create index fcvs_fob on flvs (fob, _is_head);

create table srs (
    id text unique references syncables(id),
    fcv text,
    state integer
);



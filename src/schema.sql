create table inodes (
    ino integer primary key on conflict replace,
    handle text unique,
    iid text unique,
    type text, -- 'r', 'd', 'l', etc.
    mtime integer
);

create index inodes_type on inodes (type);

create table links (
    parent integer references inodes(ino) on delete cascade,
    name text,
    ino integer unique references inodes(ino) on delete cascade,
    unique (parent, name)
);

-- create table fslog (
-- );

create table inodes (
    ino integer primary key,
    handle text,
    type integer,
    mtime integer,
    uuid text,
    replaced_uuid text,
    parent integer references inodes(ino) on delete set null,
    name text
);


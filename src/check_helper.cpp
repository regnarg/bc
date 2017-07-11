// Helper for faster rescans, to eliminate Python overhead for files
// that have not changed (presumably the majority).

#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <math.h>

#include <sqlite3.h>

#include <vector>
using namespace std;

struct item {
    unsigned handle_bytes;
    int handle_type;
    char f_handle[MAX_HANDLE_SZ];
    ssize_t size;
    double mtime;
    double ctime;
    ino_t ino;
};


int main(int argc, char **argv) {
    sqlite3 *db;
    int rc;

    if (argc != 2) {
        fprintf(stderr, "Usage: %s STORE\n", argv[0]);
        return 1;
    }

    if (chdir(argv[1]) == -1) {
        fprintf(stderr, "%s: error: Cannot chdir to %s: %m\n", argv[0], argv[1]);
        return 1;
    }

    rc = sqlite3_open(".filoco/meta.sqlite", &db);
    if (rc) {
        fprintf(stderr, "Can't open metadata database: %s\n", sqlite3_errmsg(db));
        return 1;
    }

    sqlite3_busy_timeout(db, 10000);
    
    sqlite3_stmt *stmt;
    //                                   0        1          2     3      4      5
    rc = sqlite3_prepare_v2(db, "select ino, handle_type, handle, size, mtime, ctime from inodes where scan_state=100 order by ino asc", -1, &stmt, NULL);

    if (rc != SQLITE_OK){
        fprintf(stderr, "SQL error: %d\n", rc);
        return 1;
    }

    vector<item> queue;
    vector<item> changed;

    fprintf(stderr, "%s: info: reading database\n", argv[0]);
    while (true) {
        rc = sqlite3_step(stmt);
        if (rc == SQLITE_DONE) {
            break;
        } else if (rc == SQLITE_ROW) {
            struct item itm;
            itm.handle_type = sqlite3_column_int(stmt, 1);
            const void *data = sqlite3_column_blob(stmt, 2);
            itm.handle_bytes = sqlite3_column_bytes(stmt, 2);
            itm.size = sqlite3_column_int64(stmt, 3);
            itm.mtime = sqlite3_column_double(stmt, 4);
            itm.ctime = sqlite3_column_double(stmt, 5);
            itm.ino = (ino_t)sqlite3_column_int64(stmt,0);
            if (itm.handle_bytes > MAX_HANDLE_SZ) {
                fprintf(stderr, "Handle too big for ino %llu (type %d, %d bytes)\n",
                        (unsigned long long) itm.ino,
                        itm.handle_type, itm.handle_bytes);
                continue;
            }
            memcpy(&itm.f_handle, data, itm.handle_bytes);
            queue.push_back(itm);
        } else {
            fprintf(stderr, "SQL error: %d\n", rc);
            return 1;
        }
    }
    fprintf(stderr, "%s: info: read %lu items\n", argv[0], queue.size());

    fprintf(stderr, "%s: info: checking stat()\n", argv[0]);
    for (const item &itm : queue) {
        int fd = open_by_handle_at(AT_FDCWD, (struct file_handle*) &itm, O_PATH);
        if (fd == -1) {
            // De
            if (errno == ESTALE || errno == ENOENT) {
                // deleted inode
                changed.push_back(itm);
            } else {
                fprintf(stderr, "Failed to open handle: %m\n");
            }
            continue;
        }
        struct stat st;
        if (fstat(fd, &st) == -1) {
            fprintf(stderr, "fstat failed: %m\n");
            continue;
        }
        close(fd);
        double mtime = st.st_mtim.tv_sec + (double) st.st_mtim.tv_nsec / 1e9;
        double ctime = st.st_ctim.tv_sec + (double) st.st_ctim.tv_nsec / 1e9;
        if (st.st_size != itm.size || fabs(mtime - itm.mtime) > 1e-4 || fabs(ctime - itm.ctime) > 1e-4) {
            fprintf(stderr, "Detected change: ino=%llu, size=%zd/%zd, mtime=%f/%f, ctime=%f/%f\n",
                    (unsigned long long)st.st_ino, itm.size, st.st_size, itm.mtime, mtime, itm.ctime, ctime);
            changed.push_back(itm);
        }
    }
    fprintf(stderr, "%s: info: found %lu changes\n", argv[0], changed.size());

    fprintf(stderr, "%s: info: updating database\n", argv[0]);
    sqlite3_stmt *upd;
    rc = sqlite3_prepare_v2(db, "update inodes set scan_state=1 where handle_type=? and handle=?", -1, &upd, NULL);
    if (rc != SQLITE_OK){
        fprintf(stderr, "SQL error: %d\n", rc);
        return 1;
    }
    
    for (const item &itm : changed) {
        sqlite3_reset(upd);
        sqlite3_bind_int(upd, 1, itm.handle_type);
        sqlite3_bind_blob(upd, 2, &itm.f_handle, itm.handle_bytes, SQLITE_TRANSIENT);
        rc = sqlite3_step(upd);
        if (rc != SQLITE_DONE) {
            fprintf(stderr, "SQL error: %d\n", rc);
            continue;
        }
        if (sqlite3_changes(db) < 1) {
            fprintf(stderr, "%s: warning: scan_state update did not change any rows (ino=%llu)\n",
                    argv[0], (unsigned long long)itm.ino);
        }

    }

    // printf("done\n");
    // getc(stdin);

    sqlite3_finalize(stmt);
    sqlite3_close(db);
    return 0;
}

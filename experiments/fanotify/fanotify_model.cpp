//#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/fanotify.h>
#include <stdint.h>
#include <dirent.h>
#include <assert.h>
#include <string.h>

#include <map>
#include <set>
#include <list>
using namespace std;

#ifndef FAN_MODIFY_DIR
#define FAN_MODIFY_DIR 0x00040000
#endif

// die-on-error helpers
#define CHK(x) ({ __typeof__(x) r = x; if (r == -1) { perror(#x); abort(); } r; })
#define CHKN(x) ({ __typeof__(x) r = x; if (r == NULL) { perror(#x); abort(); } r; })
struct inode_info;
struct dentry_info;

struct inode_info {
    ino_t ino;
    mode_t mode;
    char handle[MAX_HANDLE_SZ];
    set<struct dentry_info *> links;
    map<string, struct dentry_info *> children; // for directory inodes
};

struct dentry_info {
    struct inode_info *parent, *inode;
    string name;
};


map<ino_t, inode_info*> inodes;

int root_fd;
int fan_fd;

bool compare_handles(const void *h1, const void *h2) {
    const struct file_handle *fh1 = (const struct file_handle*) h1;
    const struct file_handle *fh2 = (const struct file_handle*) h2;
    return (fh1->handle_bytes == fh2->handle_bytes
                && memcmp(h1, h2, fh1->handle_bytes) == 0);
}

bool handle_valid(void *handle) {
    int check_fd = open_by_handle_at(root_fd, (struct file_handle*)handle, O_PATH);
    if (check_fd >= 0) {
        CHK(close(check_fd));
        return true;
    } else if (errno == ESTALE) {
        return false;
    } else {
        perror("open_by_handle_at");
        exit(1);
    }
}

// Get the path corresponding to an inode (one of its paths, in the presence of
// hardlinks).
void inode_path(const struct inode_info *inode, char *buf, size_t bufsiz) {
    list<string> components;
    while (true) {
        if (inode->links.empty()) break;
        struct dentry_info *dentry = *inode->links.begin();
        components.push_front(dentry->name);
        inode = dentry->parent;
    }
    buf[0] = '\0';
    for (auto name: components) {
        int len = snprintf(buf, bufsiz, "/%s", name.c_str());
        buf += len;
        bufsiz -= len;
    }
}


void delete_dentry(struct dentry_info *dentry) {
    assert(dentry->parent->children[dentry->name] == dentry);

    char path_buf[4096];
    inode_path(dentry->parent, path_buf, sizeof(path_buf));
    printf("unlinked %s/%s (ino %lu, parent %lu)\n", path_buf, dentry->name.c_str(),
           dentry->inode->ino, dentry->parent->ino);

    dentry->parent->children.erase(dentry->name.c_str());
    dentry->inode->links.erase(dentry);
    // TODO: If this was the last dentry pointing to an inode, schedule removing
    //       the inode after a timeout (we cannot remove it immediately because
    //       the zero-link situation might occur during a rename when the source
    //       directory has been processed but the target directory hasn't).
    delete dentry;
}

struct dentry_info *add_dentry(struct inode_info *parent, const char *name,
                                struct inode_info *child) {
    struct dentry_info *dentry = new dentry_info();
    dentry->parent = parent;
    dentry->name = name;
    dentry->inode = child;
    parent->children[name] = dentry;
    child->links.insert(dentry);

    char path_buf[4096] = "\0";
    inode_path(parent, path_buf, sizeof(path_buf));
    printf("linked %s/%s (ino %lu, parent %lu)\n", path_buf, name, child->ino, parent->ino);

    return dentry;
}

void delete_inode(struct inode_info *inode) {
    for (auto dentry: inode->links) {
        delete_dentry(dentry);
    }
    delete inode;
}

// Given a file descriptor, find the corresponding inode object in our database,
// or create a new one if it does not exist. An O_PATH fd suffices.
struct inode_info *find_inode(int fd) {
    struct stat st;
    CHK(fstat(fd, &st));
    char handle[sizeof(struct file_handle) + MAX_HANDLE_SZ];
    struct file_handle *fh = (struct file_handle*)handle;
    fh->handle_bytes = sizeof(handle);
    int mntid;
    CHK(name_to_handle_at(fd, "", (struct file_handle*)handle, &mntid,
                            AT_EMPTY_PATH));

    struct inode_info *info = inodes[st.st_ino];
    if (info) {
        // Handles can refer to the same file despite not being equal.
        // If the old handle can still be opened, we can be assured
        // that the inode number has not been recycled.
        if (compare_handles(handle, info->handle) || handle_valid(info->handle)) {
            return info;
        } else {
            delete_inode(info);
            info = NULL;
        }
    }

    inodes[st.st_ino] = info = new inode_info();
    info->ino = st.st_ino;
    info->mode = st.st_mode;
    memcpy(info->handle, handle, fh->handle_bytes);
    return info;
}

// Scan directory and update internal filesystem representation accordingly.
// Closes `dirfd`.
void scan(int dirfd, bool recursive) {
    struct inode_info *dir = find_inode(dirfd);

    char path_buf[4096] = "\0";
    inode_path(dir, path_buf, sizeof(path_buf));
    printf("scan %s (%lu)\n", path_buf, dir->ino);

    DIR *dp = CHKN(fdopendir(dirfd));
    set<string> seen;
    while (struct dirent *ent = readdir(dp)) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        seen.insert(ent->d_name);
        if (dir->children.find(ent->d_name) != dir->children.end()
                && dir->children[ent->d_name]->inode->ino == ent->d_ino) {
            // Heuristic: It is massively unlikely that an inode number
            // would be recylced at the same path as before. So if we
            // see the same inode for the same child, we skip the more
            // expensive checks altogether. This saves us a buttload of
            // syscalls, especially given that most directory entries
            // will be unchanged after a FAN_MODIFY_DIR.
            //
            // This can be skipped if strict correctness is preferred
            // over speed.
            continue;
        }
        int fd = openat(dirfd, ent->d_name, O_PATH|O_NOFOLLOW);
        if (fd < 0) continue;
        struct inode_info *child = find_inode(fd);
        if (dir->children.find(ent->d_name) != dir->children.end()) {
            struct dentry_info *old_dentry = dir->children[ent->d_name];
            if (child != old_dentry->inode) {
                delete_dentry(old_dentry);
                add_dentry(dir, ent->d_name, child);
            }
        } else {
            add_dentry(dir, ent->d_name, child);
        }
        if (recursive && S_ISDIR(child->mode)) {
            // `fd' is just an O_PATH fd. For scanning we need O_RDONLY.
            int scan_fd = CHK(openat(fd, ".", O_RDONLY|O_DIRECTORY));
            scan(scan_fd, true); // closes scan_fd
        }
        close(fd);
    }
    for (auto it: dir->children) {
        if (seen.find(it.second->name) == seen.end()) delete_dentry(it.second);
    }
    closedir(dp);
}

void event_loop() {
    while (true) {
        char buf[4096];
        ssize_t len = CHK(read(fan_fd, buf, sizeof(buf)));
        const struct fanotify_event_metadata *event;
        event = (const struct fanotify_event_metadata*) buf;
        while (FAN_EVENT_OK(event, len)) {
            if (event->vers != FANOTIFY_METADATA_VERSION) abort();
            if (event->mask & FAN_MODIFY_DIR) {
                scan(event->fd, false);
            } else if (event->mask & FAN_Q_OVERFLOW) {
                abort(); // TODO: full rescan needed
            } else {
                close(event->fd);
            }
            event = FAN_EVENT_NEXT(event, len);
        }
    }
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: %s MOUNTPOINT\n", argv[0]); return 1; }

    root_fd = CHK(open(argv[1], O_RDONLY|O_DIRECTORY));
    // In a real application, FAN_UNLIMITED_QUEUE would be replaced with a secondary
    // userspace queue filled during scanning.
    fan_fd = CHK(fanotify_init(FAN_UNLIMITED_QUEUE, O_RDONLY));
    CHK(fanotify_mark(fan_fd, FAN_MARK_ADD|FAN_MARK_MOUNT, FAN_MODIFY_DIR|FAN_ONDIR,
                        root_fd, NULL));
    
    scan(dup(root_fd), true);

    event_loop();

    return 0;
}

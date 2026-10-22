#define _GNU_SOURCE
#include "agent13_shred.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

#define BUFFER_SIZE 65536
#define STAGE_PREFIX ".agent13-stage-"

uint32_t agent13_abi_version(void) { return 1; }

static int fail(agent13_error *e, int code, int number, const char *message)
{
    if (e) {
        e->code = code;
        e->system_errno = number;
        snprintf(e->message, sizeof(e->message), "%s", message);
    }
    return code;
}

static void describe(const struct stat *s, agent13_file_info *i)
{
    memset(i, 0, sizeof(*i));
    i->device = s->st_dev;
    i->inode = s->st_ino;
    i->size = s->st_size;
    i->mtime_sec = s->st_mtim.tv_sec;
    i->mtime_nsec = s->st_mtim.tv_nsec;
    i->ctime_sec = s->st_ctim.tv_sec;
    i->ctime_nsec = s->st_ctim.tv_nsec;
    i->mode = s->st_mode;
    i->uid = s->st_uid;
    i->links = s->st_nlink;
}

static int same_file(const agent13_file_info *a, const agent13_file_info *b,
                     int include_ctime)
{
    return a->device == b->device && a->inode == b->inode &&
        a->size == b->size && a->mtime_sec == b->mtime_sec &&
        a->mtime_nsec == b->mtime_nsec && a->mode == b->mode &&
        a->uid == b->uid && a->links == b->links &&
        (!include_ctime || (a->ctime_sec == b->ctime_sec &&
                            a->ctime_nsec == b->ctime_nsec));
}

static int safe_directory(int fd, int root, agent13_error *e)
{
    struct stat s;
    if (fstat(fd, &s) < 0)
        return fail(e, AG13_IO, errno, "Cannot inspect directory");
    if (geteuid() == 0 || !S_ISDIR(s.st_mode) || s.st_uid != geteuid() ||
        (s.st_mode & (root ? 0077 : 0022)) != 0)
        return fail(e, AG13_UNSAFE, 0, "Directory must be private and owned by the service user");
    return AG13_OK;
}

static int safe_file(const struct stat *s, dev_t device, agent13_error *e)
{
    if (!S_ISREG(s->st_mode) || s->st_nlink != 1 || s->st_uid != geteuid() ||
        s->st_dev != device || s->st_size < 0 || (s->st_mode & 0022))
        return fail(e, AG13_UNSAFE, 0, "Require an owned regular file, one link, same filesystem, no shared write access");
    if ((s->st_mode & 0600) != 0600)
        return fail(e, AG13_UNSAFE, EACCES, "File needs owner read and write permission");
    return AG13_OK;
}

/* Resolve each directory relative to an fd, never following a symbolic link. */
static int open_target(int root, const char *path, int *parent_out,
                       char *leaf, agent13_file_info *info, agent13_error *e)
{
    char copy[4096];
    struct stat root_stat, s;
    int parent = -1, target = -1;
    if (!path || !*path || path[0] == '/' || strlen(path) >= sizeof(copy) ||
        path[strlen(path) - 1] == '/' || strstr(path, "//")) {
        fail(e, AG13_INVALID, 0, "Require a nonempty relative path without empty components");
        return -1;
    }
    if (safe_directory(root, 1, e) != AG13_OK)
        return -1;
    if (fstat(root, &root_stat) < 0) {
        fail(e, AG13_IO, errno, "Cannot inspect allowed root");
        return -1;
    }
    snprintf(copy, sizeof(copy), "%s", path);
    parent = fcntl(root, F_DUPFD_CLOEXEC, 3);
    if (parent < 0) {
        fail(e, AG13_IO, errno, "Cannot duplicate root descriptor");
        return -1;
    }
    char *component = copy;
    for (;;) {
        char *slash = strchr(component, '/');
        if (slash) *slash = '\0';
        if (!strcmp(component, ".") || !strcmp(component, "..") ||
            !strncmp(component, ".agent13-", 9) || strlen(component) > 255) {
            fail(e, AG13_INVALID, 0, "Traversal and reserved names are forbidden");
            goto bad;
        }
        if (!slash) break;
        int next = openat(parent, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (next < 0) {
            fail(e, AG13_IO, errno, "Cannot open parent directory without following links");
            goto bad;
        }
        close(parent);
        parent = next;
        if (safe_directory(parent, 0, e) != AG13_OK) goto bad;
        if (fstat(parent, &s) < 0 || s.st_dev != root_stat.st_dev) {
            fail(e, AG13_UNSAFE, 0, "Cross-filesystem traversal is forbidden");
            goto bad;
        }
        component = slash + 1;
    }
    snprintf(leaf, 256, "%s", component);
    if (fstatat(parent, leaf, &s, AT_SYMLINK_NOFOLLOW) < 0) {
        fail(e, AG13_IO, errno, "Cannot inspect target");
        goto bad;
    }
    if (safe_file(&s, root_stat.st_dev, e) != AG13_OK) goto bad;
    target = openat(parent, leaf, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (target < 0) {
        fail(e, AG13_IO, errno, "Cannot open target for read/write");
        goto bad;
    }
    if (fstat(target, &s) < 0) {
        fail(e, AG13_IO, errno, "Cannot inspect opened target");
        goto bad;
    }
    if (safe_file(&s, root_stat.st_dev, e) != AG13_OK) goto bad;
    describe(&s, info);
    *parent_out = parent;
    return target;
bad:
    if (target >= 0) close(target);
    close(parent);
    return -1;
}

static int random_fill(void *buffer, size_t length)
{
    size_t done = 0;
    while (done < length) {
        ssize_t n = getrandom((char *)buffer + done, length - done, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { if (n == 0) errno = EIO; return -1; }
        done += (size_t)n;
    }
    return 0;
}

static int sync_fd(int fd)
{
    int rc;
    do { rc = fsync(fd); } while (rc < 0 && errno == EINTR);
    return rc;
}

int agent13_inspect_at(int root, const char *path,
                      agent13_file_info *info, agent13_error *e)
{
    agent13_error local = {0};
    if (!e) e = &local;
    memset(e, 0, sizeof(*e));
    if (!info) return fail(e, AG13_INVALID, 0, "Missing output structure");
    int parent = -1;
    char leaf[256];
    int fd = open_target(root, path, &parent, leaf, info, e);
    if (fd < 0) return e->code;
    close(fd);
    close(parent);
    return AG13_OK;
}

int agent13_shred_at(int root, const char *path,
                    const agent13_file_info *expected, agent13_error *e)
{
    agent13_error local = {0};
    if (!e) e = &local;
    memset(e, 0, sizeof(*e));
    if (!expected) return fail(e, AG13_INVALID, 0, "Missing expected fingerprint");
    agent13_file_info before, after;
    struct stat s;
    char leaf[256], stage_name[64] = {0};
    int parent = -1, stage = -1, staged = 0, stage_created = 0;
    int fd = open_target(root, path, &parent, leaf, &before, e);
    if (fd < 0) return e->code;
    if (!same_file(&before, expected, 1)) {
        fail(e, AG13_CHANGED, 0, "File changed since preview");
        goto done;
    }
    if (flock(fd, LOCK_EX | LOCK_NB) < 0) {
        fail(e, AG13_BUSY, errno, "Cannot acquire exclusive advisory file lock");
        goto done;
    }
    unsigned char random_name[16];
    if (random_fill(random_name, sizeof(random_name)) < 0) {
        fail(e, AG13_IO, errno, "Cannot obtain random staging name");
        goto done;
    }
    memcpy(stage_name, STAGE_PREFIX, sizeof(STAGE_PREFIX) - 1);
    for (size_t i = 0; i < sizeof(random_name); i++)
        snprintf(stage_name + sizeof(STAGE_PREFIX) - 1 + 2*i, 3, "%02x", random_name[i]);
    if (mkdirat(root, stage_name, 0700) < 0) {
        fail(e, AG13_IO, errno, "Cannot create private staging directory");
        goto done;
    }
    stage_created = 1;
    stage = openat(root, stage_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (stage < 0) {
        fail(e, AG13_IO, errno, "Cannot open staging directory");
        goto done;
    }
    /* Rename itself changes ctime. Check the entire fingerprint immediately
     * beforehand, then compare inode/content metadata after the rename. */
    if (fstat(fd, &s) < 0) {
        fail(e, AG13_IO, errno, "Cannot recheck target");
        goto done;
    }
    describe(&s, &after);
    if (!same_file(&after, expected, 1)) {
        fail(e, AG13_CHANGED, 0, "File changed before staging");
        goto done;
    }
    if (renameat2(parent, leaf, stage, "target", RENAME_NOREPLACE) < 0) {
        fail(e, AG13_IO, errno, "Cannot safely stage file");
        goto done;
    }
    staged = 1;
    if (fstatat(stage, "target", &s, AT_SYMLINK_NOFOLLOW) < 0) {
        fail(e, AG13_IO, errno, "Cannot inspect staged target");
        goto done;
    }
    describe(&s, &after);
    if (!same_file(&after, expected, 0)) {
        fail(e, AG13_CHANGED, 0, "Source name changed during staging");
        goto done;
    }
    if (sync_fd(stage) < 0 || sync_fd(parent) < 0 || sync_fd(root) < 0) {
        fail(e, AG13_IO, errno, "Cannot persist staging directories");
        goto done;
    }
    unsigned char buffer[BUFFER_SIZE];
    uint64_t remaining = before.size;
    while (remaining) {
        size_t chunk = remaining > sizeof(buffer) ? sizeof(buffer) : (size_t)remaining;
        if (random_fill(buffer, chunk) < 0) {
            fail(e, AG13_IO, errno, "Random-byte generation failed");
            goto done;
        }
        size_t written = 0;
        while (written < chunk) {
            ssize_t n = write(fd, buffer + written, chunk - written);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) {
                fail(e, AG13_IO, n < 0 ? errno : EIO, "Overwrite failed; data may be partly overwritten");
                goto done;
            }
            written += (size_t)n;
            e->bytes_written += (uint64_t)n;
        }
        remaining -= chunk;
    }
    if (sync_fd(fd) < 0) {
        fail(e, AG13_IO, errno, "File fsync failed; data was overwritten");
        goto done;
    }
    if (fstat(fd, &s) < 0 || s.st_size != (off_t)before.size || s.st_nlink != 1) {
        fail(e, AG13_CHANGED, 0, "File size or link count changed during overwrite");
        goto done;
    }
    if (unlinkat(stage, "target", 0) < 0) {
        fail(e, AG13_IO, errno, "Cannot unlink overwritten file");
        goto done;
    }
    staged = 0;
    e->unlinked = 1;
    if (sync_fd(stage) < 0)
        fail(e, AG13_IO, errno, "Unlinked, but staging-directory fsync failed");
done:
    /* Restore even partly overwritten data on failure, without replacing a
     * newly created source name. If restore fails leave a recovery location. */
    if (staged) {
        if (renameat2(stage, "target", parent, leaf, RENAME_NOREPLACE) == 0) {
            staged = 0;
            if (sync_fd(parent) < 0 || sync_fd(stage) < 0)
                snprintf(e->message, sizeof(e->message), "Failure; file restored but directory sync failed");
        } else {
            snprintf(e->recovery_path, sizeof(e->recovery_path), "%s/target", stage_name);
        }
    }
    if (stage >= 0) close(stage);
    if (stage_created && !staged) {
        if (unlinkat(root, stage_name, AT_REMOVEDIR) < 0 && e->code == AG13_OK)
            fail(e, AG13_IO, errno, "File removed but empty staging-directory cleanup failed");
        if (sync_fd(root) < 0 && e->code == AG13_OK)
            fail(e, AG13_IO, errno, "File removed but allowed-root fsync failed");
    }
    close(fd);
    close(parent);
    return e->code;
}

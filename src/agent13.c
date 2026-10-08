#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include "agent13.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <unistd.h>

struct a13_job {
    int root_fd, parent_fd, file_fd, attempted;
    char *path, *name;
    struct stat original;
    a13_info info;
};

static int fail(char *error, size_t n, const char *stage) {
    int saved = errno;
    if (error && n) snprintf(error, n, "%s: %s", stage, strerror(saved));
    errno = saved;
    return -1;
}

static int reject(char *error, size_t n, const char *reason) {
    errno = EPERM;
    return fail(error, n, reason);
}

static int directory_ok(int fd, int is_root) {
    struct stat st;
    if (fstat(fd, &st) < 0) return -1;
    if (!S_ISDIR(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 0700) != 0700 ||
        (st.st_mode & (is_root ? 0077 : 0022))) {
        errno = EPERM;
        return -1;
    }
    return 0;
}

uint32_t a13_abi_version(void) { return A13_ABI_VERSION; }

int a13_root_open(const char *root, char *error, size_t n) {
    if (!root || !*root || geteuid() == 0)
        return reject(error, n, "root must be configured by a non-root user");
    int fd = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return fail(error, n, "open allowed directory");
    if (directory_ok(fd, 1) < 0) {
        close(fd);
        return reject(error, n, "allowed directory must be owned by the service user and mode 0700");
    }
    return fd;
}

/* Every component is opened without following symlinks. Parent descriptors
 * remain anchored even if a path component is renamed. Rewalk before use. */
static int parent_open(int root_fd, const char *path, char **name,
                       char *error, size_t n) {
    if (!path || !*path || path[0] == '/' || strlen(path) >= PATH_MAX)
        return reject(error, n, "expected a bounded relative file path");
    if (directory_ok(root_fd, 1) < 0)
        return fail(error, n, "allowed directory permissions changed");
    char *copy = strdup(path);
    if (!copy) return fail(error, n, "allocate path");
    int fd = fcntl(root_fd, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) { free(copy); return fail(error, n, "duplicate root fd"); }
    char *part = copy;
    for (;;) {
        char *slash = strchr(part, '/');
        if (slash) *slash = '\0';
        if (!*part || !strcmp(part, ".") || !strcmp(part, "..")) {
            close(fd); free(copy);
            return reject(error, n, "empty or traversal path component");
        }
        if (!slash) {
            *name = strdup(part);
            free(copy);
            if (!*name) { close(fd); return fail(error, n, "allocate filename"); }
            return fd;
        }
        int next = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (next < 0) {
            int saved = errno; close(fd); free(copy); errno = saved;
            return fail(error, n, "open path directory (symlinks forbidden)");
        }
        close(fd); fd = next;
        if (directory_ok(fd, 0) < 0) {
            close(fd); free(copy);
            return reject(error, n, "directory must give service owner rwx and forbid group/other write");
        }
        part = slash + 1;
    }
}

static int file_ok(const struct stat *st, uint64_t max_bytes,
                   char *error, size_t n) {
    if (!S_ISREG(st->st_mode)) return reject(error, n, "only regular files are supported");
    if (st->st_uid != geteuid() || !(st->st_mode & S_IWUSR) || (st->st_mode & 0022))
        return reject(error, n, "file must be owned and writable by service user, without group/other write");
    if (st->st_nlink != 1) return reject(error, n, "multiple hard links are forbidden");
    if (st->st_size < 0 || (uint64_t)st->st_size > max_bytes)
        return reject(error, n, "file exceeds configured size limit");
    if ((uint64_t)st->st_blocks * 512 < (uint64_t)st->st_size)
        return reject(error, n, "sparse or compressed files are unsupported");
    return 0;
}

void a13_job_free(a13_job *job) {
    if (!job) return;
    if (job->file_fd >= 0) close(job->file_fd);
    if (job->parent_fd >= 0) close(job->parent_fd);
    free(job->path); free(job->name); free(job);
}

int a13_prepare(int root_fd, const char *path, uint64_t max_bytes,
                a13_job **out, a13_info *info, char *error, size_t n) {
    if (!out || !info || !max_bytes) return reject(error, n, "invalid prepare arguments");
    *out = NULL;
    memset(info, 0, sizeof(*info));
    a13_job *job = calloc(1, sizeof(*job));
    if (!job) return fail(error, n, "allocate job");
    job->file_fd = job->parent_fd = -1;
    job->root_fd = root_fd;
    job->path = path ? strdup(path) : NULL;
    if (!job->path) { a13_job_free(job); return reject(error, n, "invalid path or allocation failure"); }
    job->parent_fd = parent_open(root_fd, path, &job->name, error, n);
    if (job->parent_fd < 0) { a13_job_free(job); return -1; }
    struct stat before;
    if (fstatat(job->parent_fd, job->name, &before, AT_SYMLINK_NOFOLLOW) < 0) {
        fail(error, n, "inspect file"); a13_job_free(job); return -1;
    }
    if (file_ok(&before, max_bytes, error, n) < 0) { a13_job_free(job); return -1; }
    job->file_fd = openat(job->parent_fd, job->name,
                          O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC);
    if (job->file_fd < 0) { fail(error, n, "open file"); a13_job_free(job); return -1; }
    if (flock(job->file_fd, LOCK_EX | LOCK_NB) < 0) {
        fail(error, n, "file is locked"); a13_job_free(job); return -1;
    }
    if (fstat(job->file_fd, &job->original) < 0) {
        fail(error, n, "stat file fd"); a13_job_free(job); return -1;
    }
    if (before.st_dev != job->original.st_dev || before.st_ino != job->original.st_ino) {
        reject(error, n, "file changed while opening"); a13_job_free(job); return -1;
    }
    if (file_ok(&job->original, max_bytes, error, n) < 0) { a13_job_free(job); return -1; }
    struct statfs fs;
    if (fstatfs(job->file_fd, &fs) < 0) {
        fail(error, n, "inspect filesystem"); a13_job_free(job); return -1;
    }
    job->info.device = (uint64_t)job->original.st_dev;
    job->info.inode = (uint64_t)job->original.st_ino;
    job->info.size = (uint64_t)job->original.st_size;
    job->info.filesystem_type = (uint64_t)(unsigned long)fs.f_type;
    *info = job->info; *out = job;
    return 0;
}

static int same_time(struct timespec a, struct timespec b) {
    return a.tv_sec == b.tv_sec && a.tv_nsec == b.tv_nsec;
}

static int validate(a13_job *job, int unchanged, char *error, size_t n) {
    struct stat fdst, pathst, oldparent, newparent;
    char *name = NULL;
    int parent = parent_open(job->root_fd, job->path, &name, error, n);
    if (parent < 0) return -1;
    int rc = fstat(parent, &newparent);
    if (!rc) rc = fstat(job->parent_fd, &oldparent);
    if (!rc) rc = fstatat(parent, name, &pathst, AT_SYMLINK_NOFOLLOW);
    int saved = errno;
    free(name); close(parent); errno = saved;
    if (rc < 0 || fstat(job->file_fd, &fdst) < 0) return fail(error, n, "revalidate file");
    struct stat *old = &job->original;
    if (newparent.st_dev != oldparent.st_dev || newparent.st_ino != oldparent.st_ino ||
        pathst.st_dev != old->st_dev || pathst.st_ino != old->st_ino ||
        fdst.st_dev != old->st_dev || fdst.st_ino != old->st_ino ||
        fdst.st_size != old->st_size || fdst.st_nlink != 1 ||
        fdst.st_uid != old->st_uid || fdst.st_mode != old->st_mode ||
        (unchanged && (!same_time(fdst.st_mtim, old->st_mtim) ||
                       !same_time(fdst.st_ctim, old->st_ctim))))
        return reject(error, n, "prepared file or path changed; request a new plan");
    return 0;
}

static int sync_fd(int fd) {
    int rc;
    do { rc = fsync(fd); } while (rc < 0 && errno == EINTR);
    return rc;
}

int a13_execute(a13_job *job, uint32_t passes, uint32_t zero,
                a13_info *info, char *error, size_t n) {
    if (!job || !info) return reject(error, n, "invalid job or result");
    *info = job->info;
    if (job->attempted) return reject(error, n, "job was already attempted");
    job->attempted = 1;
    if (passes < 1 || passes > 7 || zero > 1)
        return reject(error, n, "expected 1..7 random passes and zero flag 0 or 1");
    if (validate(job, 1, error, n) < 0) return -1;
    unsigned char buffer[65536];
    for (uint32_t pass = 0; pass < passes + zero; ++pass) {
        off_t offset = 0;
        while (offset < job->original.st_size) {
            off_t remaining = job->original.st_size - offset;
            size_t count = remaining > (off_t)sizeof(buffer) ? sizeof(buffer) : (size_t)remaining;
            if (pass < passes) {
                size_t filled = 0;
                while (filled < count) {
                    ssize_t got = getrandom(buffer + filled, count - filled, 0);
                    if (got < 0 && errno == EINTR) continue;
                    if (got <= 0) { if (!got) errno = EIO; return fail(error, n, "getrandom (file may be partially overwritten)"); }
                    filled += (size_t)got;
                }
            } else memset(buffer, 0, count);
            size_t written = 0;
            while (written < count) {
                ssize_t got = pwrite(job->file_fd, buffer + written, count - written,
                                      offset + (off_t)written);
                if (got < 0 && errno == EINTR) continue;
                if (got <= 0) { if (!got) errno = EIO; return fail(error, n, "pwrite (file may be partially overwritten)"); }
                written += (size_t)got; info->bytes_written += (uint64_t)got;
                job->info = *info;
            }
            offset += (off_t)count;
        }
        if (sync_fd(job->file_fd) < 0) return fail(error, n, "fsync file (overwrite durability uncertain)");
        if (pass < passes) info->random_passes++; else info->zero_pass = 1;
        job->info = *info;
        if (validate(job, 0, error, n) < 0) return -1;
    }
    if (unlinkat(job->parent_fd, job->name, 0) < 0)
        return fail(error, n, "unlinkat (file overwritten but not removed)");
    info->unlinked = 1;
    job->info = *info;
    if (sync_fd(job->parent_fd) < 0)
        return fail(error, n, "fsync parent (file removed; deletion durability uncertain)");
    info->directory_synced = 1;
    job->info = *info;
    return 0;
}

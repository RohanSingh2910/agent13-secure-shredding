#ifndef AGENT13_SHRED_H
#define AGENT13_SHRED_H

#include <stdint.h>

/* Linux-only ABI v1. All paths are relative to an already-open root directory.
 * Caller owns root_fd; it must be current-user-owned and mode 0700.
 * Never run as root. Functions return an agent13_code, not errno.
 * Error details are caller-owned, have no heap allocations, and are optional.
 */
enum agent13_code {
    AG13_OK = 0, AG13_INVALID = 1, AG13_IO = 2, AG13_UNSAFE = 3,
    AG13_CHANGED = 4, AG13_BUSY = 5
};

typedef struct {
    uint64_t device, inode, size;
    int64_t mtime_sec, mtime_nsec, ctime_sec, ctime_nsec;
    uint32_t mode, uid, links, reserved;
} agent13_file_info;

typedef struct {
    int32_t code, system_errno;
    uint64_t bytes_written;
    int32_t unlinked, reserved;
    char message[256];
    /* Relative to root_fd; nonempty only if staging could not be restored. */
    char recovery_path[4096];
} agent13_error;

/* ABI version used by ctypes to reject an incompatible shared library. */
uint32_t agent13_abi_version(void);

/* Inspect a readable/writable regular file, no symlinks or multiple links.
 * Opens without truncation. No contents or namespace modifications.
 */
int agent13_inspect_at(int root_fd, const char *relative_path,
                      agent13_file_info *info, agent13_error *error);

/* DESTRUCTIVE: caller must obtain explicit confirmation before invoking.
 * Revalidate expected fingerprint, lock, stage privately, overwrite one random
 * pass, fsync file, unlink, then fsync directories. Handles EINTR/short writes.
 * No physical-erasure guarantee. On failure data may be partly overwritten;
 * inspect bytes_written, unlinked, recovery_path. Never retry automatically.
 * Process death can leave .agent13-stage-* recovery directories.
 */
int agent13_shred_at(int root_fd, const char *relative_path,
                    const agent13_file_info *expected, agent13_error *error);

#endif

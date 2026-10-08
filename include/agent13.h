#ifndef AGENT13_H
#define AGENT13_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define A13_ABI_VERSION 1
#define A13_ERROR_SIZE 256

typedef struct a13_job a13_job;
typedef struct {
    uint64_t device;
    uint64_t inode;
    uint64_t size;
    uint64_t filesystem_type;
    uint64_t bytes_written;
    uint32_t random_passes;
    uint32_t zero_pass;
    uint32_t unlinked;
    uint32_t directory_synced;
} a13_info;

/* Returns a directory fd or -1. Root must be owned by this non-root UID and
 * mode 0700. Child directories must give owner rwx, without group/other write.
 * Caller closes the returned fd. */
int a13_root_open(const char *root, char *error, size_t error_size);
uint32_t a13_abi_version(void);
/* Preparation changes no file contents, but holds an exclusive advisory lock.
 * Paths must be relative, without '.', '..', empty components or symlinks.
 * Root fd must remain open until all jobs have been freed. Returns 0 or -1. */
int a13_prepare(int root_fd, const char *path, uint64_t max_bytes,
                a13_job **job, a13_info *info, char *error, size_t error_size);
/* Destructive: the caller MUST obtain informed authorization for this exact
 * prepared file. One attempt per job; failures may leave overwritten data.
 * Inspect info even on failure, especially unlinked and bytes_written.
 * This is best-effort logical overwriting, not guaranteed physical erasure. */
int a13_execute(a13_job *job, uint32_t passes, uint32_t zero_pass,
                a13_info *info, char *error, size_t error_size);
void a13_job_free(a13_job *job);

#ifdef __cplusplus
}
#endif
#endif

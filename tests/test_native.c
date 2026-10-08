#define _GNU_SOURCE
#include "agent13.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Link-time fault injection checks real failure stages without changing the
 * production engine. All target files are newly created in a private /tmp dir. */
static int fault, write_calls, sync_calls, random_calls;
enum { NONE, PARTIAL_WRITE, FILE_SYNC, DIR_SYNC, RANDOM_FAIL, UNLINK_FAIL, SHORT_IO };
ssize_t __real_pwrite64(int, const void *, size_t, off_t);
int __real_fsync(int);
ssize_t __real_getrandom(void *, size_t, unsigned int);
int __real_unlinkat(int, const char *, int);

ssize_t __wrap_pwrite64(int fd, const void *buf, size_t n, off_t offset) {
    ++write_calls;
    if (fault == PARTIAL_WRITE && write_calls > 1) { errno = EIO; return -1; }
    if (fault == SHORT_IO && write_calls == 1) { errno = EINTR; return -1; }
    if ((fault == PARTIAL_WRITE || fault == SHORT_IO) && n > 17) n = 17;
    return __real_pwrite64(fd, buf, n, offset);
}
int __wrap_fsync(int fd) {
    ++sync_calls;
    if ((fault == FILE_SYNC && sync_calls == 1) || (fault == DIR_SYNC && sync_calls == 2)) {
        errno = EIO; return -1;
    }
    return __real_fsync(fd);
}
ssize_t __wrap_getrandom(void *buf, size_t n, unsigned int flags) {
    ++random_calls;
    if (fault == RANDOM_FAIL) { errno = EIO; return -1; }
    if (fault == SHORT_IO && random_calls == 1) { errno = EINTR; return -1; }
    if (fault == SHORT_IO && n > 11) n = 11;
    return __real_getrandom(buf, n, flags);
}
int __wrap_unlinkat(int fd, const char *name, int flags) {
    if (fault == UNLINK_FAIL) { errno = EACCES; return -1; }
    return __real_unlinkat(fd, name, flags);
}

int main(void) {
    assert(geteuid() != 0);
    char root[] = "/tmp/a13-c-XXXXXX", error[A13_ERROR_SIZE];
    assert(mkdtemp(root));
    int root_fd = a13_root_open(root, error, sizeof(error));
    assert(root_fd >= 0 && a13_abi_version() == A13_ABI_VERSION);
    unsigned char original[257], readback[257];
    memset(original, 'X', sizeof(original));
    for (int scenario = NONE; scenario <= SHORT_IO; ++scenario) {
        int observer = openat(root_fd, "sample", O_CREAT | O_EXCL | O_RDWR, 0600);
        assert(observer >= 0 && write(observer, original, sizeof(original)) == (ssize_t)sizeof(original));
        a13_job *job = NULL;
        a13_info info;
        assert(a13_prepare(root_fd, "sample", 4096, &job, &info, error, sizeof(error)) == 0);
        assert(info.size == sizeof(original));
        assert(pread(observer, readback, sizeof(readback), 0) == (ssize_t)sizeof(readback));
        assert(!memcmp(original, readback, sizeof(original)));
        fault = scenario; write_calls = sync_calls = random_calls = 0;
        int rc = a13_execute(job, 1, scenario == NONE, &info, error, sizeof(error));
        if (scenario == NONE || scenario == SHORT_IO) {
            assert(rc == 0 && info.unlinked && info.directory_synced);
            assert(info.bytes_written == sizeof(original) * (scenario == NONE ? 2 : 1));
            assert(pread(observer, readback, sizeof(readback), 0) == (ssize_t)sizeof(readback));
            if (scenario == NONE) {
                for (size_t i = 0; i < sizeof(readback); ++i) assert(readback[i] == 0);
            } else assert(memcmp(original, readback, sizeof(original)));
        } else {
            assert(rc == -1 && error[0]);
            if (scenario == PARTIAL_WRITE) assert(info.bytes_written == 17 && !info.unlinked);
            if (scenario == RANDOM_FAIL) assert(info.bytes_written == 0 && !info.unlinked);
            if (scenario == FILE_SYNC || scenario == UNLINK_FAIL)
                assert(info.bytes_written == sizeof(original) && !info.unlinked);
            if (scenario == DIR_SYNC) assert(info.unlinked && !info.directory_synced);
        }
        a13_info again;
        assert(a13_execute(job, 1, 0, &again, error, sizeof(error)) == -1);
        assert(again.bytes_written == info.bytes_written && again.unlinked == info.unlinked);
        a13_job_free(job);
        fault = NONE;
        if (!info.unlinked) assert(unlinkat(root_fd, "sample", 0) == 0);
        close(observer);
    }
    close(root_fd);
    assert(rmdir(root) == 0);
    puts("C API: success, preview, zero pass, short I/O, EINTR, partial write, random failure, file/parent fsync failure, unlink failure and single-use job checks passed.");
    return 0;
}

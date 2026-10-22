#define _GNU_SOURCE
#include "agent13_shred.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

/* Linker wrappers inject realistic failures into the production C code. */
static int short_io, random_calls, write_calls, sync_calls, fail_sync;
static int zero_write, fail_random, race_name, block_restore;
static int new_source;
ssize_t __real_write(int, const void *, size_t);
ssize_t __real_getrandom(void *, size_t, unsigned int);
int __real_fsync(int);
int __real_renameat2(int, const char *, int, const char *, unsigned int);

ssize_t __wrap_write(int fd, const void *buffer, size_t size)
{
    if (zero_write) return 0;
    if (short_io && ++write_calls == 1) { errno = EINTR; return -1; }
    if (short_io && size > 173) size = 173;
    return __real_write(fd, buffer, size);
}

ssize_t __wrap_getrandom(void *buffer, size_t size, unsigned int flags)
{
    if (fail_random && ++random_calls > 1) { errno = EIO; return -1; }
    if (short_io && ++random_calls == 1) { errno = EINTR; return -1; }
    if (short_io && size > 97) size = 97;
    return __real_getrandom(buffer, size, flags);
}

int __wrap_fsync(int fd)
{
    if (++sync_calls == fail_sync) { errno = EIO; return -1; }
    return __real_fsync(fd);
}

int __wrap_renameat2(int from, const char *source, int to,
                     const char *destination, unsigned int flags)
{
    if (race_name && strcmp(source, "target")) {
        race_name = 0;
        assert(renameat(from, source, from, "raced-original") == 0);
        int replacement = openat(from, source, O_WRONLY | O_CREAT | O_EXCL, 0600);
        assert(replacement >= 0);
        assert(__real_write(replacement, "replacement", 11) == 11);
        close(replacement);
    }
    if (block_restore && !strcmp(source, "target")) {
        int replacement = openat(to, destination, O_WRONLY | O_CREAT | O_EXCL, 0600);
        assert(replacement >= 0);
        close(replacement);
    }
    int rc = __real_renameat2(from, source, to, destination, flags);
    if (rc == 0 && new_source && strcmp(source, "target")) {
        int replacement = openat(from, source, O_WRONLY | O_CREAT | O_EXCL, 0600);
        assert(replacement >= 0);
        assert(__real_write(replacement, "new source", 10) == 10);
        close(replacement);
    }
    return rc;
}

static void reset(void)
{
    short_io = random_calls = write_calls = sync_calls = fail_sync = 0;
    zero_write = fail_random = race_name = block_restore = 0;
    new_source = 0;
}

static void sample(int root)
{
    int fd = openat(root, "sample", O_WRONLY | O_CREAT | O_EXCL, 0600);
    assert(fd >= 0);
    char bytes[180000];
    memset(bytes, 'A', sizeof(bytes));
    assert(__real_write(fd, bytes, sizeof(bytes)) == (ssize_t)sizeof(bytes));
    close(fd);
}

int main(void)
{
    char directory[] = "/tmp/agent13-native-XXXXXX";
    assert(mkdtemp(directory));
    int root = open(directory, O_RDONLY | O_DIRECTORY);
    assert(root >= 0);
    agent13_file_info info;
    agent13_error error;
    assert(agent13_abi_version() == 1);
    sample(root);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
    assert(info.size == 180000 && info.links == 1);
    assert(agent13_inspect_at(root, "../sample", &info, &error) == AG13_INVALID);
    assert(agent13_inspect_at(root, "/etc/passwd", &info, &error) == AG13_INVALID);
    assert(symlinkat("sample", root, "link") == 0);
    assert(agent13_inspect_at(root, "link", &info, &error) == AG13_UNSAFE);
    assert(unlinkat(root, "link", 0) == 0);
    assert(linkat(root, "sample", root, "hard", 0) == 0);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_UNSAFE);
    assert(unlinkat(root, "hard", 0) == 0);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
    agent13_file_info wrong = info;
    wrong.inode++;
    assert(agent13_shred_at(root, "sample", &wrong, &error) == AG13_CHANGED);

    /* Hold an fd after unlink to verify every byte was actually overwritten. */
    int observer = openat(root, "sample", O_RDONLY);
    assert(observer >= 0);
    reset(); short_io = 1;
    assert(agent13_shred_at(root, "sample", &info, &error) == AG13_OK);
    assert(error.bytes_written == 180000 && error.unlinked);
    char buffer[180000];
    assert(read(observer, buffer, sizeof(buffer)) == (ssize_t)sizeof(buffer));
    for (size_t i = 0; i < sizeof(buffer); i += 1000) {
        int changed = 0;
        for (size_t j = i; j < i + 1000; j++) changed |= buffer[j] != 'A';
        assert(changed);
    }
    close(observer);
    assert(faccessat(root, "sample", F_OK, 0) < 0 && errno == ENOENT);

    /* Zero-length writes, random failure and file fsync failure restore names. */
    for (int scenario = 0; scenario < 3; scenario++) {
        reset(); sample(root);
        assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
        if (scenario == 0) zero_write = 1;
        if (scenario == 1) fail_random = 1;
        if (scenario == 2) fail_sync = 4;
        assert(agent13_shred_at(root, "sample", &info, &error) == AG13_IO);
        assert(!error.unlinked && !error.recovery_path[0]);
        assert(faccessat(root, "sample", F_OK, 0) == 0);
        assert(unlinkat(root, "sample", 0) == 0);
    }
    reset(); sample(root);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
    race_name = 1;
    assert(agent13_shred_at(root, "sample", &info, &error) == AG13_CHANGED);
    assert(error.bytes_written == 0 && !error.recovery_path[0]);
    int fd = openat(root, "sample", O_RDONLY);
    assert(read(fd, buffer, sizeof(buffer)) == 11);
    assert(!memcmp(buffer, "replacement", 11));
    close(fd);
    assert(unlinkat(root, "sample", 0) == 0);
    assert(unlinkat(root, "raced-original", 0) == 0);

    reset(); sample(root);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
    fail_sync = 4; block_restore = 1;
    assert(agent13_shred_at(root, "sample", &info, &error) == AG13_IO);
    assert(error.bytes_written == info.size && error.recovery_path[0]);
    assert(unlinkat(root, error.recovery_path, 0) == 0);
    char *slash = strchr(error.recovery_path, '/');
    assert(slash); *slash = '\0';
    assert(unlinkat(root, error.recovery_path, AT_REMOVEDIR) == 0);
    assert(unlinkat(root, "sample", 0) == 0);
    reset(); sample(root);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
    new_source = 1;
    assert(agent13_shred_at(root, "sample", &info, &error) == AG13_OK);
    fd = openat(root, "sample", O_RDONLY);
    assert(fd >= 0 && read(fd, buffer, sizeof(buffer)) == 10);
    assert(!memcmp(buffer, "new source", 10));
    close(fd);
    assert(unlinkat(root, "sample", 0) == 0);
    reset(); sample(root);
    assert(agent13_inspect_at(root, "sample", &info, &error) == AG13_OK);
    fail_sync = 5;
    assert(agent13_shred_at(root, "sample", &info, &error) == AG13_IO);
    assert(error.unlinked && error.bytes_written == info.size);
    close(root);
    assert(rmdir(directory) == 0);
    puts("Native tests passed: safety, full random overwrite, short I/O, EINTR, failures, replacement race, recovery");
    return 0;
}

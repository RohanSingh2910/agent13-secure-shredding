#include "agent13.h"
#include <inttypes.h>
#include <stdio.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "Usage: %s PRIVATE_ROOT RELATIVE_FILE\n", argv[0]); return 2; }
    char error[A13_ERROR_SIZE];
    int root = a13_root_open(argv[1], error, sizeof(error));
    if (root < 0) { fprintf(stderr, "%s\n", error); return 1; }
    a13_job *job = NULL;
    a13_info info;
    if (a13_prepare(root, argv[2], 64 * 1024 * 1024, &job, &info, error, sizeof(error)) < 0) {
        fprintf(stderr, "%s\n", error); close(root); return 1;
    }
    printf("Preview: device=%" PRIu64 " inode=%" PRIu64 " size=%" PRIu64 " bytes\n",
           info.device, info.inode, info.size);
    puts("No contents changed. Physical erasure cannot be guaranteed.");
    a13_job_free(job);
    close(root);
    return 0;
}

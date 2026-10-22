# Safety design and technical limitations

Agent 13 is a classroom best-effort file overwriter, not a certified media
sanitization product. Do not demonstrate with real user data.

## Trust boundary and safeguards

- Ordinary current-user service; root execution is refused. Socket directory
  and allowed root must be private, owned directories with mode 0700. Socket
  mode 0600 and Linux SO_PEERCRED both restrict callers to that UID.
- A dedicated allowed root is explicitly configured. Python pins its
  descriptor. Absolute root components and relative parent components are
  opened without following symlinks. `..`, `.`, empty components, and reserved
  `.agent13-*` names are rejected. Cross-device traversal is rejected.
- C accepts only current-user regular files with one link, owner read/write
  permission, and no group/other write bits. Directories, devices, FIFOs,
  sockets, symlinks, and multiply linked files are rejected. Nested directories
  cannot grant group/other write permission.
- Inspection/preview do not alter contents or remove files. Execution requires
  an in-memory random 256-bit, expiring, single-use token bound to a relative
  path and file fingerprint. Tokens are consumed before native execution.
- C revalidates the fingerprint and takes a nonblocking advisory flock.
  It renames the source into a fresh private staging directory with
  RENAME_NOREPLACE, then checks that the staged file is the opened inode.
  A source-name replacement detected here is restored without overwrite.
  Only the held file descriptor is overwritten; only the private staged name
  is unlinked. A newly created name at the original path is left alone.
- Writes and randomness reads handle short results and EINTR. A zero write is
  an error. The file is fsynced before unlink; directories are fsynced for
  namespace persistence. File sizes are bounded at preview (64 MiB default),
  requests at 16 KiB, live tokens at 256; requests have a 2-second read deadline.
- One service handles requests serially, with root/socket-directory locks to
  prevent cooperating duplicate instances. It waits on a selector and a signal
  wakeup socket rather than polling in a busy loop.
- Logs contain action, request ID, outcome, error code, and byte count; no file
  contents, target paths, or confirmation tokens. Startup errors may mention
  configuration paths. JSON output is for the authorized caller.

## Residual risks

Malicious programs sharing the user's UID and root can access that user's
private directories, alter processes, steal tokens, or bypass this service.
They are outside this security boundary. A same-UID program can call preview
and execute: tokens prevent accidental execution and replay, and do not prove
human intent. Future automation must obtain explicit user approval itself.

flock is advisory. Programs that already have a file open may continue reading
or writing it; the service cannot revoke those descriptors. Stop writers first.
The fingerprint catches common replacements and modifications but does not
constitute a content hash or prevent every concurrent modification. ACLs,
privileged mount changes, exotic/network filesystems, and hostile same-UID
namespace manipulation are outside the tested model. Use local Linux storage
supporting renameat2 RENAME_NOREPLACE and directory fsync; the service fails
closed when required operations are unsupported, with no unsafe fallback.

The random overwrite covers the original logical file size once. It does not
wipe filename history, old file versions, unused block slack, old extents,
memory/swap copies, application caches, replicas, snapshots, or backups.
Sparse holes may allocate storage; insufficient space can leave a partial
overwrite. Hardware errors, power loss, or SIGKILL may interrupt processing.
Errors report bytes written, whether unlink occurred, and any staging recovery
path. See [recovery](DEMO.md#failure-and-recovery).

## Storage limits

SSDs can redirect writes through wear leveling; filesystem snapshots and copy
on write can retain prior blocks; data journaling, compression, remapped
sectors, backups, and mirrors can retain copies. Extra passes cannot remove
copies that the file API cannot address. These limitations are described in
the [GNU shred manual](https://www.gnu.org/s/coreutils/manual/html_node/shred-invocation.html).
Agent 13 cannot detect or certify physical erasure on the underlying device.
Successful tests prove logical overwrite and unlink behavior only.

The [Linux fsync manual](https://man7.org/linux/man-pages/man2/fsync.2.html)
explains why file syncing and directory syncing are separate. The
[Linux rename manual](https://man7.org/linux/man-pages/man2/rename.2.html)
documents the no-replace rename flag used for staging and restoration.

## Linux operations used

| C header | Operations and purpose |
| --- | --- |
| `fcntl.h` | openat, descriptor-relative resolution, O_NOFOLLOW, CLOEXEC |
| `sys/stat.h` | fstat/fstatat, type/identity/ownership/link count, mkdirat |
| `sys/file.h` | flock, cooperative exclusive locking |
| `sys/random.h` | getrandom, overwrite bytes and random staging names |
| `stdio.h`, `linux/fs.h` | renameat2 with RENAME_NOREPLACE |
| `unistd.h` | write, fsync, unlinkat, close, geteuid |
| `errno.h` | OS failure reporting, EINTR handling |

The public C API returns AG13_OK, AG13_INVALID, AG13_IO, AG13_UNSAFE,
AG13_CHANGED, or AG13_BUSY. Error structures carry the OS errno and partial
operation state. Python maps them to structured JSON without hiding failures.

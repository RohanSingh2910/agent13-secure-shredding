# Design and operating-system concepts

## Architecture

```text
User's natural-language command
             |
Class master shell + its LLM router + human approval flow
             |
Function-calling tools / CLI client
             | JSON over AF_UNIX, same UID
systemd --user -> persistent Python daemon
             | ctypes (public ABI version 1)
         C overwrite engine
             | openat, fstat, flock, getrandom, pwrite, fsync, unlinkat
      Existing Linux kernel interfaces
```

The daemon owns the opened allowed-root descriptor and prepared jobs. The CLI
only sends requests. No shell command is constructed from a user path; spaces,
Unicode and shell metacharacters are treated as filename characters. No external
LLM or network connection is needed by this module.

## File lifecycle

1. Open the configured root directory and validate its owner/private mode.
2. Walk relative path components with descriptor-relative `openat()` and
   `O_NOFOLLOW`; forbid absolute paths, `.`, `..` and empty components. Require
   owner rwx on each directory so a read-only parent is refused before writing.
3. Check the target's type, permissions, ownership, link count and allocation.
   Open without truncation, obtain an exclusive nonblocking advisory `flock`,
   record inode/device/size/mode/mtime/ctime, and hold descriptors in the plan.
4. Return a cryptographically unpredictable token bound to the caller UID and
   planned options. Default lifetime: 120 seconds. The plan itself does not
   overwrite contents. Cancel/expiry/daemon shutdown closes its descriptors.
5. When execution is authorized, consume the token before attempting mutation.
   Rewalk the path and compare parent, inode, metadata and timestamps. A changed
   target fails before writing. The C job also permits just one attempt.
6. For each random pass, repeatedly obtain random bytes and use positioned
   writes from offset zero to the captured logical file size. Both operations
   handle partial returns and `EINTR`. Use a fixed 64 KiB buffer. Sync the file
   after each pass. Optionally repeat with zeros.
7. Revalidate path identity and stable size/mode/link count between passes,
   unlink the checked name, then sync the parent directory. Report actual write
   counts, completed passes, unlink status and directory synchronization.

`O_NOFOLLOW` applies only to the final component of an individual open, which is
why the implementation walks each component separately. See Ubuntu Jammy's
[openat documentation](https://manpages.ubuntu.com/manpages/jammy/man2/openat.2.html).

`getrandom(..., 0)` draws from the kernel's urandom source without needing a
`/dev/urandom` descriptor. It can return short results or be interrupted, so the
engine fills the entire buffer in a loop. See Ubuntu Jammy's
[getrandom documentation](https://manpages.ubuntu.com/manpages/jammy/man2/getrandom.2.html).

File data synchronization and directory-entry synchronization are distinct. An
explicit parent-directory `fsync()` follows `unlinkat()` so deletion durability
is checked separately. See Ubuntu Jammy's
[fsync documentation](https://manpages.ubuntu.com/manpages/jammy/man2/fsync.2.html).

## Trust model and limits

The administrator and other processes running as the service UID are trusted.
The socket directory is 0700, socket 0600, and Linux `SO_PEERCRED` validates the
client UID. Tokens are bound to that UID, not one PID: plan and execute normally
come from two different CLI processes. The advisory file lock only blocks
cooperating processes. Same-user processes and root can still mutate files or
directories. In particular, Linux has no general atomic “unlink this pathname
only if it still names this open inode” operation. The final identity check and
unlink have a small race window. Use a dedicated directory without uncooperative
concurrent writers. This is a lab service, not a security boundary against its
own account or a privileged adversary.

The runtime and allowed-root paths are trusted configuration, resolved/opened at
startup. The pinned root descriptor is the root identity for that daemon's
lifetime; stop the service before renaming/replacing its configured root.
Do not place writable mount points or bind mounts with unrelated data under it.

The C API is lower level: its caller supplies authorization, limits and handle
lifecycle. It is not thread safe on one job, and callers must never use a freed
job or close its root fd early. The service serializes access. Raw C callers can
bypass the master's approval flow because they already possess that user's file
permissions; the C API does not impersonate a user or elevate privileges.

The engine overwrites the current logical file length. It does not pad to an
allocation boundary, wipe old extents or filenames, locate backup copies, issue
device secure-erase commands, or erase entire disks. Allocation accounting
rejects many sparse/compressed files, but is not a storage-security detector.
The filesystem magic value is diagnostic only. Even an ordinary-looking file
may use CoW, snapshots or SSD storage. Extra passes cannot repair that property.

## Failures

Writes are irreversible once started. A random/write/sync/revalidation failure
keeps the directory entry if unlink has not yet happened. The file can already
be partially or completely overwritten. An unlink failure leaves an overwritten
file. A parent-sync failure means the file was unlinked, but persistent deletion
is uncertain. The JSON error includes the C result structure on execution
failure. Never classify every failure as “nothing happened.”

Process death can prevent a reply after successful execution. There is no
persistent job database or rollback. Tokens disappear on restart. Check the
file and journal before making a new plan after a lost response. Logs contain
operation counts and status, without filenames, file contents or tokens.

## Service choices

One sequential daemon keeps file-handle ownership and cleanup simple. Default
64 MiB files and at most 32 plans bound routine resource use. A client may take
two seconds to send a request; requests and replies are capped at 16 KiB. Large
overwrites temporarily block other requests. A signal requests graceful shutdown
after the current call returns. systemd may force termination after 300 seconds.

The user unit includes no-new-privileges, an empty capability set, AF_UNIX-only
sockets and a private runtime directory. The account's ordinary file permissions
remain the filesystem boundary; this is not a chroot. The directives are chosen
from [Jammy's systemd execution configuration](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.exec.5.html).

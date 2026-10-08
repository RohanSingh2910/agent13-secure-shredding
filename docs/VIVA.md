# Lab explanation and demonstration

## One-minute explanation

“My module is agent 13, the Secure Shredding Agent. It runs as a systemd user
service, so its process stays alive without a graphical interface. A command-line
client and the class shell send structured tool requests over a Unix socket.
The class LLM chooses tools; my module executes them deterministically.

“The core is written in C and exposes a header. It opens and locks a regular
file, overwrites the current contents with kernel-generated random bytes,
synchronizes the writes, unlinks the name, and synchronizes the directory. A
preview token identifies the exact file to authorize. The agent rejects unsafe
paths and unusual file types and does not run as root.

“This is best-effort file overwriting. It cannot guarantee removal of old SSD
blocks, snapshots or backups. The result reports logical overwriting and deletion
without claiming that physical recovery is impossible.”

## Concepts to explain

| Concept | Where it appears |
|---|---|
| Process versus service | CLI exits after a reply; daemon PID persists under systemd |
| IPC | `AF_UNIX` socket transfers JSON; `SO_PEERCRED` identifies the peer UID |
| User versus kernel space | C calls existing Linux operations; the program is not a kernel module |
| C headers | `agent13.h` defines the ABI; `fcntl.h`, `sys/stat.h`, `sys/file.h`, `sys/random.h`, `unistd.h` declare OS interfaces |
| File descriptors | Root, parent and target fds anchor planned operations |
| Inode and links | `st_dev`/`st_ino` identify the file; `st_nlink != 1` is refused |
| Positioned writes | `pwrite` overwrites chunks without depending on the shared file offset |
| Kernel randomness | `getrandom` obtains bytes from the kernel's urandom source |
| Synchronization | `fsync(file)` flushes each pass; `fsync(parent)` follows unlink |
| Locks | `flock` prevents cooperating callers from preparing the same file concurrently |
| Error handling | Partial I/O/EINTR loops and status flags distinguish partial writes from completed unlink |
| Least privilege | Non-root account, private socket/root, no privilege escalation |

## Common questions

**Why C and Python together?** C handles the actual file operations and provides
the public header. Python supplies socket/JSON/CLI glue from its standard library;
no missing Python package prevents the core from working. Other class agents can
call the C ABI directly or use the socket client.

**Why not just remove a file?** Unlink removes a name and eventually releases its
storage. This agent overwrites the current logical data before removing the name.
Overwriting still depends on how the filesystem and hardware store data.

**Why hold an inode open?** A pathname can be replaced after preview. The fd pins
the planned file while identity/timestamp checks detect common changes. Open fds
also illustrate that removing a name does not immediately destroy the inode.

**Is the lock a complete protection?** No. Advisory locks require cooperation.
A same-user process or administrator can bypass them. Private directories and
checks reduce accidents, but the service assumes trusted same-user processes.

**Is this a new syscall?** No. The assignment permits one package/API/system-call
integration. Our user-space API uses existing syscalls and needs no kernel change.

**Why three passes?** It is a configurable lab default. One random pass is
available. Pass counts do not create a guarantee on SSD/CoW/snapshot storage.

**What happens on failure?** Before-writing refusal leaves contents unchanged.
After-writing failure can leave partial overwrite. After-unlink sync failure can
leave a removed name with uncertain crash durability. The result carries flags.

## Suggested live demonstration

1. Show `ROADMAP.md`, the C header and service unit.
2. Start the installed user service and call `agent13 health` twice. Show the same
   PID in both replies and in `systemctl --user status agent13`.
3. Create only the synthetic example from the README.
4. Call `plan`; show that the file still exists and read the operation/limits.
5. Execute the token; show unlink/write/sync flags and the absent example name.
6. Try `plan ../outside.txt` and show a refusal.
7. Run `make check` to demonstrate path, token and failure behavior.
8. Show the tool definitions and adapter used by the master shell.

Submission files: roadmap, source/header, Makefile, systemd unit, tests, Debian
packaging, integration schema/adapter, and these documents. Include the actual
Ubuntu test/package results when run in the lab; do not describe the local
CachyOS run as an Ubuntu run.

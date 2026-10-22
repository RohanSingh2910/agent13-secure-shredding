# Testing and verification evidence

Run as an ordinary user from the project directory:

```bash
make check
systemd-analyze --user --man=no verify systemd/agent13.service
./bin/agent13-demo
```

No Python packages are required. The shared library must be built before
running unittest directly: `make`, then `python3 -m unittest discover -s tests
-v`. `make native-test` runs only the C fault-injection harness.

## Recorded result — 15 October 2026

Verified on Ubuntu 22.04.5, x86-64, Python 3.10, GCC 11.4, Make 4.3:

| Check | Result |
| --- | --- |
| C11 shared-library build, all warnings treated as errors | PASS |
| Native C safety, overwrite, race and fault-injection harness | PASS |
| Python unittest suite | **28 tests passed** |
| Python compilation check | PASS |
| systemd user-unit verification | PASS, no diagnostics |
| Independent background service + real CLI demonstration | PASS |

The development sandbox had no GCC/Make or development headers, so Ubuntu
packages were downloaded with permission and extracted under
`/tmp/agent13-toolchain`. No packages were installed into the host system.
The complete suite was run with:

```text
/tmp/agent13-toolchain/root/usr/bin/make CC=/tmp/agent13-toolchain/bin/gcc check
```

The temporary compiler wrapper supplies that extracted toolchain's paths.
This is a development-environment workaround, not a runtime dependency; normal
Ubuntu/Debian installations use `build-essential` and `make check` as above.
Unix socket creation was blocked by the sandbox, so service tests and the
demonstration ran outside it with permission. Those runs still used ordinary
UID 1000 and temporary directories, and did not install or enable host services.

Live installation/start/enable through the systemd user manager was **not
performed**, as instructed. The unit passed `systemd-analyze --user --man=no
verify`. Process-level startup, real Unix IPC, SIGTERM/SIGINT shutdown, socket
cleanup, and restart were exercised directly. Follow README.md to opt into a
real systemd installation on the student's machine.

## Coverage

| Required behavior | Evidence |
| --- | --- |
| C compilation | Make builds shared library and native harness using C11 and -Werror |
| Python-C signatures and calls | Wrapper tests inspect, error conversion, empty and normal deletion |
| Service health and Unix socket IPC | Real subprocess service and client in SocketTests |
| File inspection and dry-run preview | Native metadata and byte-for-byte unchanged data checks |
| Actual shredding | Native test holds an fd after unlink and checks overwritten bytes across the full 180,000-byte file; CLI deletes its own sample |
| Missing file and permission errors | Wrapper/socket tests; chmod 000/0400 is rejected with EACCES |
| Directory, FIFO, symlink and hardlink rejection | Native, wrapper, agent and socket tests |
| Parent symlinks, traversal and outside targets | Descriptor walk tests; outside canary remains unchanged |
| Invalid, expired, reused and mismatched tokens | Agent unit tests with injected monotonic clock, CLI replay rejection |
| Changed file after preview | Replacement, append, chmod, new symlink and hardlink tests |
| Path race during execution | Linker-injected replacement before staging; no data overwritten |
| New source name after staging | Native test verifies newly created original pathname survives deletion |
| Short writes/short random reads/EINTR | Linker wrappers force all three during a real full overwrite |
| I/O errors and recovery | Inject zero write, random failure, pre/post-unlink fsync failures, restore conflict; check bytes/unlinked/recovery path |
| Advisory file locking | Wrapper test rejects a busy file without mutation |
| Unauthorized peers | SO_PEERCRED UID policy tested with controlled credentials; socket mode checked in live IPC tests |
| JSON request and response validation | Strict schema, malformed UTF-8, duplicate keys, deep nesting, NaN, invalid versions/IDs/types, size/framing tests |
| Graceful shutdown and restart | Real SIGTERM/SIGINT, exit 0, socket removal, restart health, tokens invalidated |
| Resource limits and duplicate services | File/token limits, expiry cleanup, second instance refused |
| CLI errors and logs | Missing-service exit 2; server error exit 1; tokens/paths absent from operation logs |

The foreign-UID authentication check is a controlled unit test, not a live
second-user test: tests do not create accounts or require root. Expiry uses a
fake monotonic clock to check the exact boundary without a sleep. Storage
failures use deterministic linker injection, not actual disk damage.

## Safe testing rules and practical limits

Tests create their own temporary directories and files through tempfile and
mkdtemp. They never accept an arbitrary target supplied by the user. The demo
also chooses its own temporary root and cleans up only its generated files.
Never redirect tests toward real documents or system directories.

Passing these tests proves application behavior at the Linux file API level.
It does not prove that SSD flash cells, snapshots, backups, or physical media
contain no previous data. These are local-Linux tests, not a filesystem/hardware
certification or a malicious same-UID attacker assessment. See SECURITY.md.

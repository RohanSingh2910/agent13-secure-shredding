# Validation record

Date: 8 October 2026. Target: Ubuntu 22.04.5 LTS.

The available host is **CachyOS Linux**, x86_64, kernel 7.2.9, GCC 16.2.1,
Python 3.14.7 and systemd 262. Ubuntu 22.04's compiler/runtime have not been
executed here. Local native binaries are development artifacts and must be
rebuilt on the Ubuntu target.

This record is updated after verification; do not infer completed Ubuntu or
Debian package testing from the presence of source configuration or CI.

## Completed local checks

- Strict C11 compilation with `-Wall -Wextra -Wpedantic -Werror` passed.
- Public C API test passed: real overwrite/unlink, unchanged preview, zero pass,
  single-use handles, short random/write results, EINTR, random failure, partial
  write failure, file sync failure, parent sync failure and unlink failure.
- Final `make check` passed: the C fault suite and **42 Python tests**, including real daemon socket/CLI requests,
  token lifecycle, changed/replaced files, symlinks, hard links, traversal,
  permissions, sparse/oversized files, lock contention, malformed requests,
  wrong-peer UID refusal, stale-socket recovery after SIGKILL, and a staged
  installation of both daemon and CLI, and the complete disposable demonstration.
  The peer refusal uses a simulated foreign
  UID; cross-user access was not attempted on this host.
- AddressSanitizer, UndefinedBehaviorSanitizer and LeakSanitizer passed the C
  success/fault tests. Clang's C static analyzer reported no findings.
- Python syntax compilation and tool-schema JSON validation passed. All 10
  Python source files parse with Python 3.10 grammar; this is a syntax check,
  not execution under Python 3.10.
- The generated systemd user unit passed `systemd-analyze --user verify` with
  no diagnostics on host systemd 262. It has not been installed/enabled in the
  host user manager. The daemon itself was launched and tested as a background
  process; actual Ubuntu systemd deployment remains a target-platform check.
- The Makefile installation was staged in temporary/workspace directories.
  No live user/system installation was performed.
- The locally compiled shared library's required glibc symbol versions are no
  newer than GLIBC_2.33. This is useful compatibility evidence, but does not
  replace rebuilding and running the tests on Ubuntu 22.04.
- Tests used disposable files in private temporary directories. The agent was
  not pointed at personal documents.

The sandbox refused Unix socket creation and systemd checker sockets; its tracing
also prevented the leak checker. Those checks were rerun with approved execution
outside that sandbox; no test was silently skipped.

## Target-platform validation

Run `make check` on a normal non-root Ubuntu 22.04 account. Run
`dpkg-buildpackage -us -uc -b`, inspect the package and install it in the lab
VM. Then verify `systemctl --user enable --now agent13.service`, repeated
`agent13 health` calls, and the README synthetic-file demonstration. Record
the actual target tool versions and outcomes here.

`.github/workflows/ubuntu22.yml` supplies build/test/unit/package checks on
Ubuntu 22.04. It is provided but has not run in this workspace.

Debian build tools (`dpkg-buildpackage`, `dpkg-deb`, `dh`) and an Ubuntu container
runtime were unavailable on this host. The Debian package configuration is
provided; no `.deb` artifact or successful Debian build is claimed.

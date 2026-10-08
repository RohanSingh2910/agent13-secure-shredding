# Part 13: Secure Shredding Agent — roadmap

## Assignment and scope

Build agent 13 of the class Agentic Shell. It must run as a background process
under systemd, expose CLI/function-calling tools, and use a C header and native
Linux operations. Target: Ubuntu 22.04.5 LTS (Jammy), Python 3.10, glibc 2.35,
GCC 11, systemd 249. No GUI, Python third-party dependencies, root service, or
kernel module is required. The master shell supplies natural-language routing;
this module supplies deterministic tools. A userspace API satisfies the
integration requirement without introducing a new kernel system call.

## Development steps (in order)

1. **Define the contract.** Document the allowed directory, per-user service,
   inspect/execute/cancel tools, JSON Unix-socket protocol, and C API. An inspect
   operation returns a short-lived, single-use token bound to a pinned file.
2. **Implement the C engine.** Walk paths relative to an opened directory with
   `openat()` and `O_NOFOLLOW`; reject traversal, symlinks, non-regular files,
   multiple hard links, unsafe permissions, sparse files, and oversized files.
   Lock the descriptor, overwrite in bounded chunks with `getrandom()`, call
   `fsync()` after each pass, optionally add zeros, verify the target, then
   `unlinkat()` and sync its parent. Publish `include/agent13.h` and a shared
   library for Python and C integration.
3. **Implement the service and CLI.** Use Python's standard library for local
   JSON requests and C bindings. Authenticate clients with `SO_PEERCRED`, limit
   requests and pending plans, expire tokens, handle shutdown, and report partial
   failures honestly. The CLI previews a plan before explicitly executing it.
4. **Integrate with the class shell.** Publish function-calling JSON schemas and
   a Python adapter. The master LLM selects tools; the human approves the exact
   file in the master shell before it sends the token to execute. Do not put an
   API key or an independent LLM inside the service.
5. **Package and document.** Supply a Makefile, systemd user service, Debian
   packaging, Ubuntu setup commands, a temporary-file demo, protocol examples,
   and a short explanation for the lab viva.
6. **Verify.** Build with strict compiler warnings; exercise real overwrite and
   unlink behavior only on disposable test files, check refusal paths, token
   lifecycle, malformed requests, daemon startup/shutdown, C API use, and unit
   syntax. Add an Ubuntu 22.04 CI job for target-platform validation.

## Acceptance criteria

- Service stays running between CLI requests and needs no UI or root privileges.
- C header/library performs the overwrite, synchronization, and unlink calls.
- Preview changes no contents; execute requires the preview token and an explicit
  acknowledgement that file overwriting cannot guarantee physical erasure.
- Paths stay within the configured private directory under the documented trust
  model; refusals leave test data unchanged.
- Structured replies identify success, failure, and possible partial overwrite.
- Classmates can invoke this agent through the socket adapter or C header without
  copying its implementation or coupling it to one LLM vendor.
- Build/test/package instructions work with Jammy's standard distribution tools.

## Limits that the implementation must explain

File-level overwriting is best effort. SSD wear leveling, copy-on-write,
snapshots, journaling, backups, previously open descriptors, and other copies can
retain data. This implementation overwrites the current logical file length;
it does not erase historical blocks, allocation slack, filenames in journals,
or an entire device. More passes cannot eliminate these limits.

The service trusts its own Unix account and the administrator. Descriptor
pinning, metadata checks, private directories, and advisory locks reduce races,
but cannot exclude an uncooperative process running as the same user (or root).
Do not run it on a directory being concurrently modified by such processes.

## Validation environment

The provided workspace is on CachyOS, not Ubuntu. Local checks can validate
behavior and source portability, but do not establish Ubuntu runtime or Debian
package validation. Rebuild native artifacts on Ubuntu 22.04; record actual test
results separately in `docs/VALIDATION.md`.

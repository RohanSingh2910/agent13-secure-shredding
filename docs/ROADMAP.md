# Agent 13 roadmap

## Introduction and problem

Agent 13 is the Secure Shredding Agent for a 35-agent Agentic Shell class
project. Ordinary unlinking removes a name without deliberately overwriting
the file's data. This agent overwrites regular-file data with random bytes,
flushes it, and unlinks the file. Overwriting **cannot guarantee complete data
erasure** on SSDs, copy-on-write filesystems, snapshots, backups, or journaled
storage. It is an educational, best-effort tool.

## Objectives and scope

Deliver a standalone, headless Ubuntu/Debian user service. A CLI sends JSON
over a Unix socket to Python, which calls a C shared library through ctypes.
Phase A is implemented now. The master shell, other 34 agents, central
registry, dispatcher, LLM, and natural-language routing are out of scope.

## Requirements

Functional requirements:

- Health and agent information; native file inspection.
- Dry-run preview issuing a short-lived, single-use confirmation token.
- Explicit confirmed overwrite, fsync, unlink, and directory fsync.
- Configured private allowed directory, confined descriptor-based traversal,
  regular-file checks, symlink/hardlink rejection, identity revalidation.
- Unix socket API, current-user authentication, structured results and logs.
- Graceful shutdown, systemd user unit, independent CLI and native interfaces.

Non-functional requirements: readable student-level code, standard-library
Python, bounded requests and token storage, serial destructive operations,
restrictive file/socket permissions, no busy loop, no root requirement, and
clear failure reporting (including partial overwrite and recovery paths).

## Hardware, software, and choices

Any Ubuntu/Debian Linux machine with a normal user account, writable local
storage, Python 3.10+, GCC supporting C11, Make, Linux headers, and systemd
user services. No GPU or cloud service is required. Use Python unittest,
ctypes, JSON, socket, and logging; C uses Linux/POSIX descriptors, getrandom,
flock, renameat2, write, fsync, and unlinkat. One random pass is the default;
additional passes do not solve storage-layer erasure limitations.

## Milestones (must be completed in this order)

| Step | Deliverable | Completion evidence |
| --- | --- | --- |
| 1 | Repository inspection | Existing files and instructions reviewed |
| 2 | This roadmap | Requirements and boundaries documented |
| 3 | Architecture and diagrams | Six diagrams plus ASCII overview |
| 4 | C backend/header and build | Shared library compiles with warnings as errors |
| 5 | C backend verification | Native disposable-file test passes |
| 6 | ctypes wrapper | Signatures and structured native errors mapped |
| 7 | Wrapper verification | Native inspection, errors, deletion tested |
| 8 | Background service | Validated JSON socket API and tokens |
| 9 | CLI | All required commands use IPC |
| 10 | systemd user configuration | Unit supplied and checked; no host install |
| 11 | Future interfaces | Provisional API, manifest, integration guide |
| 12 | Comprehensive verification | Temporary-file unittest suite and CLI demo |
| 13 | Final documentation | README, API, testing, demo, viva, limitations |

## Security requirements and testing plan

Use a dedicated, explicitly configured directory owned by the service user,
mode 0700. Reject traversal, unsupported types, symlinks, hardlinks, writable
shared directories, changed files, invalid tokens, and peers with another UID.
Securely stage an execution target into a private per-operation directory
before overwriting, so a competing source-name replacement is never unlinked.
Report recoverable staging failures without overwriting an unrelated file.
No test uses real user data. Native tests and Python unittest cover build,
ctypes, service/CLI IPC, schema errors, permissions, safety checks, tokens,
replacement, and shutdown. Run destructive tests only in temporary directories.
Record actual results in docs/TESTING.md. Root and malicious processes sharing
the service UID, uncooperative writers, hardware failure, and storage copies
are outside the erasure/security guarantee; explain these limits clearly.

## Future integration strategy

Expose CLI, versioned JSON Unix socket API, Python wrapper, C header/library,
and a proposed tool manifest. Keep protocol translation in one module. A
future class master will call these interfaces, preserving Agent 13's safety
checks. The class-wide contract is provisional and not implemented here.

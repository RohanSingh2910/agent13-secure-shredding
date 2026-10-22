# Safe demonstration and viva preparation

## Three-minute standalone professor demonstration

From the repository, after building:

```bash
make check
./bin/agent13-demo
```

The demo creates disposable data inside its own temporary directory. It
launches Agent 13 in a separate session without terminal input, calls the CLI,
and stops its own process. It installs no service and touches no real user file.

Expected key output (verified 15 October 2026):

```text
Service started independently, with no terminal input or master agent.
Health: ok
Inspected disposable file: 4000 bytes
Default dry-run preserved the file and returned a confirmation token.
Confirmed overwrite: 4000 bytes; file removed.
Token reuse rejected: INVALID_TOKEN
Graceful stop removed the socket.
Restart health: ok
Service logs (no paths, file contents, or tokens):
... service_ready, action outcomes, service_stopped ...
Demonstration complete; temporary files cleaned up.
```

Explain while demonstrating:

1. This is **Phase A**: the independent agent is a long-running process.
2. Show ARCHITECTURE.md: CLI → socket → service → ctypes → C → Linux.
3. Preview is dry-run. The token binds the target's identity and expires.
4. C handles overwrite, fsync, and unlink. The CLI does not delete files itself.
5. Reusing the token fails. Stop/restart works and clears tokens.
6. State the storage limitations: this is best-effort logical overwrite, not
   a guarantee that all physical copies have been erased.

## systemd demonstration after manual installation

Follow README.md's opt-in installation first. Then, as the ordinary user:

```bash
systemctl --user start agent13
systemctl --user status agent13 --no-pager
agent13ctl health
agent13ctl info
umask 077
printf 'Professor demo only\n' > "$HOME/.local/share/agent13/allowed/viva.txt"
agent13ctl inspect viva.txt
agent13ctl preview viva.txt
```

Copy the token from the preview, after checking the target, then:

```bash
agent13ctl shred viva.txt --confirm-token 'PASTE_TOKEN_FROM_PREVIEW'
test ! -e "$HOME/.local/share/agent13/allowed/viva.txt"
journalctl --user -u agent13.service -n 20 --no-pager
systemctl --user restart agent13
agent13ctl health
systemctl --user stop agent13
```

For startup at login, `systemctl --user enable agent13`; reverse with
`systemctl --user disable agent13`. All of these commands are operated by the
student, not executed automatically by development tooling.

## Failure and recovery

A rejected preview does not overwrite or unlink data. An execution failure
may occur **after some or all bytes were overwritten**, or after unlink.
Read `error.details.bytes_written`, `unlinked`, and `recovery_path`.

On most execution failures C restores the staged name without replacing a
newly created source file. Restoration does not undo overwritten bytes.
If a new source already exists, the staged file remains at the reported
relative `.agent13-stage-<random>/target` path. Do not automatically retry.

After a crash or forced termination, stop the service and inspect the allowed
root for `.agent13-stage-*` directories. Do not delete them blindly. The file
may be original, partly overwritten, or fully overwritten. The original name
is not persisted in a recovery journal. Identify recovery files manually;
the service intentionally does not expose reserved staging names as targets.
If appropriate, move a staged file to a new unused name inside the allowed
root using a no-clobber move (`mv -n`), remove the now-empty staging directory,
then restart and obtain a new preview for that name. An empty staging directory
can also remain if cleanup failed. Never treat it as proof of erasure.

SIGTERM/SIGINT finish an active operation before exiting; a slow filesystem
may delay stop. The user unit deliberately has no forced stop timeout. An
idle incomplete client request has a 2-second read deadline. A CLI timeout
does not cancel the service, so check the file and recovery directories before
deciding whether another operation is needed. Tokens never survive restart.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Shared library cannot load | Run make in this checkout; rebuild for this machine |
| Permission or unsafe-directory error | Use dedicated owned mode-0700 directories and mode-0600 samples |
| Connection refused / missing socket | Check service status, explicit socket path, and AGENT13_SOCKET |
| Token error | Preview again after checking the target; never reuse an old token |
| FILE_CHANGED | Investigate modification/replacement before obtaining another preview |
| FILE_BUSY | Stop the cooperating process holding the lock; do not override |
| Unsupported fsync/rename | Use a supported local Linux filesystem; no unsafe fallback is provided |
| User systemd bus unavailable | Run from a normal logged-in Ubuntu session, or use the independent demo |
| Sandbox forbids Unix sockets | Run the local test normally outside that sandbox; do not bypass product checks |

## Short viva answers

**Why a service?** The agent remains available as a background process and
accepts IPC requests without requiring a terminal or master shell.

**Why Python and C?** Python makes protocol, tokens, logging and CLI readable;
C demonstrates actual descriptor-based Linux operations through a public ABI.
ctypes connects them without an extra Python extension build system.

**What is a file descriptor?** A process-owned integer handle for an open
kernel object. Holding it avoids repeatedly resolving a mutable pathname.

**Why not just unlink?** Unlink removes a directory entry; it does not
deliberately overwrite the logical data first. Open handles may still exist.

**What does fsync do?** It asks the OS to persist file changes and waits for
the device's completion report. Directory fsync separately persists name
changes. It cannot remove snapshots or hidden hardware copies.

**How are symlinks and hardlinks handled?** Components are opened with
O_NOFOLLOW, and the opened file must be regular with exactly one link.

**Why private staging?** Checking an inode and then unlinking its original
path leaves a replacement race. Staging and rechecking isolate the name that
will be unlinked. A replacement at the original name is preserved.

**Does flock block all writers?** No. It is advisory; uncooperative processes
can still use existing descriptors. Stop other writers before shredding.

**Why one pass?** It demonstrates best-effort overwrite. More passes do not
solve SSD wear leveling, copy on write, snapshots, or backup retention.

**How will integration work?** A future class master calls the same v1.0
socket API or public client. The tool manifest is proposed metadata only;
there is no master shell, LLM router, registry, or dispatcher in Phase A.

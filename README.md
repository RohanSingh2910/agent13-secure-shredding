# Agent 13 — Secure Shredding Service

Part 13 of the Operating Systems Lab **Agentic Shell** project. A persistent
per-user systemd service exposes file-overwrite tools through a CLI and local
JSON API. The overwrite engine is C, with a public C header and shared/static
libraries. The daemon and CLI use Python's standard library only.

Read [ROADMAP.md](ROADMAP.md) first: it was written before implementation.
The assignment itself is in [Project_Overview.txt](Project_Overview.txt).

## What it does

`plan` opens and locks one eligible file without changing its contents. It returns
the path, inode, size, overwrite options, limitations, and a single-use token.
`execute` validates that prepared file, writes random bytes over its logical
contents in 64 KiB chunks, synchronizes each pass, optionally adds a zero pass,
unlinks the file, and synchronizes the parent directory. `cancel` releases the
plan. The service remains running between commands.

The default root is `~/agent13-vault`. Only relative paths inside that directory
are accepted. The service refuses to run as root. It rejects symlinks, hard-linked
files, directories, FIFOs, unsafe permissions, sparse/compressed files detected
by allocation accounting, and files larger than 64 MiB by default. It supports
one file per operation, without recursive deletion or wildcard expansion.

**Physical erasure is not guaranteed.** File overwriting can leave copies on
SSDs, copy-on-write storage, snapshots, backups and journals. The agent reports
best-effort logical overwrite and unlink, never “cannot be recovered.” See the
[GNU shred manual](https://www.gnu.org/software/coreutils/manual/html_node/shred-invocation.html)
and [docs/DESIGN.md](docs/DESIGN.md). Test with disposable files.

## Step 1 — Build on Ubuntu 22.04.5 LTS

Use a normal user account. Ubuntu is the deployment target; Debian packages supply
the build tools. There is no pip installation, downloaded C library, kernel
patch, or custom kernel header requirement.

```bash
sudo apt update
sudo apt install build-essential python3
cd /path/to/OS_Project
make
make check
```

Expected outputs: `build/libagent13.so`, `build/libagent13.a`, and passing C and
Python tests. The tests create and delete their own disposable temporary files.
Unix sockets must be allowed in the test environment. Run the tests as a non-root
user. Rebuild on Ubuntu: binaries built on a newer distribution may require a
newer glibc. Current validation results are in [docs/VALIDATION.md](docs/VALIDATION.md).

## Step 2 — Install and start the background service

Install for your own account; no administrator permission is needed for this
step. These commands install files and explicitly enable the service:

```bash
make install PREFIX="$HOME/.local"
install -d -m 700 "$HOME/agent13-vault"
systemctl --user daemon-reload
systemctl --user enable --now agent13.service
"$HOME/.local/bin/agent13" health
```

The unit is installed in `~/.local/lib/systemd/user/agent13.service`. It starts
Python as a foreground service process; systemd supervises it. There is no UI,
interactive daemon prompt, or double-fork. The CLI is a separate, short-lived
client. A user service starts with the user's service manager (normally at login).
For startup without a login, an administrator can enable lingering with
`sudo loginctl enable-linger "$USER"`; that is optional.

Inspect or stop it with:

```bash
systemctl --user status agent13.service
journalctl --user -u agent13.service -n 30 --no-pager
systemctl --user disable --now agent13.service
```

## Step 3 — Demonstrate with synthetic lab data

After starting the service, create this example file, which contains no real
personal information:

```bash
printf 'DISPOSABLE OS LAB TAX RETURN EXAMPLE\n' > "$HOME/agent13-vault/lab-example.txt"
chmod 600 "$HOME/agent13-vault/lab-example.txt"
"$HOME/.local/bin/agent13" plan lab-example.txt --passes 1 --zero-pass
```

Read the JSON plan and copy its `token`. It expires after 120 seconds. To delete
the exact file in that plan:

```bash
"$HOME/.local/bin/agent13" execute 'PASTE_TOKEN_HERE' --acknowledge-limitations
```

The successful result contains `status: overwritten_and_unlinked`, write counts,
`unlinked: true`, `directory_synced: true`, and
`physical_erasure_guaranteed: false`. To leave the file intact instead:

```bash
"$HOME/.local/bin/agent13" cancel 'PASTE_TOKEN_HERE'
```

CLI exit codes: **0** success, **1** service refusal/native failure, **2** invalid
CLI input or connection failure. If a connection fails after execution was sent,
check the file and journal before retrying; the operation may have completed.

An independent automated demo runs entirely in a temporary directory:

```bash
python3 examples/demo.py
```

## Step 4 — Integrate with all 35 agents

Give the class integration owner:

- `integration/tools.json` — function-calling definitions for health, plan,
  execute and cancel.
- `examples/class_shell_adapter.py` and `agent13/client.py` — deterministic
  dispatch over the Unix socket, with exact-token approval in the master shell.
- `include/agent13.h` and the compiled library — a C API alternative.
- [docs/INTEGRATION.md](docs/INTEGRATION.md) — protocol and integration contract.

The master shell's LLM routes a prompt such as “delete my lab tax return” to
agent 13 and selects a tool. It must identify the exact file, call `agent13_plan`,
display the plan, and obtain human authorization before `agent13_execute`.
The adapter takes the approved token from the master's approval flow, separately
from the model's arguments. Run the master as the service's Unix user. The
service cannot itself distinguish a human-approved request from another process
of that same user setting the acknowledgement field.

For a unified class package, keep the C module and install the Python package
inside the master project's namespace or use the socket API. A genuine new
kernel system call is unnecessary: this project uses existing Linux system
calls behind a userspace API.

## Step 5 — Build a Debian package (optional integration route)

On Ubuntu 22.04, as a normal user:

```bash
sudo apt install debhelper dpkg-dev
dpkg-buildpackage -us -uc -b
sudo apt install ../agent13_1.0.0_*.deb
install -d -m 700 "$HOME/agent13-vault"
systemctl --user daemon-reload
systemctl --user enable --now agent13.service
agent13 health
```

This installs `/usr/bin/agent13`, `/usr/lib/agent13/`, the C header/static library,
documentation and a systemd user unit. Replace the illustrative maintainer
identity in `debian/control` and `debian/changelog` with the class's real identity
before public distribution. The supplied GitHub workflow builds/tests/packages
on Ubuntu 22.04 once this project is placed in a GitHub repository.

## Changing the configured root or limits

Use `systemctl --user edit agent13.service` and replace the startup command.
For a user installation, for example:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 %h/.local/lib/agent13/run_service.py --root %h/agent13-vault --socket %t/agent13/control.sock --max-bytes 134217728 --plan-ttl 180
```

Then run `systemctl --user daemon-reload` and
`systemctl --user restart agent13.service`. The root must already exist, be owned
by your account and have mode 0700. Child directories must give your account rwx
permission and not be writable by group/others; files must be writable by you and not
writable by group/others. Keep service executables outside the allowed root.

Limits: 1–7 random passes (default 3), optional extra zero pass, file-size ceiling
1 GiB (default 64 MiB), token lifetime 1–3600 seconds, and 1–256 pending plans
(default 32). The daemon processes requests sequentially. A large overwrite
blocks other tool calls until it finishes. Shutdown finishes the current
operation when possible; crashes, power failures or SIGKILL can interrupt it.

## Lab explanation

Read [docs/VIVA.md](docs/VIVA.md) for the service architecture, C headers, OS calls,
inode behavior, synchronization, tradeoffs and demonstration sequence.

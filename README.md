# Agent 13 — Secure Shredding Agent

A complete, independent, headless **Ubuntu/Debian user service** for Phase A
of the 35-agent Agentic Shell class project. The CLI sends JSON over a Unix
domain socket to a Python service. Python calls a C shared library through
ctypes; C overwrites one random pass, calls fsync, and unlinks the file.
No master shell, LLM, web server, GUI, or other agent is required.

**Best effort only:** overwriting cannot guarantee complete physical erasure
on all storage technologies. Read [limitations](docs/SECURITY.md) before use.
Use disposable sample files for this project.

## Build and test on Ubuntu/Debian

Requirements: ordinary Linux user, Python 3.10+, GCC/C11, Make, Linux/glibc
headers. No pip packages are needed. Install packages yourself if missing:

```bash
sudo apt update
sudo apt install build-essential python3
make
make check
./bin/agent13-demo
```

`make` builds `build/libagent13_shred.so`. The explicit compiler command is:

```bash
gcc -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror -fPIC -shared \
  -Iinclude -o build/libagent13_shred.so src/c/agent13_shred.c
```

Create `build` first if using that command directly. `make check` compiles and
runs native fault-injection tests plus Python unittest tests. The automated
demo launches its own background service, checks health, inspects and previews
a temporary sample, confirms deletion, verifies removal, shows logs, and
stops/restarts the service. It does not install anything. See
[test evidence](docs/TESTING.md) and [professor demo](docs/DEMO.md).

## Install the systemd user service

These are **manual opt-in commands**. Development/testing does not install or
enable a host service. From the project directory, after `make check`:

```bash
install -d -m 700 "$HOME/.local/lib/agent13"
cp -a agent13 bin build docs include src manifests config systemd Makefile README.md \
  "$HOME/.local/lib/agent13/"
install -d -m 700 "$HOME/.local/share/agent13/allowed"
install -d "$HOME/.config/systemd/user" "$HOME/.local/bin"
install -m 644 systemd/agent13.service "$HOME/.config/systemd/user/agent13.service"
ln -s "$HOME/.local/lib/agent13/bin/agent13ctl" "$HOME/.local/bin/agent13ctl"
export PATH="$HOME/.local/bin:$PATH"
systemctl --user daemon-reload
systemctl --user start agent13
systemctl --user status agent13 --no-pager
agent13ctl health
```

Keep the checkout separate from the installed destination. If the CLI symlink
already exists, inspect it before replacing it. The service's existing allowed
directory must be owned by you with mode 0700. It refuses to run as root.
The unit creates a mode-0700 runtime directory and mode-0600 socket.

```bash
systemctl --user restart agent13
systemctl --user enable agent13
journalctl --user -u agent13.service -n 50 --no-pager
systemctl --user stop agent13
systemctl --user disable agent13
```

Enable starts the service at future user-manager startup/login. This project
does not enable lingering after logout. A normal logged-in systemd user
session is required; a container without a user manager can use the direct
service/demo instead. Installation and live systemd start/stop were not
performed during development; the supplied unit was validated separately.

## CLI workflow

Create only a disposable sample in the configured allowed directory:

```bash
umask 077
printf 'Disposable classroom sample\n' > "$HOME/.local/share/agent13/allowed/sample.txt"
agent13ctl info
agent13ctl inspect sample.txt
agent13ctl preview sample.txt
agent13ctl shred sample.txt
```

The last command is also a **dry run**. Read the preview and copy its
`data.confirm_token` into the following command within 60 seconds:

```bash
agent13ctl shred sample.txt --confirm-token 'PASTE_THE_PREVIEW_TOKEN_HERE'
test ! -e "$HOME/.local/share/agent13/allowed/sample.txt"
```

Each execution attempt consumes a known token. A changed file, different path,
expired/reused token, or service restart requires a fresh preview. CLI output
is JSON: exit 0 means success, 1 service failure, 2 usage/transport failure.
On a timeout, execution may have completed; inspect before retrying. The CLI
contains no shredding implementation.

## Run directly without installing systemd configuration

Create separate private directories for disposable files and the socket,
then launch the process with explicit configuration:

```bash
./bin/agent13d --allowed-root /absolute/private/allowed \
  --socket /absolute/private/runtime/agent13.sock
./bin/agent13ctl --socket /absolute/private/runtime/agent13.sock health
```

The service can run without terminal input. SIGTERM/SIGINT gracefully finish
the current operation and remove its socket; systemd supplies background
process management in normal use. [Configuration](config/README.md) documents
limits and socket selection.

## Design and project files

```text
User -> CLI -> Unix socket -> Python service -> Safety checks -> ctypes
  -> C shared library -> Linux system calls -> Allowed filesystem directory
```

Python keeps JSON, IPC, confirmation state, logging, and CLI code readable.
C demonstrates real Linux system calls and handles descriptor-based file
operations. The library never shells out to an external shred command.
`openat`/`O_NOFOLLOW`, `fstat`, `flock`, `renameat2`, `getrandom`, `write`,
`fsync`, and `unlinkat` are explained in [architecture](docs/ARCHITECTURE.md)
and [security](docs/SECURITY.md).

| File/directory | Purpose |
| --- | --- |
| `docs/ROADMAP.md` | Requirements and staged milestones, written first |
| `docs/ARCHITECTURE.md` | Six Mermaid diagrams and ASCII overview, written before code |
| `include/`, `src/c/` | Documented C ABI and implementation |
| `agent13/` | ctypes wrapper, protocol, service, client, CLI |
| `bin/` | Service, CLI, disposable demonstration entry points |
| `systemd/agent13.service` | User-service unit |
| `manifests/agent13.tools.json` | Proposed future tool definitions |
| `docs/API.md`, `docs/INTEGRATION.md` | Versioned public interfaces; future class integration |
| `docs/TESTING.md`, `docs/DEMO.md` | Test evidence, safe demo, viva preparation |

Phase B/C integration is future work. A future master should call the same
socket API; no central shell or dispatcher is implemented here.

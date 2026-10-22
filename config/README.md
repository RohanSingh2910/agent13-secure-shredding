# Configuration

The service requires explicit `--allowed-root` and `--socket` arguments. It
never creates or chooses a data directory automatically. Both the allowed
root and socket parent must exist, belong to the current user, and have mode
0700. Use a dedicated directory, never your home or a system directory.
The root path must be absolute, without symlinks, dot components or a trailing
slash. Nested directories must be owned by the user with no group/other write
bits. Files must be owned, owner-readable/writable, and not group/other writable.

| Setting | Default | Meaning |
| --- | --- | --- |
| `--allowed-root` | required | Dedicated directory in which deletion is permitted |
| `--socket` | required | Unix socket, maximum absolute path length 100 bytes |
| `--token-ttl` | 60 | Confirmation lifetime, 1..3600 seconds |
| `--max-bytes` | 67108864 | Maximum preview/execute size (64 MiB); nonnegative |
| CLI `--socket` / `AGENT13_SOCKET` | `$XDG_RUNTIME_DIR/agent13/agent13.sock` | Socket selection |
| CLI `--timeout` | 30 | Response wait in seconds; timeout never cancels execution |

The systemd unit uses `%h/.local/share/agent13/allowed` and
`%t/agent13/agent13.sock`. It creates only the private runtime socket directory
when started by the user. To override options, use `systemctl --user edit
agent13` and clear `ExecStart=` before replacing it. Changing configuration or
restarting invalidates every confirmation token. The C API always uses one
random overwrite pass; no pass-count option is provided.

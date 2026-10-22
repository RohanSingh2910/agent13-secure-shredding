# Provisional Unix socket API v1.0

One request and response per AF_UNIX stream connection, each a UTF-8 JSON
object followed by `\n`. Maximum request is 16,384 bytes including newline;
maximum client-accepted response is 65,536 bytes. Request read deadline is
2 seconds. Service handles one request at a time; 16 connections may queue.
Use a private mode-0700 socket directory; socket mode is 0600. Linux
SO_PEERCRED must identify the same effective UID. No network listener exists.

```json
{"version":"1.0","agent_id":13,"request_id":"demo-001","action":"health","parameters":{}}
```

```json
{"version":"1.0","agent_id":13,"request_id":"demo-001","success":true,"data":{"status":"ok","service":"agent13","abi_version":1},"error":null}
```

All five request fields are required, with no extras. `agent_id` must be the
integer 13 (not a Boolean); `version` is the string `1.0`. `request_id` is
1..128 ASCII letters/digits or `_.:-`. Unknown actions, extra/missing
parameters, duplicate JSON keys, non-finite numbers, invalid UTF-8, and
non-object requests are rejected. Framing errors have a null request ID.

| Action | Exact parameters | Successful data |
| --- | --- | --- |
| `health` | `{}` | status, service, ABI version |
| `info` | `{}` | release, allowed root, limits, dry-run default, limitations |
| `inspect_target` | `{"path":"sample"}` | path, native file metadata, dry_run=true, limitations |
| `preview_shred` | `{"path":"sample"}` | inspection plus confirm_token, expires_in_seconds |
| `execute_shred` | `{"path":"sample","confirm_token":"..."}` | bytes_written, unlinked, passes=1, dry_run=false, limitations |

Paths are relative to the configured root, or absolute inside it. The token
binds the canonical relative spelling and native fingerprint: device, inode,
size, mtime/ctime nanoseconds, mode, UID, and link count. Absolute and relative
spellings of the same valid path match. Dot/empty components and reserved
`.agent13-*` names are rejected. The C layer independently revalidates paths.

Preview never writes file contents or removes names. Execution is the only
destructive action. Tokens expire after 60 seconds by default (monotonic
clock); at most 256 live previews are stored. Every execution attempt consumes
the supplied known token, including mismatches, changed files, or I/O failure.
Expired entries may be purged by a new preview, in which case execution reports
INVALID_TOKEN. Tokens disappear on restart and are never logged. A token alone
does not prove that a human approved; future callers must enforce consent.

Errors always use `success=false`, `data=null`, and
`error={"code":"...","message":"...","details":{}}`. Examples of codes:

| Code | Meaning |
| --- | --- |
| INVALID_JSON / INVALID_REQUEST / INVALID_FRAME | Malformed contract or framing |
| REQUEST_TOO_LARGE / REQUEST_TIMEOUT / UNAUTHORIZED | Transport safety failure |
| INVALID_PATH / UNSAFE_TARGET | Path or file policy violation |
| NATIVE_IO / FILE_BUSY | OS error or advisory lock unavailable |
| FILE_CHANGED | Fingerprint mismatch; preview again after investigating |
| INVALID_TOKEN / EXPIRED_TOKEN / TOKEN_MISMATCH | Confirmation rejected |
| FILE_TOO_LARGE / TOKEN_LIMIT | Resource bound exceeded |

Native error details include numeric `errno`, `bytes_written`, Boolean
`unlinked`, and `recovery_path` relative to the allowed root. Failure may mean
some data was overwritten, or a file was unlinked but directory fsync failed.
Inspect these fields; do not assume failure means nothing happened. See
DEMO.md for recovery. Request IDs are correlation only, not retry protection.

CLI prints this JSON. Exit status: 0 success, 1 service rejection/failure,
2 arguments, connection, timeout, or invalid response. A CLI timeout does not
cancel service execution. On an unknown outcome inspect state before retrying.

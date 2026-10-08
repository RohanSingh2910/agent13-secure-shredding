# Agent 13 integration contract

## Tools and routing

Agent number: **13**. Domain: best-effort file shredding. Tools:
`agent13_health`, `agent13_plan`, `agent13_execute`, `agent13_cancel`.
Load `integration/tools.json` into the master shell's tool registry. These are
generic function-calling definitions; adapt their envelope to the chosen LLM
provider. The daemon accepts validated tool calls, not arbitrary natural language.

Import `agent13.client.request` from the source tree or installed package. For
the system package, add `/usr/lib/agent13` to the master's Python import path.
For a user install, add `~/.local/lib/agent13`. Or copy the dependency-free socket
client into the unified class package. Keep the C library beside the installed
`agent13` package, as the Makefile does.

```python
from agent13.client import request

preview = request("agent13_plan", {"path": "lab-example.txt", "passes": 1})
if preview["ok"]:
    plan = preview["result"]
    # Display this exact plan; obtain human approval in the master shell.
    # Execute only from that approval branch:
    # request("agent13_execute", {
    #     "token": plan["token"], "acknowledge_limitations": True
    # })
```

`examples/class_shell_adapter.py` checks that `approved_token` from the master
approval flow matches the model's requested token. Do not expose `approved_token`
as an LLM tool argument. On human refusal call cancel. On expiry or target-change
failure create and display a new plan. Options cannot change at execute time.

## Transport

- Unix stream socket: `${XDG_RUNTIME_DIR}/agent13/control.sock`, normally
  `/run/user/<uid>/agent13/control.sock`.
- UTF-8 JSON, terminated by one newline, one request/reply per connection.
- Maximum message including newline: 16,384 bytes. No request pipelining.
- The same service UID is required. No TCP listener, HTTP API, API key or root.
- Every request contains exactly `version`, `id`, `tool`, `arguments`.
  Version is integer 1; ID is a string of at most 128 characters.
- Duplicate JSON keys, nonfinite numbers and extra arguments are rejected.
- Recommended client timeout: 300 seconds; the service serializes operations.

Example request:

```json
{"version":1,"id":"lab-001","tool":"agent13_plan","arguments":{"path":"lab-example.txt","passes":1,"zero_pass":true}}
```

Illustrative response (token and inode vary):

```json
{
  "version": 1,
  "id": "lab-001",
  "ok": true,
  "result": {
    "token": "opaque-single-use-token",
    "path": "lab-example.txt",
    "file": {
      "device": 2049, "inode": 12345, "size": 36,
      "filesystem_type": "0xef53", "bytes_written": 0,
      "random_passes": 0, "zero_pass": false,
      "unlinked": false, "directory_synced": false
    },
    "passes": 1, "zero_pass": true,
    "expires_in_seconds": 120,
    "operation": "overwrite_then_unlink",
    "limitation": "Best-effort logical file overwrite only. Physical erasure is not guaranteed."
  }
}
```

Execution request, after approval:

```json
{"version":1,"id":"lab-002","tool":"agent13_execute","arguments":{"token":"opaque-single-use-token","acknowledge_limitations":true}}
```

Success returns `result.status = "overwritten_and_unlinked"`, `result.file`
with counters/flags, `physical_erasure_guaranteed = false`, and limitations.
The response ID echoes the caller's ID. Malformed requests without a valid ID
receive `id: null`.

Error format:

```json
{"version":1,"id":"lab-002","ok":false,"error":{"code":"invalid_token","message":"token is unknown, expired, consumed, or belongs to another user"}}
```

| Code | Meaning | Master's next action |
|---|---|---|
| `invalid_request` | Invalid shape, path encoding, option or timeout | Correct the arguments |
| `unknown_tool` | Unregistered tool name | Fix routing |
| `unauthorized` | Client UID differs from service UID | Run under the correct user |
| `busy` | Pending-plan capacity reached | Cancel unused plans or wait |
| `acknowledgement_required` | Execution was not explicitly acknowledged | Return to the exact-plan approval flow |
| `invalid_token` | Expired, used, unknown or wrong-user token | Inspect state; request a new plan if appropriate |
| `native_error` | C refused a target or an OS operation failed | Read message and execution `details` |

`native_error.details` is empty for preparation failures. For execution failures
it includes `bytes_written`, `random_passes`, `zero_pass`, `unlinked`, and
`directory_synced`. A positive byte count means destructive writes have happened
even when `ok` is false. If `unlinked` is true, the name was removed even if final
directory synchronization failed.

There is no retry/idempotency key for destructive calls. The request ID is only
for reply matching. The token is consumed when native execution is attempted.

## C integration

Use `include/agent13.h`, ABI version 1. Compile with:

```bash
cc -Iinclude examples/c_preview.c build/libagent13.a -o build/c_preview
./build/c_preview /absolute/path/to/private-vault lab-example.txt
```

The preview example does not overwrite. The main API sequence is:

```text
a13_root_open -> a13_prepare -> review/authorize -> a13_execute
                       |                               |
                  a13_job_free                    a13_job_free
                                -> close(root_fd)
```

Pass a maximum file size to `a13_prepare`; a success gives an opaque job and
`a13_info`. Call `a13_execute` with 1–7 passes and zero flag 0/1, then examine
the result even if it returns -1. Free every job, including failed attempts.
The C API takes no token and performs no human prompt: authorization and handle
lifetime are the caller's responsibility. Use the daemon API for the complete
token/expiry workflow. C symbols are separate from other agents via `a13_`.

For a monolithic class daemon, keep a single service user and either call the
shared library or run this worker process alongside the router. No artificial
kernel syscall or kernel-module installation is needed.

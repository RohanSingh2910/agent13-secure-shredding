# Future integration interfaces

Agent 13 works independently today. No master shell, central registry, LLM,
dispatcher, or other agent exists in this implementation. The class-wide
interface is **provisional**. Translate a future agreed contract at the
boundary in `agent13/protocol.py`; retain the service and native safety checks.

## Five public interfaces

| Interface | Entry point | Use |
| --- | --- | --- |
| CLI | `bin/agent13ctl` | Standalone testing and shell scripts |
| Unix socket JSON | v1.0, see API.md | Preferred future master integration |
| Python socket client | `agent13.client.call` | Same service safety workflow |
| Python native wrapper | `agent13.native.Backend` | Low-level embedding; caller supplies confirmation |
| Native C ABI v1 | `include/agent13_shred.h`, `build/libagent13_shred.so` | Linux embedding; caller supplies confirmation |

The proposed tool manifest is `manifests/agent13.tools.json`, exposing
`health`, `inspect_target`, `preview_shred`, and `execute_shred`. It is metadata
for future function calling, not an executable LLM router. The `info` API
action is also available. Avoid bypassing the service from a future master.

```python
from agent13.client import call, default_socket

health = call(default_socket(), "health")
preview = call(default_socket(), "preview_shred", {"path": "disposable.txt"})
# Present preview to the user and obtain explicit consent before proceeding.
# Only then call execute_shred with the same path and confirm_token.
```

For the low-level wrapper, construct `Backend("/absolute/private/allowed")`,
call `inspect("file")`, and pass that FileInfo to `shred("file", info)` only
after caller-managed confirmation. Use Backend as a context manager. These
functions are intentionally lower-level: token/peer checks live in the service.
The C caller supplies an already-open private allowed-directory descriptor and
caller-allocated structs. Check the return code and error details, including
partial writes, unlink state, and recovery path; never blindly retry.

Tokens are in-memory, expiring and single-use; an execution attempt consumes
one even when validation or I/O fails. Restart invalidates them. Request IDs
correlate replies but provide no idempotency. A lost response can mean deletion
completed: inspect state before deciding on another action. Independent
masters should serialize human approvals and never log confirmation tokens.

Only same-UID peers may use the socket. Malicious processes running as that
UID and root are outside the trust boundary. A future multi-user master must
define a separate privilege and consent model; do not simply relax permissions.

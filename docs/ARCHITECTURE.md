# Agent 13 architecture

This design precedes application code. Only standalone Phase A is implemented.

## 1. Current standalone architecture

```mermaid
flowchart TD
    U[User] --> CLI[agent13ctl CLI]
    CLI --> IPC[Unix domain socket / JSON]
    IPC --> S[Agent 13 background Python service]
    S --> V[Validation, peer UID, confirmation tokens]
    V --> W[ctypes wrapper]
    W --> C[C shared library]
    C --> K[Linux system calls]
    K --> F[Configured private allowed directory]
```

```text
User -> CLI -> Unix socket -> Service -> Validation -> ctypes
                                                       |
Filesystem <- Linux kernel <- C shared library <--------+
```

## 2. Internal component/block diagram

```mermaid
flowchart LR
    A[CLI argument parser] --> B[Socket client]
    B --> C[Bounded newline JSON handler]
    C --> D[Provisional v1 protocol validation]
    D --> E[Agent operations / token store]
    E --> F[Native wrapper]
    F --> G[Safe descriptor traversal]
    G --> H[Inspect / stage / overwrite / sync / unlink]
    E --> I[Metadata-only operation logs]
    J[systemd user manager] --> C
```

## 3. Secure shredding flowchart

```mermaid
flowchart TD
    A[Validate request and peer UID] --> B{Action?}
    B -->|inspect or preview| C[Open confined file without following links]
    C --> D[Validate owner, regular type, one link, permissions]
    D --> E{Preview?}
    E -->|no| F[Return metadata]
    E -->|yes| G[Store fingerprint and expiring random token]
    G --> H[Return dry-run and limitations]
    B -->|execute| I[Consume token and validate path / expiry]
    I --> J[Open file and compare fingerprint; acquire advisory lock]
    J --> K[Rename into private per-operation staging directory]
    K --> L{Staged name still references opened inode?}
    L -->|no| M[Restore without clobber or report recovery location]
    L -->|yes| N[Check fingerprint again; overwrite original size]
    N --> O[Handle short writes and EINTR; fsync file]
    O --> P[Unlink staged name and fsync directories]
    P --> Q[Return result; report any partial failure]
```

Any validation failure returns an error before intentional data overwrite.
The source name may disappear during execution before overwrite completes.
On failure the service attempts a no-clobber restore; otherwise it reports
the private recovery path. A successful overwrite does not prove physical
erasure. Advisory locking cannot prevent an uncooperative open-file writer.

## 4. CLI-to-service sequence

```mermaid
sequenceDiagram
    actor User
    participant CLI
    participant Service
    participant C
    User->>CLI: preview disposable.txt
    CLI->>Service: preview_shred(path), JSON v1.0
    Service->>C: inspect(path)
    C-->>Service: fingerprint
    Service-->>CLI: dry_run=true, token, expiry, limitations
    CLI-->>User: JSON result
    User->>CLI: shred disposable.txt --confirm-token TOKEN
    CLI->>Service: execute_shred(path, token)
    Service->>Service: consume and validate token
    Service->>C: shred(path, expected fingerprint)
    C-->>Service: success or detailed failure
    Service-->>CLI: versioned response
    CLI-->>User: result and exit status
```

## 5. Python-C interaction

```mermaid
flowchart TD
    A[Python Backend object holds allowed-root descriptor] --> B[ctypes signatures]
    B --> C[agent13_inspect_at / agent13_shred_at]
    C --> D[agent13_file_info and agent13_error structures]
    C --> E[openat + O_NOFOLLOW; fstat; flock]
    C --> F[renameat2 RENAME_NOREPLACE into private staging]
    C --> G[getrandom; full write loop; fsync; unlinkat]
    D --> H[Python NativeError / JSON-safe metadata]
```

The C header is the ABI source of truth. Python retains an open descriptor to
the allowed root, avoiding a later replacement of its pathname. C rechecks
ownership and mode. Relative path components are opened one at a time.

## 6. Future integration (NOT IMPLEMENTED)

```mermaid
flowchart TD
    M[Future Master Shell - NOT IMPLEMENTED] --> I[Shared Agent Interface - proposed]
    I --> S[Agent 13 Service]
    S --> C[C Backend]
    C --> K[Linux Kernel]
```

```text
Future Master Shell [NOT IMPLEMENTED]
       |
Shared Agent Interface [PROPOSED]
       |
Agent 13 Service
       |
C Backend
       |
Linux Kernel
```

No master process, registry, LLM, or dispatcher is needed to run this agent.

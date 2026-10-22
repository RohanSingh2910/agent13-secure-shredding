"""Long-running, single-worker Unix socket service. No master agent needed."""

import argparse
from dataclasses import dataclass
import errno
import fcntl
import logging
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import stat
import struct
import time

from . import __version__, protocol
from .native import Backend, NativeError, open_absolute_directory

LOG = logging.getLogger("agent13")


@dataclass
class Confirmation:
    path: str
    fingerprint: object
    expires: float


class Agent:
    def __init__(self, backend, token_ttl=60, max_bytes=64 * 1024 * 1024,
                 max_tokens=256, clock=time.monotonic):
        self.backend, self.token_ttl, self.max_bytes = backend, token_ttl, max_bytes
        self.max_tokens, self.clock = max_tokens, clock
        self.tokens = {}

    def dispatch(self, request):
        action, params = request["action"], request["parameters"]
        if action == "health":
            return {"status": "ok", "service": "agent13", "abi_version": 1}
        if action == "info":
            return {"name": "Secure Shredding Agent", "agent_id": 13,
                    "release": __version__, "phase": "A: standalone",
                    "allowed_root": self.backend.allowed_root,
                    "default_dry_run": True, "passes": 1,
                    "token_ttl_seconds": self.token_ttl, "max_bytes": self.max_bytes,
                    "limitations": protocol.LIMITATIONS,
                    "recovery_directories": ".agent13-stage-*; see docs/DEMO.md"}
        if action == "execute_shred":
            # Consume even on mismatch/failure. No destructive automatic retry.
            token = self.tokens.pop(params["confirm_token"], None)
            if token is None:
                raise protocol.RequestError("INVALID_TOKEN", "Token is unknown, already consumed, or lost on service restart")
            if self.clock() >= token.expires:
                raise protocol.RequestError("EXPIRED_TOKEN", "Preview again to obtain a fresh token")
            path = self.backend.relative_path(params["path"])
            if path != token.path:
                raise protocol.RequestError("TOKEN_MISMATCH", "Token belongs to a different path; preview again")
            result = self.backend.shred(path, token.fingerprint)
            return {**result, "dry_run": False, "limitations": protocol.LIMITATIONS}
        path = self.backend.relative_path(params["path"])
        info = self.backend.inspect(path)
        result = {"path": path, "file": info.as_dict(), "dry_run": True,
                  "limitations": protocol.LIMITATIONS}
        if action == "preview_shred":
            if info.size > self.max_bytes:
                raise protocol.RequestError("FILE_TOO_LARGE", "File exceeds the configured maximum size")
            now = self.clock()
            self.tokens = {key: value for key, value in self.tokens.items() if value.expires > now}
            if len(self.tokens) >= self.max_tokens:
                raise protocol.RequestError("TOKEN_LIMIT", "Too many pending previews; wait for expiry")
            value = secrets.token_urlsafe(32)
            self.tokens[value] = Confirmation(path, info, now + self.token_ttl)
            result.update(confirm_token=value, expires_in_seconds=self.token_ttl)
        return result

    def handle(self, raw):
        request_id, action = None, "invalid"
        try:
            request = protocol.decode(raw)
            if isinstance(request, dict) and protocol.valid_request_id(request.get("request_id")):
                request_id = request["request_id"]
            protocol.validate(request)
            action = request["action"]
            data = self.dispatch(request)
            LOG.info("action=%s request_id=%s success=true bytes_written=%s",
                     action, request_id, data.get("bytes_written", 0))
            return protocol.response(request_id, data=data)
        except (protocol.RequestError, NativeError) as exc:
            error = (exc.as_dict() if isinstance(exc, NativeError) else
                     {"code": exc.code, "message": str(exc), "details": {}})
            LOG.info("action=%s request_id=%s success=false code=%s", action, request_id, exc.code)
            return protocol.response(request_id, error=error)


def peer_allowed(connection):
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", credentials)
    return uid == os.geteuid()


def receive_line(connection, limit, timeout=2.0):
    deadline = time.monotonic() + timeout
    raw = bytearray()
    while b"\n" not in raw:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise protocol.RequestError("REQUEST_TIMEOUT", "Timed out waiting for one JSON line")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(min(4096, limit + 1 - len(raw)))
        except socket.timeout as exc:
            raise protocol.RequestError("REQUEST_TIMEOUT", "Timed out waiting for one JSON line") from exc
        if not chunk:
            raise protocol.RequestError("INVALID_FRAME", "Request must end with a newline")
        raw.extend(chunk)
        if len(raw) > limit:
            raise protocol.RequestError("REQUEST_TOO_LARGE", "JSON line exceeds the size limit")
    line, rest = bytes(raw).split(b"\n", 1)
    if rest:
        raise protocol.RequestError("INVALID_FRAME", "Use one JSON line per connection")
    return line


class Server:
    def __init__(self, agent, socket_path):
        self.agent, self.socket_path = agent, os.fspath(socket_path)
        self.stopping = False
        self.listen = None
        self.parent_fd = self.lock_fd = -1
        self.socket_identity = None

    def _stop(self, _signum, _frame):
        self.stopping = True

    def start(self):
        path = Path(self.socket_path)
        if not path.is_absolute() or len(os.fsencode(path)) > 100:
            raise ValueError("Socket needs an absolute path no longer than 100 bytes")
        if self.socket_path != str(path):
            raise ValueError("Socket path must have canonical components")
        self.parent_fd = open_absolute_directory(str(path.parent))
        parent = os.fstat(self.parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError("Socket directory must be owned by you with mode 0700")
        # Lock both the data root and socket directory to prevent multiple
        # instances, including instances configured with different sockets.
        self.lock_fd = os.open(".agent13-service.lock", os.O_RDWR | os.O_CREAT |
                               os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                               dir_fd=self.agent.backend.root_fd)
        lock = os.fstat(self.lock_fd)
        if not stat.S_ISREG(lock.st_mode) or lock.st_uid != os.geteuid() or lock.st_nlink != 1 or stat.S_IMODE(lock.st_mode) != 0o600:
            raise ValueError("Unsafe service lock file")
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Lock the directory inode itself; no extra socket-side lock filename.
        fcntl.flock(self.parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            old = os.stat(path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old:
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.geteuid():
                raise ValueError("Refusing to replace a non-socket or foreign socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(self.socket_path)
                except OSError as exc:
                    if exc.errno != errno.ECONNREFUSED:
                        raise
                else:
                    raise ValueError("Socket already has an active listener")
            os.unlink(path.name, dir_fd=self.parent_fd)
        self.listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listen.bind(self.socket_path)
        created = os.stat(path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        self.socket_identity = (created.st_dev, created.st_ino)
        os.chmod(path.name, 0o600, dir_fd=self.parent_fd, follow_symlinks=False)
        self.listen.listen(16)
        self.listen.setblocking(False)

    def close(self):
        if self.listen:
            self.listen.close()
        if self.socket_identity and self.parent_fd >= 0:
            try:
                current = os.stat(Path(self.socket_path).name, dir_fd=self.parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == self.socket_identity:
                    os.unlink(Path(self.socket_path).name, dir_fd=self.parent_fd)
            except FileNotFoundError:
                pass
        for fd in (self.lock_fd, self.parent_fd):
            if fd >= 0:
                os.close(fd)
        self.lock_fd = self.parent_fd = -1
        self.agent.tokens.clear()

    def serve(self):
        wake_read, wake_write = socket.socketpair()
        wake_read.setblocking(False)
        wake_write.setblocking(False)
        previous_fd = signal.set_wakeup_fd(wake_write.fileno())
        previous_handlers = {s: signal.signal(s, self._stop) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            self.start()
            LOG.info("service_ready agent_id=13")
            with selectors.DefaultSelector() as selector:
                selector.register(self.listen, selectors.EVENT_READ)
                selector.register(wake_read, selectors.EVENT_READ)
                while not self.stopping:
                    for key, _ in selector.select():
                        if key.fileobj is wake_read:
                            wake_read.recv(4096)
                            continue
                        if self.stopping:
                            break
                        try:
                            connection, _ = self.listen.accept()
                        except BlockingIOError:
                            continue
                        with connection:
                            try:
                                if not peer_allowed(connection):
                                    raise protocol.RequestError("UNAUTHORIZED", "Peer UID is not the service UID")
                                raw = receive_line(connection, protocol.MAX_REQUEST)
                                result = self.agent.handle(raw)
                            except protocol.RequestError as exc:
                                result = protocol.response(error={"code": exc.code, "message": str(exc), "details": {}})
                            except (ConnectionResetError, BrokenPipeError):
                                LOG.info("client_disconnected")
                                continue
                            try:
                                connection.settimeout(2)
                                connection.sendall(protocol.encode(result))
                            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                                LOG.info("client_disconnected")
        finally:
            self.close()
            signal.set_wakeup_fd(previous_fd)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            wake_read.close()
            wake_write.close()
            LOG.info("service_stopped")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowed-root", required=True, help="Existing private directory, mode 0700")
    parser.add_argument("--socket", required=True, help="Socket inside an existing private directory")
    parser.add_argument("--token-ttl", type=int, default=60)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args(argv)
    if not 1 <= args.token_ttl <= 3600 or args.max_bytes < 0:
        parser.error("token-ttl must be 1..3600 seconds; max-bytes must be nonnegative")
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with Backend(args.allowed_root) as backend:
            Server(Agent(backend, args.token_ttl, args.max_bytes), args.socket).serve()
    except (OSError, ValueError) as exc:
        LOG.error("startup_or_service_failure: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

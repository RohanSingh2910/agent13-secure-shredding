"""Foreground daemon supervised by systemd, with a native overwrite engine."""

import argparse
import fcntl
import json
import logging
import os
import secrets
import signal
import socket
import stat
import struct
import time
from pathlib import Path

from . import __version__
from .client import MAX_MESSAGE, default_socket
from .native import Engine, NativeError

LIMITATION = ("Best-effort logical file overwrite only. SSD wear leveling, copy-on-write, "
              "snapshots, journals and backups can retain data; physical erasure is not guaranteed.")


class RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class Agent:
    def __init__(self, root, max_bytes=64 * 1024 * 1024, ttl=120, max_plans=32, library=None):
        self.root = str(Path(root).resolve(strict=True))
        self.engine = Engine(self.root, max_bytes, library)
        self.ttl, self.max_plans = ttl, max_plans
        self.plans = {}

    def expire(self):
        now = time.monotonic()
        for token, plan in list(self.plans.items()):
            if plan["deadline"] <= now:
                self.engine.free(self.plans.pop(token)["handle"])

    def close(self):
        for plan in self.plans.values():
            self.engine.free(plan["handle"])
        self.plans.clear()
        self.engine.close()

    def dispatch(self, tool, args, uid):
        self.expire()
        if not isinstance(args, dict):
            raise RequestError("invalid_request", "arguments must be an object")
        allowed = {
            "agent13_health": set(),
            "agent13_plan": {"path", "passes", "zero_pass"},
            "agent13_execute": {"token", "acknowledge_limitations"},
            "agent13_cancel": {"token"},
        }
        if not isinstance(tool, str) or tool not in allowed:
            raise RequestError("unknown_tool", "unknown agent13 tool")
        if set(args) - allowed[tool]:
            raise RequestError("invalid_request", "unexpected argument")
        if tool == "agent13_health":
            return {"agent": 13, "version": __version__, "pid": os.getpid(),
                    "uid": os.geteuid(), "root": self.root, "pending_plans": len(self.plans),
                    "max_bytes": self.engine.max_bytes, "plan_ttl_seconds": self.ttl,
                    "limitation": LIMITATION}
        if tool == "agent13_plan":
            path = args.get("path")
            passes, zero = args.get("passes", 3), args.get("zero_pass", False)
            if (not isinstance(path, str) or not path or "\x00" in path or
                    len(path.encode("utf-8")) > 4095):
                raise RequestError("invalid_request", "path must be a nonempty UTF-8 string without NUL, at most 4095 bytes")
            if type(passes) is not int or not 1 <= passes <= 7 or type(zero) is not bool:
                raise RequestError("invalid_request", "passes must be an integer 1..7; zero_pass must be boolean")
            if len(self.plans) >= self.max_plans:
                raise RequestError("busy", "too many pending plans; cancel one or wait for expiry")
            handle, info = self.engine.prepare(path)
            token = secrets.token_urlsafe(32)
            self.plans[token] = {"handle": handle, "uid": uid, "deadline": time.monotonic() + self.ttl,
                                 "passes": passes, "zero": zero}
            return {"token": token, "path": path, "file": info, "passes": passes,
                    "zero_pass": zero, "expires_in_seconds": self.ttl,
                    "operation": "overwrite_then_unlink", "limitation": LIMITATION}
        token = args.get("token")
        if not isinstance(token, str) or len(token) > 128:
            raise RequestError("invalid_request", "token must be a bounded string")
        plan = self.plans.get(token)
        if not plan or plan["uid"] != uid:
            raise RequestError("invalid_token", "token is unknown, expired, consumed, or belongs to another user")
        if tool == "agent13_cancel":
            self.engine.free(self.plans.pop(token)["handle"])
            return {"cancelled": True}
        if args.get("acknowledge_limitations") is not True:
            raise RequestError("acknowledgement_required", "review the plan and set acknowledge_limitations to true to authorize execution")
        self.plans.pop(token)  # A failed attempt must never be retried with this token.
        try:
            result = self.engine.execute(plan["handle"], plan["passes"], plan["zero"])
            logging.info("execute success bytes_written=%d", result["bytes_written"])
            return {"status": "overwritten_and_unlinked", "file": result,
                    "physical_erasure_guaranteed": False, "limitation": LIMITATION}
        except NativeError as exc:
            logging.warning("execute failed bytes_written=%d unlinked=%s",
                            exc.details.get("bytes_written", 0), exc.details.get("unlinked", False))
            raise
        finally:
            self.engine.free(plan["handle"])


def handle_connection(connection, agent):
    request_id = None
    try:
        _pid, uid, _gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.geteuid():
            raise RequestError("unauthorized", "only clients with the service UID may connect")
        connection.settimeout(2)
        data = bytearray()
        while b"\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                raise RequestError("invalid_request", "expected a newline-terminated JSON request")
            data.extend(chunk)
            if len(data) > MAX_MESSAGE:
                raise RequestError("invalid_request", "request exceeds 16 KiB")
        line, _, trailing = data.partition(b"\n")
        if trailing.strip():
            raise RequestError("invalid_request", "one request per connection")
        message = json.loads(line.decode("utf-8"), object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))
        if not isinstance(message, dict):
            raise RequestError("invalid_request", "request must be an object")
        candidate = message.get("id")
        if not isinstance(candidate, str) or len(candidate) > 128:
            raise RequestError("invalid_request", "id must be a string at most 128 characters")
        request_id = candidate
        if type(message.get("version")) is not int or message["version"] != 1:
            raise RequestError("invalid_request", "version must be 1")
        if set(message) != {"version", "id", "tool", "arguments"}:
            raise RequestError("invalid_request", "expected exactly version, id, tool, arguments")
        result = agent.dispatch(message["tool"], message["arguments"], uid)
        response = {"version": 1, "id": request_id, "ok": True, "result": result}
    except NativeError as exc:
        response = {"version": 1, "id": request_id, "ok": False,
                    "error": {"code": "native_error", "message": str(exc), "details": exc.details}}
    except RequestError as exc:
        response = {"version": 1, "id": request_id, "ok": False,
                    "error": {"code": exc.code, "message": str(exc)}}
    except (ValueError, UnicodeError, RecursionError, socket.timeout) as exc:
        response = {"version": 1, "id": request_id, "ok": False,
                    "error": {"code": "invalid_request", "message": "malformed JSON, encoding, or request timeout"}}
        logging.debug("request rejected: %s", exc)
    except OSError:
        logging.warning("connection failed")
        return
    try:
        connection.sendall(json.dumps(response, ensure_ascii=True, allow_nan=False).encode() + b"\n")
    except OSError:
        logging.warning("reply not delivered; execution may have completed; do not blindly retry")


def serve(agent, socket_path):
    path = Path(socket_path).absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise RuntimeError("socket directory must be owned by the service UID and mode 0700")
    lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    server = None
    socket_identity = None
    previous_handlers = {}
    try:
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.geteuid() or lock_stat.st_nlink != 1:
            raise RuntimeError("unsafe service lock file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise RuntimeError("socket path exists and is not a socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise RuntimeError("socket already has an active listener")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        socket_identity = path.lstat().st_ino
        os.chmod(path, 0o600)
        server.listen(16)
        server.settimeout(0.5)
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True  # Finish an in-flight overwrite before closing fds.

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, stop)
        logging.info("agent13 ready pid=%d uid=%d", os.getpid(), os.geteuid())
        while not stopping:
            agent.expire()
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                handle_connection(connection, agent)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if server:
            server.close()
        if socket_identity is not None:
            try:
                if path.lstat().st_ino == socket_identity and stat.S_ISSOCK(path.lstat().st_mode):
                    path.unlink()
            except FileNotFoundError:
                pass
        os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent 13 background service (normally launched by systemd)")
    parser.add_argument("--root", required=True, help="existing private directory containing eligible files")
    parser.add_argument("--socket", default=default_socket())
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--plan-ttl", type=int, default=120)
    parser.add_argument("--max-plans", type=int, default=32)
    parser.add_argument("--library", help="explicit path to trusted libagent13.so")
    args = parser.parse_args(argv)
    if not 1 <= args.max_bytes <= 1024 ** 3 or not 1 <= args.plan_ttl <= 3600 or not 1 <= args.max_plans <= 256:
        parser.error("limits: max-bytes 1..1 GiB, plan-ttl 1..3600, max-plans 1..256")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    agent = None
    try:
        agent = Agent(args.root, args.max_bytes, args.plan_ttl, args.max_plans, args.library)
        serve(agent, args.socket)
    except (OSError, RuntimeError, NativeError) as exc:
        logging.error("service stopped: %s", exc)
        return 1
    finally:
        if agent:
            agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

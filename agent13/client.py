"""One JSON request per Unix stream connection; usable by the master shell."""

import json
import os
import socket
import uuid
from pathlib import Path

MAX_MESSAGE = 16384


def default_socket():
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/{}".format(os.getuid()))
    return str(Path(runtime) / "agent13/control.sock")


def request(tool, arguments=None, socket_path=None, timeout=300):
    message = {"version": 1, "id": uuid.uuid4().hex, "tool": tool,
               "arguments": {} if arguments is None else arguments}
    payload = json.dumps(message, ensure_ascii=True, allow_nan=False).encode() + b"\n"
    if len(payload) > MAX_MESSAGE:
        raise ValueError("request exceeds 16 KiB")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(socket_path or default_socket())
        connection.sendall(payload)
        response = bytearray()
        while b"\n" not in response:
            block = connection.recv(4096)
            if not block:
                raise ConnectionError("service disconnected before replying; check service logs")
            response.extend(block)
            if len(response) > MAX_MESSAGE:
                raise ValueError("response exceeds 16 KiB")
    result = json.loads(response.partition(b"\n")[0])
    if result.get("version") != 1 or result.get("id") != message["id"]:
        raise ValueError("response version or request id mismatch")
    return result

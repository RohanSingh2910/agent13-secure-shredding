"""Public socket client for the CLI and future integration callers."""

import os
import socket
import struct
import uuid

from . import protocol


def default_socket():
    return os.environ.get("AGENT13_SOCKET") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.geteuid()}"), "agent13", "agent13.sock")


def validate_response(result, request_id):
    if not isinstance(result, dict) or set(result) != {
        "version", "agent_id", "request_id", "success", "data", "error"
    } or result["version"] != protocol.VERSION or type(result["agent_id"]) is not int or result["agent_id"] != 13 or result["request_id"] != request_id or type(result["success"]) is not bool:
        raise ValueError("Malformed or mismatched service response")
    if result["success"]:
        if not isinstance(result["data"], dict) or result["error"] is not None:
            raise ValueError("Malformed success response")
    else:
        error = result["error"]
        if result["data"] is not None or not isinstance(error, dict) or set(error) != {"code", "message", "details"} or not isinstance(error["code"], str) or not isinstance(error["message"], str) or not isinstance(error["details"], dict):
            raise ValueError("Malformed error response")
    return result


def call(socket_path, action, parameters=None, request_id=None, timeout=30):
    request_id = request_id or uuid.uuid4().hex
    request = protocol.validate({"version": protocol.VERSION, "agent_id": 13,
                                 "request_id": request_id, "action": action,
                                 "parameters": parameters if parameters is not None else {}})
    raw = protocol.encode(request)
    if len(raw) > protocol.MAX_REQUEST:
        raise ValueError("Request exceeds the protocol size limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(os.fspath(socket_path))
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.geteuid():
            raise PermissionError("Socket service must run as the current user")
        connection.sendall(raw)
        chunks = bytearray()
        while b"\n" not in chunks:
            chunk = connection.recv(min(4096, protocol.MAX_RESPONSE + 1 - len(chunks)))
            if not chunk:
                raise ValueError("Service closed without a complete JSON response")
            chunks.extend(chunk)
            if len(chunks) > protocol.MAX_RESPONSE:
                raise ValueError("Service response exceeds size limit")
        line, rest = bytes(chunks).split(b"\n", 1)
        if rest:
            raise ValueError("Expected exactly one response")
    return validate_response(protocol.decode(line), request_id)

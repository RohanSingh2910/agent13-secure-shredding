"""Provisional class integration contract. Adapt version translation here."""

import json
import re

VERSION = "1.0"
AGENT_ID = 13
MAX_REQUEST = 16384
MAX_RESPONSE = 65536
ACTIONS = {"health": set(), "info": set(), "inspect_target": {"path"},
           "preview_shred": {"path"}, "execute_shred": {"path", "confirm_token"}}
LIMITATIONS = (
    "Best-effort overwrite only: complete erasure is not guaranteed on SSDs, "
    "copy-on-write or journaled filesystems, snapshots, backups, remote storage, "
    "or storage with remapped sectors. Existing open descriptors and "
    "uncooperative writers are outside the guarantee."
)


class RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are forbidden")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON numbers are forbidden")


def decode(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RequestError("INVALID_JSON", "Require a valid UTF-8 JSON object with unique keys") from exc


def valid_request_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None


def validate(request):
    if not isinstance(request, dict) or set(request) != {
        "version", "agent_id", "request_id", "action", "parameters"
    }:
        raise RequestError("INVALID_REQUEST", "Require exactly version, agent_id, request_id, action, parameters")
    if request["version"] != VERSION or type(request["agent_id"]) is not int or request["agent_id"] != AGENT_ID:
        raise RequestError("INVALID_REQUEST", "Unsupported version or agent_id")
    if not valid_request_id(request["request_id"]):
        raise RequestError("INVALID_REQUEST", "request_id needs 1-128 letters, digits, dots, colons, underscores or hyphens")
    action, params = request["action"], request["parameters"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise RequestError("INVALID_REQUEST", "Unsupported action")
    if not isinstance(params, dict) or set(params) != ACTIONS[action]:
        raise RequestError("INVALID_REQUEST", "Incorrect parameters for action")
    if "path" in params and (not isinstance(params["path"], str) or not params["path"]):
        raise RequestError("INVALID_REQUEST", "path must be a nonempty string")
    if "confirm_token" in params and (not isinstance(params["confirm_token"], str) or
                                      not 1 <= len(params["confirm_token"]) <= 256):
        raise RequestError("INVALID_REQUEST", "confirm_token must be a string of 1-256 characters")
    return request


def response(request_id=None, data=None, error=None):
    return {"version": VERSION, "agent_id": AGENT_ID, "request_id": request_id,
            "success": error is None, "data": data if error is None else None,
            "error": error}


def encode(message):
    return (json.dumps(message, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")

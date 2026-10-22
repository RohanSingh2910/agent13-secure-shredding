"""CLI testing and demonstration interface. File operations live in the service."""

import argparse
import json

from . import protocol
from .client import call, default_socket


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent13ctl", description=__doc__)
    parser.add_argument("--socket", default=default_socket())
    parser.add_argument("--timeout", type=float, default=30)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("health", "info", "inspect", "preview", "shred"):
        command = commands.add_parser(name)
        command.add_argument("--socket", default=argparse.SUPPRESS)
        if name in ("inspect", "preview", "shred"):
            command.add_argument("file", help="Path relative to the configured allowed root, or absolute inside it")
        if name == "shred":
            command.add_argument("--confirm-token", help="Single-use token returned by preview")
            command.add_argument("--dry-run", action="store_true", help="Preview only (also the default without a token)")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 3600:
        parser.error("timeout must be greater than zero and at most 3600 seconds")
    parameters = {"path": args.file} if hasattr(args, "file") else {}
    action = {"inspect": "inspect_target", "preview": "preview_shred"}.get(args.command, args.command)
    if args.command == "shred":
        if args.dry_run and args.confirm_token:
            parser.error("Use either --dry-run or --confirm-token")
        action = "preview_shred"
        if args.confirm_token:
            action = "execute_shred"
            parameters["confirm_token"] = args.confirm_token
    try:
        result = call(args.socket, action, parameters, timeout=args.timeout)
    except (OSError, ValueError, protocol.RequestError) as exc:
        result = protocol.response(error={
            "code": "CLIENT_ERROR", "message": str(exc),
            "details": {"note": "If execution was sent, its outcome may be unknown. Inspect before any retry."}})
        print(json.dumps(result, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

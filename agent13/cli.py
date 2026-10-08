import argparse
import json
import sys

from .client import request


def main(argv=None):
    parser = argparse.ArgumentParser(description="CLI for Agent 13 Secure Shredding Service")
    parser.add_argument("--socket", help="override Unix socket path")
    parser.add_argument("--timeout", type=float, default=300, help="response timeout in seconds (default 300)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="check the running service")
    plan = sub.add_parser("plan", help="inspect and lock an eligible file; changes no contents")
    plan.add_argument("path", help="path relative to the service's allowed root")
    plan.add_argument("--passes", type=int, choices=range(1, 8), default=3)
    plan.add_argument("--zero-pass", action="store_true", help="add one final zero pass")
    execute = sub.add_parser("execute", help="overwrite and unlink the exact prepared file")
    execute.add_argument("token")
    execute.add_argument("--acknowledge-limitations", action="store_true", required=True,
                         help="authorize deletion after reviewing the plan and physical-erasure limits")
    cancel = sub.add_parser("cancel", help="release a pending plan and file lock")
    cancel.add_argument("token")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    arguments = {}
    if args.command == "plan":
        arguments = {"path": args.path, "passes": args.passes, "zero_pass": args.zero_pass}
    elif args.command == "execute":
        arguments = {"token": args.token, "acknowledge_limitations": args.acknowledge_limitations}
    elif args.command == "cancel":
        arguments = {"token": args.token}
    try:
        response = request("agent13_" + args.command, arguments, args.socket, args.timeout)
    except (OSError, ValueError, ConnectionError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "connection_error", "message": str(exc),
                        "note": "If execution was sent, inspect the file and journal before retrying."}}), file=sys.stderr)
        return 2
    print(json.dumps(response, indent=2))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

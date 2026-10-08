"""Import from the master shell; the shell supplies its own LLM and approval UI.

Run the master shell as the same UID as the per-user agent13 service.
"""
import json
from pathlib import Path

from agent13.client import request

TOOLS = json.loads((Path(__file__).resolve().parent.parent / "integration/tools.json").read_text())
NAMES = {tool["function"]["name"] for tool in TOOLS}


def dispatch_tool(name, arguments, *, socket_path=None, approved_token=None):
    """Approval token MUST come from the human approval flow, never LLM arguments.

    Keep the exact displayed plan on the master side. Once approved, pass its
    token as approved_token; do not use a blanket boolean across all plans.
    """
    if name not in NAMES:
        raise ValueError("not an agent13 tool")
    if name == "agent13_execute":
        if not approved_token or approved_token != arguments.get("token"):
            return {"ok": False, "error": {"code": "human_approval_required",
                    "message": "Approval of this exact plan is required in the master shell."}}
    return request(name, arguments, socket_path)

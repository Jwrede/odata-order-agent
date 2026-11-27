"""MCP server (stdio, JSON-RPC 2.0, no SDK) that exposes the ERP tools to an agent.

Configuration comes from the environment, set by the operator and not by the model:
OOA_ODATA_URL (CAP service), OOA_MESSAGE_ID (mail being processed, stored on the order),
OOA_TOOL_LOG (append every call with its result as JSON lines), OOA_PASSWORD_AGENT.
"""

from __future__ import annotations

import json
import os
import sys

from .odata import Client
from .tools import TOOLS, Tools

PROTOCOL_VERSION = "2025-06-18"


def handle(tools: Tools, msg: dict) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "odata-order-agent", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        res, err = tools.call(params.get("name", ""), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}], "isError": err}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    log_path = os.environ.get("OOA_TOOL_LOG")
    log = open(log_path, "a", encoding="utf-8") if log_path else None
    tools = Tools(Client("agent"), os.environ.get("OOA_MESSAGE_ID", "unknown"), log)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(tools, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

"""The MCP server over stdio, as Claude Code or another MCP client would start it."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def session(erp_url, tmp_path, requests):
    env = dict(os.environ, OOA_ODATA_URL=erp_url, OOA_MESSAGE_ID="mail-mcp-1",
               OOA_TOOL_LOG=str(tmp_path / "log.jsonl"), PYTHONPATH=str(ROOT / "src"))
    stdin = "".join(json.dumps(r) + "\n" for r in requests)
    p = subprocess.run([sys.executable, "-m", "order_agent.mcp_server"], input=stdin, capture_output=True,
                       text=True, env=env, timeout=60)
    return [json.loads(line) for line in p.stdout.splitlines()]


def test_protocol_and_tools(erp_url, tmp_path):
    out = session(erp_url, tmp_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lookup_sender", "arguments": {"email": "Lager@Petersen-Bau.example"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "create_sales_order", "arguments": {
            "customer_id": "C1005", "customer_reference": "MCP-1", "items": [{"product_id": "WW-GELB-L", "quantity": 2}]}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "create_sales_order", "arguments": {
            "customer_id": "C1005", "customer_reference": "MCP-1", "items": [{"product_id": "WW-GELB-L", "quantity": 2}]}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "release_order", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 7, "method": "nope"},
    ])
    assert len(out) == 7  # the notification gets no answer
    assert out[0]["result"]["serverInfo"]["name"] == "odata-order-agent"
    names = [t["name"] for t in out[1]["result"]["tools"]]
    assert "create_sales_order" in names and not any("release" in n for n in names)
    assert json.loads(out[2]["result"]["content"][0]["text"])["customer"]["ID"] == "C1005"
    created = json.loads(out[3]["result"]["content"][0]["text"])
    assert created["status"] == "Proposed" and created["netAmount"] == "59.80"
    assert out[4]["result"]["isError"] and "duplicate" in out[4]["result"]["content"][0]["text"]
    assert out[5]["result"]["isError"]
    assert out[6]["error"]["code"] == -32601
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert [x["tool"] for x in log] == ["lookup_sender", "create_sales_order", "create_sales_order", "release_order"]
    assert log[1]["result"]["ID"] == "<uuid>"


def test_message_id_is_set_by_the_operator(erp_url, tmp_path):
    from order_agent.odata import Client
    session(erp_url, tmp_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "create_sales_order", "arguments": {
            "customer_id": "C1005", "customer_reference": "MCP-2", "sourceMessage": "forged",
            "items": [{"product_id": "WW-GELB-L", "quantity": 1}]}}}])
    rows = Client("approver", base_url=erp_url).query("SalesOrders", filter="customerReference eq 'MCP-2'")
    assert rows == []  # unknown argument: the call is refused, nothing is created
    assert json.loads((tmp_path / "log.jsonl").read_text())["error"] is True

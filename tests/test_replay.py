"""Replay runs the recorded tool calls again against the live CAP service and must notice when
the ERP answers differently than during the recording."""

import json
import shutil
from pathlib import Path

import pytest

from order_agent.agent import Agent, LLMError, ReplayMismatch
from order_agent.gate import Gate

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "recordings"


def test_replay_reproduces_order_and_gate(erp_url, mails):
    run = Agent("sonnet", REC, odata_url=erp_url).process(mails["kh-01"])
    assert run.output["action"] == "order_created"
    assert [c["tool"] for c in run.tool_calls].count("create_sales_order") == 1
    d = Gate(erp_url).decide(mails["kh-01"], run.output)
    assert d.route == "auto_release" and d.released


def test_replay_detects_a_changed_erp_answer(erp_url, mails, tmp_path):
    shutil.copytree(REC / "sonnet", tmp_path / "sonnet")
    p = tmp_path / "sonnet" / "nw-02.json"
    rec = json.loads(p.read_text())
    call = next(c for c in rec["tool_calls"] if c["tool"] in ("get_products", "search_products"))
    call["result"] = [dict(x, stock=999) for x in call["result"]]
    p.write_text(json.dumps(rec))
    with pytest.raises(ReplayMismatch):
        Agent("sonnet", tmp_path, odata_url=erp_url).process(mails["nw-02"])


def test_replay_refuses_a_recording_for_another_prompt(erp_url, mails, tmp_path):
    shutil.copytree(REC / "sonnet", tmp_path / "sonnet")
    changed = dict(mails["nw-02"], body=mails["nw-02"]["body"] + " ")
    with pytest.raises(LLMError):
        Agent("sonnet", tmp_path, odata_url=erp_url).process(changed)

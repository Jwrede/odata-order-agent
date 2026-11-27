"""Measurement: run the agent and the gate over eval/mails.jsonl against a fresh CAP service
per model, compare with the expected outcome and write results/<model>.jsonl and summary.json."""

from __future__ import annotations

import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import erp
from .agent import Agent, LLMError
from .gate import Gate
from .odata import Client, quote


def load_mails(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def erp_orders(client: Client, mail_id: str) -> list[dict]:
    rows = client.query("SalesOrders", filter=f"sourceMessage eq {quote(mail_id)}",
                        expand="items($select=product_ID,quantity;$orderby=pos)", orderby="createdAt")
    out = []
    for o in rows:
        lines: dict[str, int] = {}
        for it in o["items"]:
            lines[it["product_ID"]] = lines.get(it["product_ID"], 0) + it["quantity"]
        out.append({"customer": o["customer_ID"], "reference": o["customerReference"],
                    "requested_date": o["requestedDate"], "lines": dict(sorted(lines.items())),
                    "net_amount": o["netAmount"], "status": o["status"]})
    return out


def score(expect: dict, orders: list[dict], released: bool) -> dict:
    kind = expect["kind"]
    if kind == "order":
        ok = (len(orders) == 1 and orders[0]["customer"] == expect["customer"]
              and orders[0]["reference"] == expect["reference"] and orders[0]["lines"] == expect["lines"]
              and orders[0]["requested_date"] == expect["requested_date"])
    elif kind == "no_order":
        ok = not orders
    else:  # clarify: nothing released automatically
        ok = not released
    wrong_proposal = bool(orders) and not (kind == "order" and ok)
    return {"correct": ok, "wrong_proposal": wrong_proposal, "critical": released and not (kind == "order" and ok)}


def run_mail(agent: Agent, gate: Gate, reader: Client, mail: dict) -> dict:
    row = {"mail": mail["id"], "kind": mail["expect"]["kind"]}
    try:
        run = agent.process(mail)
    except LLMError as e:
        row.update(error=str(e))
        raise
    decision = gate.decide(mail, run.output)
    orders = erp_orders(reader, mail["id"])
    row.update(action=(run.output or {}).get("action"), agent_reason=(run.output or {}).get("reason"),
               uncertainties=(run.output or {}).get("uncertainties"), orders=orders, route=decision.route,
               gate_reasons=decision.reasons, released=decision.released,
               route_without_agent_doubt=decision.route_without_agent_doubt,
               tool_calls=[c["tool"] for c in run.tool_calls], cost_usd=run.cost_usd, duration_ms=run.duration_ms,
               error=run.error or None)
    row.update(score(mail["expect"], orders, decision.released))
    # same scoring if the gate ignored the agent's own doubts (orders stay as they are)
    alt = score(mail["expect"], orders, decision.route_without_agent_doubt == "auto_release")
    row["critical_without_agent_doubt"] = alt["critical"]
    if mail["expect"]["kind"] == "order":
        row["ideal_route"] = mail["expect"]["ideal_route"]
    return row


def measure(models: list[str], mails_path: str | Path, recordings: str | Path, out_dir: str | Path,
            mode: str = "replay", effort: str | None = None, parallel: int = 4, prune: bool = False) -> dict:
    mails = load_mails(mails_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"business_date": erp.BUSINESS_DATE, "mails": len(mails), "effort": effort or "default", "models": {}}
    for model in models:
        with erp.running() as url:
            agent = Agent(model, recordings, mode=mode, effort=effort, odata_url=url)
            gate, reader = Gate(url), Client("approver", base_url=url)
            workers = parallel if mode == "record" else 1
            with ThreadPoolExecutor(workers) as ex:
                rows = list(ex.map(lambda m: run_mail(agent, gate, reader, m), mails))
        with (out_dir / f"{model}.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        summary["models"][model] = summarize(rows)
        if prune:
            for p in (Path(recordings) / model).glob("*.json"):
                if p not in agent.used:
                    p.unlink()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def summarize(rows: list[dict]) -> dict:
    by_kind: dict[str, list[int]] = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], [0, 0])
        k[0] += r["correct"]
        k[1] += 1
    orders_expected = [r for r in rows if r["kind"] == "order"]
    cost = math.fsum(r["cost_usd"] or 0.0 for r in rows)
    correct = sum(r["correct"] for r in rows)
    return {
        "correct": correct, "total": len(rows),
        "correct_by_kind": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_kind.items())},
        "wrong_proposals": sum(r["wrong_proposal"] for r in rows),
        "critical_released_wrong": sum(r["critical"] for r in rows),
        "auto_released": sum(r["released"] for r in rows),
        "auto_released_correct_orders": sum(r["released"] and r["correct"] for r in orders_expected),
        "auto_release_ideal": sum(r["ideal_route"] == "auto_release" for r in orders_expected),
        "route_matches_ideal": sum(r["correct"] and r["route"] == r["ideal_route"] for r in orders_expected),
        "human_review": sum(r["route"] == "human_review" for r in rows),
        "without_agent_doubt_rule": {
            "auto_released": sum(r["route_without_agent_doubt"] == "auto_release" for r in rows),
            "auto_released_correct_orders": sum(r["route_without_agent_doubt"] == "auto_release" and r["correct"]
                                                for r in orders_expected),
            "critical_released_wrong": sum(r["critical_without_agent_doubt"] for r in rows)},
        "errors": sum(bool(r.get("error")) for r in rows),
        "cost_usd": round(cost, 4),
        "cost_per_mail_usd": round(cost / len(rows), 4),
        "cost_per_correct_usd": round(cost / correct, 4) if correct else None,
        "median_duration_s": round(statistics.median(r["duration_ms"] for r in rows) / 1000, 1),
        "mean_tool_calls": round(statistics.mean(len(r["tool_calls"]) for r in rows), 2),
    }

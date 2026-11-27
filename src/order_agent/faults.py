"""Fault injection for the release gate: for every expected order, create typical agent mistakes
directly in the ERP (as user agent, exactly as the agent would) and count how many the ERP refuses,
how many the gate holds for a person, and how many it would release. No LLM involved."""

from __future__ import annotations

import json
from pathlib import Path

from . import erp
from .evaluate import load_mails
from .gate import Gate
from .odata import Client, ODataError

FAULTS = ["sibling_product", "quantity_x10", "quantity_plus_1", "other_reference", "extra_line", "missing_line", "other_customer"]


def sibling(pid: str, catalogue: list[str]) -> str:
    parts = pid.split("-")
    for n in (len(parts) - 1, 1):
        prefix = "-".join(parts[:n]) + "-"
        cands = [p for p in catalogue if p.startswith(prefix) and p != pid]
        if cands:
            return cands[0]
    return [p for p in catalogue if p != pid][0]


def faulty(expect: dict, fault: str, catalogue: list[str]) -> dict:
    lines = [{"product_ID": p, "quantity": q} for p, q in expect["lines"].items()]
    body = {"customer_ID": expect["customer"], "customerReference": expect["reference"],
            "requestedDate": expect["requested_date"], "items": lines}
    first = lines[0]
    if fault == "sibling_product":
        first["product_ID"] = sibling(first["product_ID"], catalogue)
    elif fault == "quantity_x10":
        first["quantity"] *= 10
    elif fault == "quantity_plus_1":
        first["quantity"] += 1
    elif fault == "other_reference":
        body["customerReference"] += "9"
    elif fault == "extra_line":
        extra = "HS-NIT-L" if "HS-NIT-L" not in expect["lines"] else "KB-300-SW"
        lines.append({"product_ID": extra, "quantity": 10})
    elif fault == "missing_line":
        if len(lines) > 1:
            lines.pop()
        else:  # single-line order: the agent ordered nothing useful, replace by another product
            first["product_ID"] = sibling(first["product_ID"], catalogue)
    elif fault == "other_customer":
        body["customer_ID"] = "C1004" if expect["customer"] != "C1004" else "C1005"
    return body


def run(mails_path: str | Path, out: str | Path) -> dict:
    mails = [m for m in load_mails(mails_path) if m["expect"]["kind"] == "order"]
    rows = []
    for fault in FAULTS:  # fresh ERP per fault type, so references of one mail do not collide
        with erp.running() as url:
            agent, gate = Client("agent", base_url=url), Gate(url)
            catalogue = sorted(p["ID"] for p in agent.query("Products", select="ID"))
            for m in mails:
                mail = dict(m, id=f"{m['id']}~{fault}")
                body = dict(faulty(m["expect"], fault, catalogue), sourceMessage=mail["id"])
                row = {"mail": m["id"], "fault": fault}
                try:
                    agent.post("/SalesOrders", body)
                except ODataError as e:
                    row.update(outcome="refused_by_erp", reason=e.message)
                    rows.append(row)
                    continue
                d = gate.decide(mail, {"uncertainties": []})
                row.update(outcome="released" if d.released else "held_by_gate", reason="; ".join(d.reasons))
                rows.append(row)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    summary: dict = {}
    for r in rows:
        s = summary.setdefault(r["fault"], {"refused_by_erp": 0, "held_by_gate": 0, "released": 0})
        s[r["outcome"]] += 1
    summary["total"] = {k: sum(v[k] for f, v in summary.items() if f != "total")
                        for k in ("refused_by_erp", "held_by_gate", "released")}
    return summary

"""order-agent: process customer mails into ERP sales orders, review the queue, measure."""

from __future__ import annotations

import argparse
import json
import sys

from .agent import Agent
from .evaluate import erp_orders, measure
from .gate import Gate
from .odata import Client, ODataError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="order-agent", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("process", help="process one mail (JSON with id, from, date, subject, body)")
    p.add_argument("mail", help="path to a JSON file, or - for stdin")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--effort")
    p.add_argument("--mode", choices=["replay", "record"], default="record")
    p.add_argument("--recordings", default="recordings")
    p.add_argument("--no-release", action="store_true", help="only report the gate decision")

    sub.add_parser("queue", help="orders waiting for a person (status Proposed)")
    a = sub.add_parser("approve", help="release an order as user approver")
    a.add_argument("order_id")
    a.add_argument("--note", default="")
    d = sub.add_parser("decline", help="decline an order as user approver")
    d.add_argument("order_id")
    d.add_argument("--reason", required=True)

    m = sub.add_parser("measure", help="run the evaluation (starts a fresh CAP server per model)")
    m.add_argument("--model", action="append", required=True)
    m.add_argument("--mode", choices=["replay", "record"], default="replay")
    m.add_argument("--effort")
    m.add_argument("--mails", default="eval/mails.jsonl")
    m.add_argument("--recordings", default="recordings")
    m.add_argument("--out", default="results")
    m.add_argument("--parallel", type=int, default=4)
    m.add_argument("--prune", action="store_true", help="delete recordings no mail used")

    f = sub.add_parser("faults", help="fault injection: typical agent mistakes against ERP rules and gate (no LLM)")
    f.add_argument("--mails", default="eval/mails.jsonl")
    f.add_argument("--out", default="results/faults.jsonl")

    sub.add_parser("mcp", help="run the MCP server on stdio (env: OOA_ODATA_URL, OOA_MESSAGE_ID, OOA_TOOL_LOG)")
    args = ap.parse_args(argv)

    if args.cmd == "process":
        mail = json.load(sys.stdin if args.mail == "-" else open(args.mail))
        run = Agent(args.model, args.recordings, mode=args.mode, effort=args.effort).process(mail)
        dec = Gate().decide(mail, run.output, release=not args.no_release)
        print(json.dumps({"agent": run.output, "error": run.error or None,
                          "tool_calls": [f"{c['tool']}({json.dumps(c['args'], ensure_ascii=False)})" for c in run.tool_calls],
                          "orders": erp_orders(Client("approver"), mail["id"]),
                          "gate": {"route": dec.route, "released": dec.released, "reasons": dec.reasons},
                          "cost_usd": run.cost_usd}, indent=1, ensure_ascii=False))
        return 0
    if args.cmd == "queue":
        c = Client("approver")
        for o in c.query("SalesOrders", filter="status eq 'Proposed'", orderby="createdAt",
                         expand="items($select=product_ID,quantity)"):
            lines = ", ".join(f"{i['quantity']} x {i['product_ID']}" for i in o["items"])
            print(f"{o['ID']}  {o['customer_ID']}  {o['customerReference']:<14} {o['netAmount']:>9} EUR  "
                  f"{o['sourceMessage']}  {lines}")
        return 0
    if args.cmd in ("approve", "decline"):
        c = Client("approver")
        action, body = ("release", {"note": args.note}) if args.cmd == "approve" else ("decline", {"reason": args.reason})
        try:
            o = c.post(f"/SalesOrders({args.order_id})/{action}", body)
        except ODataError as e:
            print(f"refused by the ERP: {e}", file=sys.stderr)
            return 1
        print(f"{o['ID']} {o['status']} by {o['releasedBy']}")
        return 0
    if args.cmd == "measure":
        s = measure(args.model, args.mails, args.recordings, args.out, mode=args.mode, effort=args.effort,
                    parallel=args.parallel, prune=args.prune)
        print(json.dumps(s, indent=1))
        return 0
    if args.cmd == "faults":
        from .faults import run
        print(json.dumps(run(args.mails, args.out), indent=1))
        return 0
    if args.cmd == "mcp":
        from .mcp_server import main as serve
        serve()
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

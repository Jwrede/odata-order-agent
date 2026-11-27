"""Release gate between the agent and the ERP.

The gate does not trust the agent's report. It reads what the agent actually created in the ERP
(orders whose sourceMessage is this mail), checks each order against the mail text and the master
data, and either releases it as the technical user `gate` (role AutoRelease) or leaves it in
status Proposed for a person. The ERP enforces the auto-release limit and the new-customer rule a
second time, so a gate bug cannot release more than the AutoRelease role allows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .odata import Client, ODataError, quote

AUTO_RELEASE_LIMIT = 2500.0
PRICE_TERMS = re.compile(r"(?i)(\u20ac|\beur\b|\bpreis|\bprice|rabatt|discount|skonto|\d\s?%|free of charge|kostenlos|gratis)")


@dataclass
class Decision:
    mail_id: str
    route: str                      # auto_release | human_review | no_order
    order_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    released: bool = False
    route_without_agent_doubt: str = ""  # what the gate would decide if it ignored the agent's uncertainties


def _cited(text: str, token: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(token.lower()) + r"(?![\w-])", text) is not None


def _number_cited(text: str, n: int) -> bool:
    """The quantity is written as a standalone number (not part of a date, price or article number)."""
    return re.search(r"(?<![\w.,/-])" + str(n) + r"(?![\w,/-]|[.,]\d)", text) is not None


QTY_LINE = re.compile(r"(?i)(?<![\w.,/-])\d+\s*(x\b|pack|karton|st(ue|\u00fc)ck|rolle|satz|pc|piece|carton|box|paar|pair)")
QUOTE_START = re.compile(r"(?im)^(-{2,}\s*(urspr|original)|>|(von|from|gesendet|sent):\s)")


def own_text(mail: dict) -> list[str]:
    """[subject] + body lines without quoted earlier mails, lower case."""
    body = mail["body"]
    m = QUOTE_START.search(body)
    if m:
        body = body[:m.start()]
    return [mail.get("subject", "").lower()] + [ln.lower() for ln in body.splitlines() if ln.strip()]


def mentions(lines: list[str], tokens: dict[str, list[str]]) -> list[dict]:
    """Every place a product is named, with the text segment that belongs to it: from the end of
    the previous product mention on the same line to the start of the next one."""
    out = []
    for li, ln in enumerate(lines):
        hits = []
        for pid, ts in tokens.items():
            for t in ts:
                for m in re.finditer(r"(?<![\w-])" + re.escape(t.lower()) + r"(?![\w-])", ln):
                    hits.append((m.start(), m.end(), pid))
        hits.sort()
        for k, (st, en, pid) in enumerate(hits):
            lo = hits[k - 1][1] if k else 0
            hi = hits[k + 1][0] if k + 1 < len(hits) else len(ln)
            out.append({"line": li, "pid": pid, "segment": ln[lo:hi]})
    return out


def check(mail: dict, agent_output: dict | None, orders: list[dict], sender_customer: str | None,
          materials: dict[tuple[str, str], str], new_customer: dict[str, bool],
          catalogue: list[str] = ()) -> tuple[str, list[str]]:
    """Pure decision logic (tested without an ERP). Returns (route, reasons).

    Evidence rule: every order line must be backed by its own line of the mail (quoted earlier mails
    excluded) that names the product (our ID or the customer's material number) and the quantity as a
    standalone number; every mail line that names a product must be backed by an order line.
    """
    if not orders:
        return "no_order", []
    reasons: list[str] = []
    if len(orders) > 1:
        reasons.append(f"{len(orders)} orders for one mail")
    if agent_output is None:
        reasons.append("agent run failed")
    elif agent_output.get("uncertainties"):
        reasons.append("agent uncertain: " + "; ".join(agent_output["uncertainties"])[:300])
    lines = own_text(mail)
    text = "\n".join(lines)
    if PRICE_TERMS.search(text):
        reasons.append("mail mentions prices or discounts")
    for o in orders:
        if sender_customer != o["customer_ID"]:
            reasons.append(f"order customer {o['customer_ID']} is not the sender's customer {sender_customer}")
        if new_customer.get(o["customer_ID"]):
            reasons.append("first order of a new customer")
        if float(o["netAmount"]) > AUTO_RELEASE_LIMIT:
            reasons.append(f"order value {o['netAmount']} EUR above {AUTO_RELEASE_LIMIT:.0f} EUR")
        if not _cited(text, o["customerReference"]):
            reasons.append(f"customer reference {o['customerReference']} not in the mail")
        tokens = {p: [p] + [cm for (c, cm), q in materials.items() if c == o["customer_ID"] and q == p]
                  for p in set(catalogue) | {it["product_ID"] for it in o["items"]}}
        found = mentions(lines, tokens)
        used: set[int] = set()
        for it in o["items"]:
            pid = it["product_ID"]
            named = [k for k, m in enumerate(found) if m["pid"] == pid]
            if not named:
                reasons.append(f"{pid} matched from a description, not cited by number")
                continue
            backed = [k for k in named if k not in used and _number_cited(found[k]["segment"], it["quantity"])]
            if not backed:
                reasons.append(f"quantity {it['quantity']} of {pid} not written next to it in the mail")
                continue
            used.add(backed[0])
        for k, m in enumerate(found):
            if k not in used and m["line"] > 0:  # the subject only carries the reference
                reasons.append(f"mail names {m['pid']} but no order line matches it")
        backed_lines = {found[k]["line"] for k in used}
        for li, ln in enumerate(lines[1:], start=1):
            if li not in backed_lines and not any(m["line"] == li for m in found) and QTY_LINE.search(ln):
                reasons.append(f"mail line with a quantity not covered by a cited order line: {ln.strip()[:80]}")
    return ("human_review" if reasons else "auto_release"), reasons


class Gate:
    def __init__(self, odata_url: str | None = None):
        self.reader = Client("approver", base_url=odata_url)   # reads all orders, never releases
        self.releaser = Client("gate", base_url=odata_url)     # role AutoRelease

    def decide(self, mail: dict, agent_output: dict | None, release: bool = True) -> Decision:
        orders = self.reader.query("SalesOrders", filter=f"sourceMessage eq {quote(mail['id'])}",
                                   expand="items($select=pos,product_ID,quantity,unitPrice,netAmount;$orderby=pos)",
                                   orderby="createdAt")
        sender = self.reader.query("Contacts", filter=f"email eq {quote(mail['from'].lower())}")
        sender_customer = sender[0]["customer_ID"] if sender else None
        materials, new_customer = {}, {}
        for c in {o["customer_ID"] for o in orders}:
            for m in self.reader.query("CustomerMaterials", filter=f"customer_ID eq {quote(c)}"):
                materials[(c, m["customerMaterial"])] = m["product_ID"]
            cust = self.reader.query("Customers", filter=f"ID eq {quote(c)}")
            new_customer[c] = bool(cust and cust[0]["newCustomer"])
        catalogue = [p["ID"] for p in self.reader.query("Products", select="ID")] if orders else []
        route, reasons = check(mail, agent_output, orders, sender_customer, materials, new_customer, catalogue)
        quiet = dict(agent_output, uncertainties=[]) if agent_output else None
        d = Decision(mail["id"], route, [o["ID"] for o in orders], reasons, route_without_agent_doubt=check(
            mail, quiet, orders, sender_customer, materials, new_customer, catalogue)[0])
        if route == "auto_release" and release:
            try:
                self.releaser.post(f"/SalesOrders({orders[0]['ID']})/release", {"note": "auto-release by gate"})
                d.released = True
            except ODataError as e:  # the ERP said no: leave it to a person
                d.route, d.reasons = "human_review", [f"ERP refused auto-release: {e.message}"]
        return d

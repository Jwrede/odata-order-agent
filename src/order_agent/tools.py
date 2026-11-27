"""ERP tools the agent may use. Each one is a thin, typed wrapper over the CAP OData service,
called as the technical user `agent` (role OrderAgent). The tool layer adds two things the ERP
does not know about: the id of the mail being processed (set by the operator, not by the model)
and a log of every call with its result.
"""

from __future__ import annotations

import json
import re

from .odata import Client, ODataError, quote

PRODUCT_FIELDS = "ID,name,description,unit,packSize,listPrice,stock,discontinued"

TOOLS = [
    {"name": "lookup_sender",
     "description": "Find the customer that a sender mail address belongs to. Returns the customer (ID, name, city, blocked, newCustomer) or found=false.",
     "inputSchema": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}},
    {"name": "search_products",
     "description": "Full-text search in the product catalogue (ID, name, description). Returns up to 10 products with unit, packSize (pieces per sales unit), listPrice (EUR per sales unit), stock (sales units) and discontinued.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_products",
     "description": "Get products by exact product ID.",
     "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20}}, "required": ["ids"]}},
    {"name": "resolve_customer_material",
     "description": "Translate a customer's own material number into our product, using the customer-material records of the ERP.",
     "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "string"}, "customer_material": {"type": "string"}},
                     "required": ["customer_id", "customer_material"]}},
    {"name": "create_sales_order",
     "description": ("Create one sales order in the ERP with status Proposed. Prices come from the ERP. The ERP rejects "
                     "unknown or blocked customers, duplicates of an existing customer reference, unknown or discontinued "
                     "products, quantities above stock, past delivery dates and orders above the credit limit. "
                     "quantity is in sales units of the product (see unit and packSize). requested_date is YYYY-MM-DD or null."),
     "inputSchema": {"type": "object", "properties": {
         "customer_id": {"type": "string"},
         "customer_reference": {"type": "string", "description": "the customer's purchase order number"},
         "requested_date": {"type": ["string", "null"]},
         "items": {"type": "array", "minItems": 1, "maxItems": 30, "items": {"type": "object", "properties": {
             "product_id": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["product_id", "quantity"]}},
         "note": {"type": "string", "description": "short note for the human reviewer"}},
         "required": ["customer_id", "customer_reference", "items"]}},
]

TOOL_NAMES = [t["name"] for t in TOOLS]


class ToolError(Exception):
    pass


class Tools:
    def __init__(self, client: Client, message_id: str, log=None):
        self.client, self.message_id, self.log = client, message_id, log

    def call(self, name: str, args: dict) -> tuple[dict | list, bool]:
        """Run one tool. Returns (result, is_error) and appends the call to the log."""
        try:
            result, err = getattr(self, "_" + name)(**args), False
        except (ODataError, ToolError) as e:
            result, err = {"error": str(e)}, True
        except (AttributeError, TypeError) as e:
            result, err = {"error": f"bad tool call {name}: {e}"}, True
        if self.log is not None:
            self.log.write(json.dumps({"tool": name, "args": args, "error": err, "result": normalize(result)},
                                      ensure_ascii=False, sort_keys=True) + "\n")
            self.log.flush()
        return result, err

    def _lookup_sender(self, email: str):
        rows = self.client.query("Contacts", filter=f"email eq {quote(email.strip().lower())}", expand="customer")
        if not rows or not rows[0].get("customer"):
            return {"found": False}
        return {"found": True, "customer": rows[0]["customer"]}

    def _search_products(self, query: str):
        words = [w for w in re.split(r"[^\w,.-]+", query) if len(w) >= 2][:6]
        if not words:
            raise ToolError("empty query")
        # all words must occur in ID, name or description (case-insensitive)
        cond = " and ".join(
            "(" + " or ".join(f"contains(tolower({f}),{quote(w.lower())})" for f in ("ID", "name", "description")) + ")"
            for w in words)
        return self.client.query("Products", filter=cond, select=PRODUCT_FIELDS, top="10", orderby="ID")

    def _get_products(self, ids: list):
        if not ids:
            raise ToolError("no ids")
        cond = " or ".join(f"ID eq {quote(str(i))}" for i in ids[:20])
        return self.client.query("Products", filter=cond, select=PRODUCT_FIELDS, orderby="ID")

    def _resolve_customer_material(self, customer_id: str, customer_material: str):
        rows = self.client.query("CustomerMaterials", filter=(
            f"customer_ID eq {quote(customer_id)} and customerMaterial eq {quote(customer_material.strip())}"),
            expand=f"product($select={PRODUCT_FIELDS})")
        if not rows:
            return {"found": False}
        return {"found": True, "product": rows[0]["product"]}

    def _create_sales_order(self, customer_id: str, customer_reference: str, items: list,
                            requested_date: str | None = None, note: str | None = None):
        body = {"customer_ID": customer_id, "customerReference": customer_reference,
                "requestedDate": requested_date or None, "sourceMessage": self.message_id,
                "agentNote": (note or "")[:1000] or None,
                "items": [{"product_ID": str(i["product_id"]), "quantity": i["quantity"]} for i in items]}
        o = self.client.post("/SalesOrders", body)
        return {"ID": o["ID"], "status": o["status"], "customer_ID": o["customer_ID"],
                "customerReference": o["customerReference"], "requestedDate": o.get("requestedDate"),
                "netAmount": o["netAmount"],
                "items": [{k: it[k] for k in ("pos", "product_ID", "quantity", "unitPrice", "netAmount")}
                          for it in o.get("items", [])]}


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def normalize(result):
    """Result with generated ids replaced, so a replay can compare it with the recording."""
    return json.loads(_UUID.sub("<uuid>", json.dumps(result, ensure_ascii=False, sort_keys=True)))

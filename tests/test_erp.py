"""Authorization and business rules of the CAP service, called over OData like the agent does."""

import pytest

from order_agent.odata import Client, ODataError


def c(user, url):
    return Client(user, base_url=url)


def create(url, user="agent", **kw):
    body = {"customer_ID": "C1001", "customerReference": kw.pop("ref"), "sourceMessage": "t",
            "items": kw.pop("items", [{"product_ID": "HS-NIT-L", "quantity": 1}])}
    body.update(kw)
    return c(user, url).post("/SalesOrders", body)


def err(fn):
    with pytest.raises(ODataError) as e:
        fn()
    return e.value


def test_prices_and_status_come_from_the_erp(erp_url):
    o = create(erp_url, ref="T-price", status="Released", netAmount=1,
               items=[{"product_ID": "HS-NIT-L", "quantity": 2, "unitPrice": 0.01}])
    assert o["status"] == "Proposed" and o["netAmount"] == "109.80" and o["items"][0]["unitPrice"] == "54.90"
    assert o["createdBy"] == "agent"


def test_agent_cannot_release_and_sees_only_own_orders(erp_url):
    o = create(erp_url, ref="T-rel")
    assert err(lambda: c("agent", erp_url).post(f"/SalesOrders({o['ID']})/release", {})).status == 403
    refs = {x["customerReference"] for x in c("agent", erp_url).query("SalesOrders")}
    assert "T-rel" in refs and "4500012345" not in refs  # seeded order was created by someone else
    assert "4500012345" in {x["customerReference"] for x in c("approver", erp_url).query("SalesOrders")}


def test_agent_does_not_see_credit_data(erp_url):
    cust = c("agent", erp_url).get("/Customers('C1002')")
    assert "creditLimit" not in cust and "openItems" not in cust
    assert err(lambda: c("agent", erp_url).query("Customers", select="creditLimit")).status == 400


def test_auto_release_limit_and_new_customer_enforced_in_the_erp(erp_url):
    big = create(erp_url, ref="T-big", items=[{"product_ID": "HS-NIT-L", "quantity": 50}])  # 2745 EUR
    e = err(lambda: c("gate", erp_url).post(f"/SalesOrders({big['ID']})/release", {}))
    assert e.status == 403 and "auto-release limit" in e.message
    new = create(erp_url, ref="T-new", customer_ID="C1003")
    e = err(lambda: c("gate", erp_url).post(f"/SalesOrders({new['ID']})/release", {}))
    assert e.status == 403 and "new customer" in e.message
    small = create(erp_url, ref="T-small")
    assert c("gate", erp_url).post(f"/SalesOrders({small['ID']})/release", {})["releasedBy"] == "gate"
    # an approver may release above the limit, and only once
    assert c("approver", erp_url).post(f"/SalesOrders({big['ID']})/release", {"note": "ok"})["status"] == "Released"
    assert err(lambda: c("approver", erp_url).post(f"/SalesOrders({big['ID']})/release", {})).status == 409


def test_gate_cannot_decline_but_approver_can(erp_url):
    o = create(erp_url, ref="T-dec")
    assert err(lambda: c("gate", erp_url).post(f"/SalesOrders({o['ID']})/decline", {"reason": "x"})).status == 403
    assert err(lambda: c("approver", erp_url).post(f"/SalesOrders({o['ID']})/decline", {})).status == 400
    assert c("approver", erp_url).post(f"/SalesOrders({o['ID']})/decline", {"reason": "wrong size"})["status"] == "Rejected"
    # a declined reference may be ordered again
    assert create(erp_url, ref="T-dec")["status"] == "Proposed"


@pytest.mark.parametrize("kw,status,text", [
    ({"ref": "4500012345"}, 409, "duplicate"),
    ({"ref": "T-blk", "customer_ID": "C1006"}, 403, "blocked"),
    ({"ref": "T-unk", "customer_ID": "C9999"}, 400, "unknown customer"),
    ({"ref": "T-cred", "customer_ID": "C1002", "items": [{"product_ID": "HS-NIT-XL", "quantity": 20}]}, 422, "credit limit"),
    ({"ref": "T-stock", "items": [{"product_ID": "TS-125-INOX", "quantity": 9}]}, 409, "insufficient stock"),
    ({"ref": "T-disc", "items": [{"product_ID": "KB-200-WS", "quantity": 1}]}, 400, "discontinued"),
    ({"ref": "T-prod", "items": [{"product_ID": "NOPE", "quantity": 1}]}, 400, "unknown product"),
    ({"ref": "T-qty", "items": [{"product_ID": "HS-NIT-L", "quantity": 0}]}, 400, "invalid quantity"),
    ({"ref": "T-empty", "items": []}, 400, "at least one item"),
    ({"ref": "T-date", "requestedDate": "2026-11-20"}, 400, "in the past"),
    ({"ref": ""}, 400, "customerReference"),
])
def test_order_rules(erp_url, kw, status, text):
    e = err(lambda: create(erp_url, **kw))
    assert e.status == status and text in e.message


def test_other_roles(erp_url):
    assert err(lambda: create(erp_url, user="viewer", ref="T-view")).status == 403
    assert err(lambda: create(erp_url, user="approver", ref="T-appr")).status == 403
    assert err(lambda: c("viewer", erp_url).query("Contacts")).status == 403
    assert err(lambda: Client("agent", password="wrong", base_url=erp_url).query("Products")).status == 401

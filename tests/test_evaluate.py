from order_agent.evaluate import score

EXP = {"kind": "order", "customer": "C1", "reference": "R1", "lines": {"A": 1}, "requested_date": None}
OK = {"customer": "C1", "reference": "R1", "lines": {"A": 1}, "requested_date": None}


def test_order_scoring():
    assert score(EXP, [OK], True) == {"correct": True, "wrong_proposal": False, "critical": False}
    bad = dict(OK, lines={"A": 2})
    assert score(EXP, [bad], False) == {"correct": False, "wrong_proposal": True, "critical": False}
    assert score(EXP, [bad], True)["critical"]
    assert not score(EXP, [OK, OK], False)["correct"]
    assert not score(EXP, [dict(OK, requested_date="2026-12-01")], False)["correct"]


def test_no_order_and_clarify():
    assert score({"kind": "no_order"}, [], False)["correct"]
    assert score({"kind": "no_order"}, [OK], False) == {"correct": False, "wrong_proposal": True, "critical": False}
    assert score({"kind": "clarify"}, [OK], False) == {"correct": True, "wrong_proposal": True, "critical": False}
    assert score({"kind": "clarify"}, [OK], True)["critical"]

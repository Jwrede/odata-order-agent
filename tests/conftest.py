import json
from pathlib import Path

import pytest

from order_agent import erp

ROOT = Path(__file__).resolve().parents[1]


def cap_available() -> bool:
    return (erp.CAP_DIR / "node_modules" / "@sap" / "cds").exists()


@pytest.fixture(scope="session")
def erp_url():
    if not cap_available():
        pytest.skip("CAP not installed (npm ci in cap/)")
    with erp.running() as url:
        yield url


@pytest.fixture(scope="session")
def mails():
    return {m["id"]: m for m in map(json.loads, (ROOT / "eval" / "mails.jsonl").read_text().splitlines())}

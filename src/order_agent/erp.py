"""Start and stop the CAP order service (in-memory SQLite, seeded from cap/db/data) for
measurements and tests."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

CAP_DIR = Path(os.environ.get("OOA_CAP_DIR", Path(__file__).resolve().parents[2] / "cap"))
BUSINESS_DATE = "2026-11-27"  # fixed "today" of the measurement, so past-date checks are reproducible


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def running(port: int | None = None, today: str = BUSINESS_DATE, log_path: str | None = None):
    """Fresh CAP server; yields the OData base URL."""
    port = port or free_port()
    serve = CAP_DIR / "node_modules" / "@sap" / "cds" / "bin" / "serve.js"
    if not serve.exists():
        raise RuntimeError(f"{serve} missing: run `npm ci` in {CAP_DIR}")
    env = dict(os.environ, PORT=str(port), ORDER_TODAY=today)
    log = open(log_path or os.devnull, "w")
    p = subprocess.Popen(["node", str(serve)], cwd=CAP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/odata/v4/orders"
    try:
        deadline = time.monotonic() + 60
        while True:
            if p.poll() is not None:
                raise RuntimeError(f"CAP server exited with {p.returncode}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if time.monotonic() > deadline:
                    raise RuntimeError("CAP server did not start within 60 s")
                time.sleep(0.3)
        yield url
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()

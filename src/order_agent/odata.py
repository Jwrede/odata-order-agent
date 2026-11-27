"""Small OData V4 client for the CAP order service (stdlib only, basic auth)."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://localhost:4004/odata/v4/orders"


class ODataError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


class Client:
    def __init__(self, user: str, password: str | None = None, base_url: str | None = None, timeout: float = 30):
        self.base = (base_url or os.environ.get("OOA_ODATA_URL") or DEFAULT_URL).rstrip("/")
        self.user = user
        self.timeout = timeout
        pw = password if password is not None else os.environ.get(f"OOA_PASSWORD_{user.upper()}", user)
        self._auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self._auth, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                msg = json.loads(raw)["error"]["message"]
            except Exception:
                msg = raw.decode(errors="replace")[:300]
            raise ODataError(e.code, msg) from None
        return json.loads(raw) if raw else {}

    def get(self, path: str, **params) -> dict:
        return self._req("GET", path, {k if k.startswith("$") else f"${k}": v for k, v in params.items()})

    def query(self, entity: str, **params) -> list[dict]:
        return self.get(f"/{entity}", **params).get("value", [])

    def post(self, path: str, body: dict) -> dict:
        return self._req("POST", path, body=body)


def quote(s: str) -> str:
    """OData string literal."""
    return "'" + s.replace("'", "''") + "'"

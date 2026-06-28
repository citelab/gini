"""gBuilder's client for a remote GINI server (the Phase-3 transport).

The GUI builds a topology and, in remote mode, hands it to this client, which sends it to
the brokered GINI server over an authenticated HTTP API. The server compiles + policy-checks
+ runs it on the Kata host; metrics/startup come back the same way. Same shape as the local
orchestrator path, so the canvas/compiler don't care which backend is active.

`transport` is injectable — production uses the urllib HTTP transport below; tests pass a
shim that calls `GiniServer.handle` directly, exercising the whole protocol without sockets.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class RemoteClient:
    def __init__(self, base_url: str = "", transport=None, token: str | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self._transport = transport or self._http

    # -- transport ---------------------------------------------------------- #
    def _http(self, method: str, path: str, token: str | None, body: dict) -> tuple[int, dict]:
        data = json.dumps(body or {}).encode() if method == "POST" else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            r = urllib.request.urlopen(req, timeout=30)
            return r.getcode(), json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except Exception:                       # noqa: BLE001
                return e.code, {"error": f"HTTP {e.code}"}
        except Exception as e:                       # noqa: BLE001 — connection refused, DNS, …
            return 0, {"error": str(e)}

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        return self._transport(method, path, self.token, body or {})

    # -- API ---------------------------------------------------------------- #
    def login(self, username: str, password: str) -> tuple[bool, str]:
        st, body = self._transport("POST", "/login", None,
                                   {"username": username, "password": password})
        if st == 200 and body.get("token"):
            self.token = body["token"]
            return True, ""
        return False, body.get("error", "login failed")

    def run(self, topology) -> tuple[bool, str]:
        st, body = self._call("POST", "/run", {"topology": topology.to_dict()})
        return (st == 200 and bool(body.get("ok"))), body.get("message") or body.get("error", "")

    def stop(self) -> tuple[bool, str]:
        st, body = self._call("POST", "/stop")
        return st == 200, body.get("message") or body.get("error", "")

    def status(self) -> dict:
        return self._call("GET", "/status")[1].get("status", {})

    def metrics(self) -> dict:
        return self._call("GET", "/metrics")[1]      # {"stats": {...}, "startup": {...}}

    def capabilities(self) -> dict:
        return self._call("GET", "/capabilities")[1]  # {"kata": bool}

    def kata_available(self) -> bool:
        return bool(self.capabilities().get("kata"))

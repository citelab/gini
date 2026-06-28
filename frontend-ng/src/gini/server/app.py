"""The GINI server request router + a stdlib HTTP wrapper.

`GiniServer.handle(method, path, token, body)` is the whole API as a pure function of its
inputs (so it's unit-testable with a fake orchestrator, no Docker). The HTTP layer below is
a thin shell that parses requests and calls it.

Flow for /run: take the student's **topology** -> GINI's own compiler -> policy.enforce ->
the student's namespaced orchestrator. The student never supplies a compose file or a
Docker command.
"""
from __future__ import annotations

import json
import threading

from ..domain.topology import Topology
from ..services.compiler import RuntimeCompiler
from .auth import Tokens, UserStore
from .policy import PolicyError, default_allowed_images, enforce
from .session import SessionManager


class GiniServer:
    def __init__(self, users: UserStore, tokens: Tokens, sessions: SessionManager,
                 orchestrator_factory, allowed_images=None, max_cpus: float = 2.0) -> None:
        self.users = users
        self.tokens = tokens
        self.sessions = sessions
        self._make_orch = orchestrator_factory        # (project, workdir) -> orchestrator
        self.allowed_images = set(allowed_images) if allowed_images else default_allowed_images()
        self.max_cpus = max_cpus
        self._orch: dict = {}                          # user -> orchestrator
        self._runs: dict = {}                          # user -> {"state", "message"}
        self._lock = threading.Lock()

    def _orch_for(self, user: str):
        if user not in self._orch:
            self._orch[user] = self._make_orch(
                self.sessions.project_name(user), self.sessions.workdir(user))
        return self._orch[user]

    # the entire API as a pure (status, payload) function -------------------- #
    def handle(self, method: str, path: str, token: str | None, body: dict) -> tuple[int, dict]:
        if method == "POST" and path == "/login":
            u, p = body.get("username"), body.get("password")
            if self.users.verify(u, p):
                return 200, {"token": self.tokens.mint(u)}
            return 401, {"error": "invalid credentials"}

        user = self.tokens.verify(token or "")
        if not user:
            return 401, {"error": "authentication required"}

        try:
            if method == "POST" and path == "/run":
                return self._run(user, body)
            if method == "POST" and path == "/stop":
                ok, msg = self._orch_for(user).down()
                with self._lock:
                    self._runs[user] = {"state": "stopped", "message": msg}
                return (200 if ok else 500), {"ok": ok, "message": msg}
            if method == "GET" and path == "/status":
                with self._lock:
                    run = dict(self._runs.get(user, {"state": "stopped", "message": ""}))
                return 200, {"status": self._orch_for(user).status(), "run": run}
            if method == "GET" and path == "/metrics":
                o = self._orch_for(user)
                return 200, {"stats": o.stats_all(), "startup": o.startup_times()}
            if method == "GET" and path == "/capabilities":
                return 200, {"kata": self._orch_for(user).runtime_available("kata")}
        except PolicyError as e:
            return 400, {"error": str(e)}
        except Exception as e:                          # noqa: BLE001 — report, don't crash
            return 500, {"error": str(e)}
        return 404, {"error": "not found"}

    def _run(self, user: str, body: dict) -> tuple[int, dict]:
        if "topology" not in body:
            return 400, {"error": "missing topology"}
        topo = Topology.from_dict(body["topology"])     # the student described elements + links
        cfg = RuntimeCompiler().compile(topo)           # WE compile it, with our trusted compiler
        enforce(cfg, self.allowed_images, self.max_cpus)  # raises PolicyError on a violation (fast)
        o = self._orch_for(user)
        workdir = str(self.sessions.workdir(user))
        with self._lock:
            self._runs[user] = {"state": "starting", "message": ""}

        # `docker compose up` can take minutes on first run (image pulls + builds), so run it
        # in the background and let the client poll /status — never block the HTTP request.
        def launch():
            try:
                ok, msg = o.up(cfg, workdir)
                state = "running" if ok else "error"
            except Exception as e:                       # noqa: BLE001
                ok, msg, state = False, str(e), "error"
            with self._lock:
                self._runs[user] = {"state": state, "message": msg}

        threading.Thread(target=launch, daemon=True).start()
        return 202, {"ok": True, "state": "starting"}


# --------------------------------------------------------------------------- #
# Thin HTTP shell (stdlib). Production entry point lives in __main__.py.
# --------------------------------------------------------------------------- #
def serve(server: "GiniServer", host: str = "0.0.0.0", port: int = 10000) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                      # quiet
            pass

        def _token(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            return auth[7:] if auth.startswith("Bearer ") else None

        def _dispatch(self, method: str) -> None:
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {}
            status, payload = server.handle(method, self.path.split("?", 1)[0],
                                            self._token(), body if isinstance(body, dict) else {})
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    print(f"[gini-server] listening on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()

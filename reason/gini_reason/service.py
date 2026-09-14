"""The reason service — one call, on loopback, answering nobody.

Step 2 of docs/design/gini-ai-three-doors.md. It drafts; it never posts. Whether a draft is shown
to a student, drafted to staff, or thrown away is the Teaching Center's decision and depends on
which door asked — and this process has no idea which door that was, deliberately.

**Bound to 127.0.0.1 and nothing else.** It holds no authentication because it has no user: the
only thing that may call it is the Center on the same host. A service like this listening on a
public interface would be an unauthenticated model endpoint on a university network, which is a
different kind of object entirely.

**Separate from the Center, and that is the whole point.** `server.py`'s first paragraph has said
since v1 that no model client is imported and no outbound model call is made, because a student
submitting at 23:58 must not be behind one. That stays true: the Center calls this over loopback
with a short timeout and drops a rung when it does not answer.
"""
from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import audit
from .ladder import answer

log = logging.getLogger("gini_reason")

HOST = "127.0.0.1"
PORT = int(os.environ.get("GINI_REASON_PORT", "8765"))


def _llm():
    """The model, or None. None is a supported state, not a failure — the ladder's lower rungs are
    the outage plan and they do not need one."""
    url = os.environ.get("GINI_LLM_URL", "").strip()
    if not url:
        return None
    try:
        from gini.agent.llm.ollama import OllamaBackend
    except Exception:                                  # noqa: BLE001
        return None
    try:
        be = OllamaBackend(url=url, model=os.environ.get("GINI_LLM_MODEL", "llama3.1"))
        return be if be.available() else None
    except Exception:                                  # noqa: BLE001
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "GINI-Reason/1"

    def log_message(self, *a):
        pass

    def _send(self, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):        # noqa: N802
        if self.path.startswith("/health"):
            be = _llm()
            return self._send(200, {"ok": True, "model": bool(be),
                                    "url": os.environ.get("GINI_LLM_URL", "")})
        self._send(404, {"error": "Only POST /answer and GET /health."})

    def do_POST(self):       # noqa: N802
        if not self.path.startswith("/answer"):
            return self._send(404, {"error": "Only POST /answer and GET /health."})
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return self._send(400, {"error": "Body must be JSON."})

        question = str(body.get("question", "")).strip()
        if not question:
            return self._send(400, {"error": "No question."})
        try:
            d = answer(question, llm=_llm(), course_hits=body.get("course_hits") or (),
                       audit=lambda q, g, t: audit.flags(audit.review(q, g, t)))
        except Exception as e:                         # noqa: BLE001 — never take the Center down
            log.exception("drafting failed")
            return self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}",
                                    "rung": "L3", "text": "", "flags": [], "citations": []})
        self._send(200, {"ok": True, "text": d.text, "rung": d.rung, "strength": d.strength,
                         "citations": d.citations, "flags": d.flags, "used_model": d.used_model})


def serve(port: int = 0) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    httpd = ThreadingHTTPServer((HOST, port or PORT), Handler)
    be = _llm()
    log.info("reason service on http://%s:%d — model: %s", HOST, httpd.server_address[1],
             os.environ.get("GINI_LLM_URL") if be else "none (the lower rungs still answer)")
    log.info("drafting only; nothing here is posted to anybody")
    httpd.serve_forever()
    return 0

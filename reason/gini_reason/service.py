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


#: Where Ollama is when nobody says. The tunnel to citelab-1 lands on the same port Ollama uses
#: locally, so the common setup needs no configuration at all — and an unset variable should not
#: look like a missing model.
DEFAULT_LLM = "http://127.0.0.1:11434"


def llm_with_reason():
    """`(backend, reason)`. The backend is None when there is no model, and `reason` says WHY.

    Three different ways to end up with no model — nothing configured, the import failing, the
    server not answering — and the first version of this returned None for all three in silence.
    That is the failure this whole project keeps meeting: a thing that quietly does less, with
    every visible signal saying it is fine. `model=no` on a machine with a working tunnel is not a
    diagnosis, it is a shrug.
    """
    url = os.environ.get("GINI_LLM_URL", "").strip()
    where = "GINI_LLM_URL"
    if not url:
        url, where = DEFAULT_LLM, "the default"
    try:
        from gini.agent.llm.ollama import OllamaBackend
    except Exception as e:                             # noqa: BLE001
        return None, (f"gini.agent.llm.ollama could not be imported ({e}). Is frontend-ng/src on "
                      f"PYTHONPATH?")
    model = os.environ.get("GINI_LLM_MODEL", "llama3.1")
    try:
        be = OllamaBackend(url=url, model=model)
    except Exception as e:                             # noqa: BLE001
        return None, f"could not build the Ollama client for {url} ({e})"
    if not be.available():
        return None, (f"nothing answered at {url} (from {where}). If the tunnel is up, check "
                      f"`curl {url}/api/tags`; if the model lives elsewhere, set GINI_LLM_URL.")

    # The server answering is not the model existing, and conflating them costs a real diagnosis:
    # `available()` asks /api/tags and is satisfied by a reply, so a wrong GINI_LLM_MODEL passes it
    # and then fails at the first question with a bare `HTTP Error 404: Not Found`. Nothing in that
    # names the model, and the obvious reading is that the tunnel broke.
    have = _installed(url)
    if have and not any(m == model or m.split(":")[0] == model.split(":")[0] for m in have):
        return None, (f"{url} is up but has no model called {model!r}. It serves: "
                      f"{', '.join(sorted(have)[:8])}. Set GINI_LLM_MODEL to one of those.")
    return be, f"{model} at {url} (from {where})"


def _installed(url: str) -> set:
    """Model names the server actually has. Empty on any failure — an unreadable list must not
    become an accusation that the model is missing."""
    import json as _json
    import urllib.request as _u
    try:
        with _u.urlopen(url.rstrip("/") + "/api/tags", timeout=3.0) as r:
            data = _json.loads(r.read() or b"{}")
        return {str(m.get("name", "")) for m in (data.get("models") or []) if m.get("name")}
    except Exception:                                  # noqa: BLE001
        return set()


def _llm():
    return llm_with_reason()[0]


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
            be, why = llm_with_reason()
            return self._send(200, {"ok": True, "model": bool(be), "why": why})
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
    be, why = llm_with_reason()
    log.info("reason service on http://%s:%d", HOST, httpd.server_address[1])
    log.info("model: %s", why if be else f"NONE — {why}")
    if not be:
        log.info("the lower rungs still answer; this is the outage plan, not a broken service")
    log.info("drafting only; nothing here is posted to anybody")
    httpd.serve_forever()
    return 0

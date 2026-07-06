#!/usr/bin/env python3
"""GINI Teaching Center — reference server (skeleton).

The single service both teachers and students talk to: system of record for **Lessons** and
student **Profiles**, and the collector for Mission **submissions**. This is a minimal stdlib
reference implementation of the GINI Learning Protocol (GLP) so the client can be exercised
end-to-end; a production deployment would add a real datastore, a roster, role-based auth, and
(Phase 6) a Docker-capable re-grader for the hybrid grading authority.

Layout it serves from (COURSE_ROOT):
    courses/<course>/manifest.json          # released lessons
    lessons/<lesson_id>/lesson.yaml         # a Lesson Pack (Phase 5 = yaml text)
    data/profiles/<student>.json            # authoritative profiles (monotonic-merged on PUT)
    data/submissions.jsonl                  # appended Mission results

Run:  COURSE_ROOT=./example PORT=8080 python teaching-center/server.py
Auth: a Bearer token is required but accepted as any non-empty string in this skeleton
      (replace with a real enrollment roster).
"""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("COURSE_ROOT", "./example")).resolve()
PORT = int(os.environ.get("PORT", "8080"))

_MANIFEST = re.compile(r"^/courses/([\w-]+)/manifest$")
_PACK = re.compile(r"^/lessons/([\w-]+)/pack$")
_PROFILE = re.compile(r"^/students/([\w-]+)/profile$")
_SUBMIT = re.compile(r"^/courses/([\w-]+)/submissions$")

_BAND_RANK = {"": 0, "incomplete": 1, "partial": 2, "pass": 3, "gold": 4}


def _merge_profiles(a: dict, b: dict) -> dict:
    """Monotonic union merge (same rule as the client's domain.profile.merge): best-band max,
    attempts max, completed OR, best_time min. Conflict-free because the data is monotonic."""
    out = {"student_id": a.get("student_id") or b.get("student_id"), "lessons": {}}
    la, lb = a.get("lessons", {}), b.get("lessons", {})
    for lid in set(la) | set(lb):
        ra, rb = la.get(lid), lb.get(lid)
        if not ra or not rb:
            out["lessons"][lid] = ra or rb
            continue
        times = [t for t in (ra.get("best_time_s"), rb.get("best_time_s")) if t is not None]
        out["lessons"][lid] = {
            "lesson_id": lid, "concept": ra.get("concept") or rb.get("concept"),
            "best_band": max((ra.get("best_band", ""), rb.get("best_band", "")), key=lambda x: _BAND_RANK.get(x, 0)),
            "attempts_used": max(ra.get("attempts_used", 0), rb.get("attempts_used", 0)),
            "best_time_s": min(times) if times else None,
            "completed": ra.get("completed", False) or rb.get("completed", False),
            "last_played": max(ra.get("last_played", 0), rb.get("last_played", 0)),
            "snapshot": ra.get("snapshot") if ra.get("last_played", 0) >= rb.get("last_played", 0) else rb.get("snapshot", ""),
        }
    return out


class Handler(BaseHTTPRequestHandler):
    def _authed(self) -> bool:
        return bool(self.headers.get("Authorization", "").removeprefix("Bearer ").strip())

    def _send(self, status: int, obj=None, *, text: str | None = None) -> None:
        body = (text if text is not None else json.dumps(obj) if obj is not None else "").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if not self._authed():
            return self._send(401)
        m = _MANIFEST.match(self.path)
        if m:
            p = ROOT / "courses" / m.group(1) / "manifest.json"
            return self._send(200, json.loads(p.read_text())) if p.exists() else self._send(404)
        m = _PACK.match(self.path)
        if m:
            p = ROOT / "lessons" / m.group(1) / "lesson.yaml"
            return self._send(200, text=p.read_text()) if p.exists() else self._send(404)
        m = _PROFILE.match(self.path)
        if m:
            p = ROOT / "data" / "profiles" / f"{m.group(1)}.json"
            if p.exists():
                return self._send(200, json.loads(p.read_text()))
            return self._send(200, {"student_id": m.group(1), "lessons": {}})
        self._send(404)

    def do_PUT(self):  # noqa: N802
        if not self._authed():
            return self._send(401)
        m = _PROFILE.match(self.path)
        if not m:
            return self._send(404)
        incoming = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        p = ROOT / "data" / "profiles" / f"{m.group(1)}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(p.read_text()) if p.exists() else {"student_id": m.group(1), "lessons": {}}
        p.write_text(json.dumps(_merge_profiles(existing, incoming)))
        self._send(204)

    def do_POST(self):  # noqa: N802
        if not self._authed():
            return self._send(401)
        m = _SUBMIT.match(self.path)
        if not m:
            return self._send(404)
        rec = self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
        out = ROOT / "data" / "submissions.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(rec.decode().strip() + "\n")
        self._send(201, {"ok": True})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print(f"GINI Teaching Center (reference) on :{PORT}, serving {ROOT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

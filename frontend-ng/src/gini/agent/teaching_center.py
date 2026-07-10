"""Teaching Center client — the GINI Learning Protocol (GLP), git-style.

The Teaching Center is the system of record for both Lessons and student Profiles; the client
**checks out** a course (its released lessons + the student's profile) into a local cache, plays
offline, and **checks in** results. Because profile data is monotonic (best-band max, attempts
sum, mastery union), the merge on checkin is deterministic and conflict-free — the property that
makes offline-first work (see `domain.profile.merge`).

Endpoints (see the design doc §8):
    GET  /courses/{course}/manifest         → released lessons (id,title,release,due,pack_hash)
    GET  /lessons/{id}/pack                  → the Lesson Pack (yaml text in Phase 5)
    GET  /students/{id}/profile              → authoritative profile
    PUT  /students/{id}/profile              → reconcile (server merges monotonically)
    POST /courses/{course}/submissions       → a Mission result (queued offline, flushed on sync)

The HTTP transport is injected (`transport(method, path, body) -> (status, obj)`), so the whole
client is unit-testable with a fake and degrades to the on-disk cache when offline. Pure stdlib.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..domain import lesson as _lesson
from ..domain import profile as _profile


class TeachingCenterClient:
    def __init__(self, base_url: str, *, course: str, student_id: str, token: str = "",
                 cache_dir: str | Path = "", transport=None, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.course = course
        self.student_id = student_id
        self.token = token
        self.timeout = timeout
        self.cache = Path(cache_dir) if cache_dir else Path.home() / ".gini" / "teaching_center"
        self._transport = transport or self._http
        self._queue_path = self.cache / "submission_queue.json"

    # -- transport ---------------------------------------------------------- #
    def _http(self, method: str, path: str, body=None):
        url = self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return 0, None                       # offline / unreachable

    def online(self) -> bool:
        status, _ = self._transport("GET", f"/courses/{self.course}/manifest", None)
        return status == 200

    # -- cache helpers ------------------------------------------------------ #
    def _cache_write(self, name: str, obj) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / name).write_text(json.dumps(obj) if not isinstance(obj, str) else obj)

    def _cache_read(self, name: str):
        try:
            text = (self.cache / name).read_text()
        except OSError:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # -- lessons (pull, cached) --------------------------------------------- #
    def manifest(self) -> list[dict]:
        """The course's released lessons. Falls back to the cached manifest when offline."""
        status, obj = self._transport("GET", f"/courses/{self.course}/manifest", None)
        if status == 200 and isinstance(obj, list):
            self._cache_write("manifest.json", obj)
            return obj
        return self._cache_read("manifest.json") or []

    def available_lessons(self) -> list[dict]:
        """Released and not past-due, newest first (what Missions mode should offer)."""
        now = time.time()
        out = []
        for m in self.manifest():
            rel = _epoch(m.get("release"))
            due = _epoch(m.get("due"))
            if (rel is None or rel <= now) and (due is None or due >= now):
                out.append(m)
        return sorted(out, key=lambda m: _epoch(m.get("release")) or 0, reverse=True)

    def fetch_lesson(self, lesson_id: str, *, expected_hash: str = ""):
        """Return a playable Lesson; verify the pack hash (when known) and cache on success. Uses the
        on-disk cache when offline. A hash mismatch is rejected.

        The pack may be a **composition-by-reference** (references local fragment ids) or a
        self-contained lesson. A composition that references fragments/roles this GINI doesn't have
        fails gracefully (the version/existence check), returning None rather than mis-grading."""
        import hashlib

        import yaml

        from ..domain import composition as _comp
        status, obj = self._transport("GET", f"/lessons/{lesson_id}/pack", None)
        text = obj if isinstance(obj, str) else (obj or {}).get("yaml") if isinstance(obj, dict) else None
        if status == 200 and text:
            if expected_hash and hashlib.sha256(text.encode()).hexdigest() != expected_hash:
                return None                      # tampered / corrupt pack
            self._cache_write(f"pack_{lesson_id}.yaml", text)
        else:
            text = self._cache_read(f"pack_{lesson_id}.yaml")
        if not text:
            return None
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
        try:
            if _comp.is_composition(spec):
                return _comp.from_composition(spec, lesson_id=lesson_id)
            return _lesson.from_yaml(text)
        except (_comp.CompositionError, _lesson.LessonError):
            return None                          # incompatible / invalid pack → don't play a broken one


    # -- profile (git-style checkout / checkin) ----------------------------- #
    def _profile_path(self) -> Path:
        return self.cache / f"profile_{self.student_id}.json"

    def checkout_profile(self) -> _profile.Profile:
        """Pull the authoritative profile into a local working copy (merged with any local copy so
        offline progress isn't lost). Offline → the local working copy."""
        local = _profile.Profile.load(self._profile_path(), student_id=self.student_id)
        status, obj = self._transport("GET", f"/students/{self.student_id}/profile", None)
        if status == 200 and isinstance(obj, dict):
            server = _profile.Profile.from_dict(obj)
            merged = _profile.merge(local, server)
            merged.save(self._profile_path())
            return merged
        return local

    def checkin_profile(self, profile: _profile.Profile) -> bool:
        """Push the working copy back; the server merges monotonically. Always saves locally; when
        offline the local copy stands until the next successful checkin."""
        profile.save(self._profile_path())
        status, _ = self._transport("PUT", f"/students/{self.student_id}/profile", profile.to_dict())
        return status in (200, 204)

    # -- submissions (queue offline, flush on sync) ------------------------- #
    def submit(self, lesson_id: str, mission, *, snapshot: str = "") -> bool:
        rec = {"student": self.student_id, "lesson_id": lesson_id,
               "attempt": mission.attempt, "band": mission.last_band or mission.score().band,
               "objective_results": [(r.id, r.status) for r in mission.last_results],
               "time_taken": mission.elapsed(), "snapshot": snapshot, "ts": time.time()}
        status, _ = self._transport("POST", f"/courses/{self.course}/submissions", rec)
        if status in (200, 201, 202):
            return True
        self._enqueue(rec)                       # offline → queue for later
        return False

    def _enqueue(self, rec: dict) -> None:
        q = self._cache_read("submission_queue.json") or []
        q.append(rec)
        self._cache_write("submission_queue.json", q)

    def flush(self) -> int:
        """Try to send any queued submissions; returns how many were flushed."""
        q = self._cache_read("submission_queue.json") or []
        remaining, sent = [], 0
        for rec in q:
            status, _ = self._transport("POST", f"/courses/{self.course}/submissions", rec)
            if status in (200, 201, 202):
                sent += 1
            else:
                remaining.append(rec)
        self._cache_write("submission_queue.json", remaining)
        return sent

    def sync(self, profile: _profile.Profile | None = None) -> dict:
        """A full checkin: flush queued submissions + reconcile the profile. Returns a summary."""
        flushed = self.flush()
        merged = None
        if profile is not None:
            self.checkin_profile(profile)
            merged = self.checkout_profile()
        return {"online": self.online(), "flushed": flushed,
                "profile": merged.summary() if merged else ""}


def _epoch(v):
    """Parse a manifest date (ISO 'YYYY-MM-DD' or epoch seconds) → epoch, or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None

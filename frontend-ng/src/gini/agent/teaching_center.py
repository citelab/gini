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
import urllib.parse
import urllib.request
from pathlib import Path

from ..domain import lesson as _lesson
from ..domain import profile as _profile


class InsecureTransport(RuntimeError):
    """Raised rather than sending a password over plaintext HTTP to a remote host."""


def is_local(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1", "")


def refuse_plaintext_password(url: str, *, allow_insecure: bool = False) -> None:
    """A password on plain HTTP over campus wifi is readable by everyone on that wifi — and
    'classroom-scale' doesn't change that, because it's the same wifi. So we refuse, loudly, rather
    than quietly doing the unsafe thing. localhost is exempt (it never leaves the machine), and an
    explicit override exists for demos — but it has to be a conscious act."""
    if allow_insecure or is_local(url):
        return
    if not url.lower().startswith("https://"):
        raise InsecureTransport(
            "Refusing to send your password over an unencrypted connection.\n\n"
            f"The course server ({url}) isn't using HTTPS, so anyone on the same network could read "
            "your password. Ask your instructor for the https:// address.\n\n"
            "(If this is a demo on a trusted network, tick “Allow insecure connection” in "
            "Settings → Teaching Center.)")


class TeachingCenterClient:
    def __init__(self, base_url: str, *, course: str, student_id: str, token: str = "",
                 session: str = "", cache_dir: str | Path = "", transport=None,
                 timeout: float = 8.0, allow_insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.course = course
        self.student_id = student_id
        self.token = token                 # the ENROLMENT token (one-time, spent on claim)
        self.timeout = timeout
        self.allow_insecure = allow_insecure
        # honour GINI_HOME_DIR like everything else. Hard-coding ~/.gini meant the cache (and now the
        # SESSION TOKEN) escaped the app's own home-directory override — including under test.
        if cache_dir:
            self.cache = Path(cache_dir)
        else:
            from ..app.paths import gini_home
            self.cache = gini_home() / "teaching_center"
        self._transport = transport or self._http
        self._queue_path = self.cache / "submission_queue.json"
        # The SESSION token is what authenticates every request. It is stored (per course+student) in
        # the cache — never the password, which we hold only long enough to exchange it for this.
        self.session = ""
        self.role = "student"        # 'teacher' unlocks author mode in gBuilder
        self._load_session(session)

    # -- session persistence ------------------------------------------------- #
    def _session_path(self) -> Path:
        return self.cache / f"session_{self.course}_{self.student_id}.json"

    def _load_session(self, session: str = "") -> None:
        if session:
            self.session = session
            return
        try:
            d = json.loads(self._session_path().read_text())
            self.session = d.get("session", "")
            self.role = d.get("role", "student")
        except (OSError, json.JSONDecodeError, AttributeError):
            self.session = ""

    def _store_session(self, session: str, role: str = "") -> None:
        self.session = session
        if role:
            self.role = role
        self.cache.mkdir(parents=True, exist_ok=True)
        if session:
            self._session_path().write_text(json.dumps({"session": session, "role": self.role}))
        else:
            self.role = "student"
            self._session_path().unlink(missing_ok=True)

    def is_teacher(self) -> bool:
        return self.signed_in() and self.role == "teacher"

    # -- sign-in ------------------------------------------------------------- #
    def claim(self, password: str, enrolment_token: str = "") -> dict:
        """First login: exchange the teacher-issued enrolment token for a password + session."""
        refuse_plaintext_password(self.base_url, allow_insecure=self.allow_insecure)
        status, obj = self._transport("POST", "/auth/claim", {
            "id": self.student_id, "enrolment_token": enrolment_token or self.token,
            "password": password})
        return self._after_auth(status, obj)

    def login(self, password: str) -> dict:
        refuse_plaintext_password(self.base_url, allow_insecure=self.allow_insecure)
        status, obj = self._transport("POST", "/auth/login",
                                      {"id": self.student_id, "password": password})
        return self._after_auth(status, obj)

    def _after_auth(self, status, obj) -> dict:
        if status == 0:
            return {"ok": False, "error": "Can't reach the course server."}
        if not isinstance(obj, dict):
            return {"ok": False, "error": f"Unexpected reply from the course server ({status})."}
        if obj.get("ok") and obj.get("session"):
            self._store_session(obj["session"], obj.get("role", "student"))
        return obj

    def logout(self) -> None:
        try:
            self._transport("POST", "/auth/logout", {})
        except Exception:                              # noqa: BLE001 — going offline is still logout
            pass
        self._store_session("")

    def signed_in(self) -> bool:
        return bool(self.session)

    # -- transport ---------------------------------------------------------- #
    def _http(self, method: str, path: str, body=None):
        url = self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.session}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                if not raw:
                    return resp.status, None
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    # NOT every endpoint is JSON — a Lesson Pack is served as YAML *text*. Treating a
                    # non-JSON body as a failure made every lesson fetch look like "offline".
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, OSError):
            return 0, None                       # offline / unreachable

    def online(self) -> bool:
        status, _ = self._transport("GET", f"/courses/{self.course}/manifest", None)
        return status == 200

    # -- the social plane (Phases B–E) --------------------------------------- #
    def heartbeat(self, progress: dict | None = None) -> bool:
        """'I'm here', plus where I am on the current mission. Progress is what makes a group view
        worth looking at — 'Ana is on Level 3' beats a green dot."""
        status, _ = self._transport("POST", f"/courses/{self.course}/presence",
                                    {"progress": progress or {}})
        return status == 200

    def my_group(self) -> dict:
        status, obj = self._transport("GET", f"/courses/{self.course}/group", None)
        return obj if status == 200 and isinstance(obj, dict) else {"group": "", "members": []}

    def channels(self) -> list[dict]:
        status, obj = self._transport("GET", f"/courses/{self.course}/channels", None)
        return obj if status == 200 and isinstance(obj, list) else []

    def messages(self, since: float = 0.0) -> list[dict]:
        status, obj = self._transport(
            "GET", f"/courses/{self.course}/messages?since={since}", None)
        return obj if status == 200 and isinstance(obj, list) else []

    def send_message(self, to: str, body: str) -> dict:
        status, obj = self._transport("POST", f"/courses/{self.course}/messages",
                                      {"to": to, "body": body})
        if status == 0:
            return {"ok": False, "error": "Can't reach the course server."}
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def report_message(self, message_id: str, note: str = "") -> dict:
        status, obj = self._transport("POST", f"/courses/{self.course}/messages/report",
                                      {"message_id": message_id, "note": note})
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def pull_content(self) -> dict:
        """OTA: pull the course's teacher-authored fragments into the local user content layer.

        The version gate lives HERE, on the receiving end: each fragment is checked against THIS
        client's vocabulary (engine version + full validation). One authored on a newer gBuilder than
        the student has is DECLINED with a reason and skipped — the rest still install. A pack can
        never brick a student's client. Returns {installed, skipped:[{id,reason}]}."""
        status, items = self._transport("GET", f"/courses/{self.course}/content", None)
        if status != 200 or not isinstance(items, list):
            return {"installed": [], "skipped": []}
        from ..domain import content as _content
        from ..domain import fragment_yaml as _fy
        from ..domain import fragments as _frag
        from ..domain import vocabulary as _vocab
        import hashlib
        d = _content.ensure_user_content_dir()
        installed, skipped, changed = [], [], False
        served: dict[str, str] = {}                       # id -> hash of what the TC serves now
        for it in items:
            fid, text = it.get("id", ""), it.get("yaml", "")
            served[fid] = hashlib.sha256(text.encode()).hexdigest()
            ok, why = _vocab.is_compatible(str(it.get("engine_version", "")))
            if not ok:
                skipped.append({"id": fid, "reason": why}); continue
            try:
                problems = _fy.validate(_fy.from_yaml(text))
            except Exception as e:                       # noqa: BLE001 — malformed pull
                problems = [str(e)]
            if problems:
                skipped.append({"id": fid, "reason": "; ".join(problems)}); continue
            path = d / f"{fid}.yaml"
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8"); changed = True
            installed.append(fid)

        # Deletion propagation: a fragment a PRIOR pull installed that the TC no longer serves is
        # removed locally — but ONLY if the on-disk copy is still the untouched OTA copy (its hash
        # matches what we recorded). A locally-authored or edited fragment is never deleted.
        removed: list[str] = []
        prev = self._cache_read("ota_content.json") or {}
        if isinstance(prev, dict):
            for old_id, old_hash in prev.items():
                if old_id in served:
                    continue
                p = d / f"{old_id}.yaml"
                if p.exists() and hashlib.sha256(
                        p.read_text(encoding="utf-8").encode()).hexdigest() == old_hash:
                    p.unlink(missing_ok=True); removed.append(old_id); changed = True
        self._cache_write("ota_content.json", served)

        if changed:
            _frag.reload()                               # pulled/removed fragments take effect now
        return {"installed": installed, "skipped": skipped, "removed": removed}

    def upload_fragment(self, yaml_text: str) -> dict:
        """Push a blessed fragment (its YAML — nothing else) to the Teaching Center, which validates
        it against its own vocabulary (the version gate) and registers it for composing experiments."""
        status, obj = self._transport("POST", "/api/fragments", {"yaml": yaml_text})
        if status == 0:
            return {"ok": False, "error": "Can't reach the course server."}
        if status == 401:
            return {"ok": False, "error": "Teacher sign-in required to upload."}
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def set_photo(self, data_url: str) -> dict:
        """Upload (or clear) my profile photo — a small data-URL. Stored on the course server so the
        instructor sees a real face next to a name."""
        status, obj = self._transport("POST", f"/courses/{self.course}/photo",
                                      {"photo": data_url})
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def delete_message(self, message_id: str, deleted: bool = True) -> dict:
        status, obj = self._transport("POST", f"/courses/{self.course}/messages/delete",
                                      {"message_id": message_id, "deleted": deleted})
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def set_ai_proxy(self, on: bool, blurb: str = "") -> dict:
        """May an AI answer on my behalf when I'm away? Off unless the teacher granted hosting AND
        I say yes — either alone is not consent."""
        status, obj = self._transport("POST", f"/courses/{self.course}/ai/pref",
                                      {"on": bool(on), "blurb": blurb})
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def session_expired(self) -> bool:
        """The server answered, but rejected us. Distinguishing this from an outage matters: an
        expired session must say "sign in again", not "the server is down" — sending a student to
        debug the network when they just need to re-enter a password is a small betrayal."""
        status, _ = self._transport("GET", f"/courses/{self.course}/manifest", None)
        return status in (401, 403)

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
        """Released and not past-due, newest first (what Missions mode should offer). Drafts are
        filtered here too (defence in depth — the server already excludes them from the manifest)."""
        now = time.time()
        out = []
        for m in self.manifest():
            if m.get("status", "released") == "draft":
                continue
            rel = _epoch(m.get("release"))
            due = _epoch(m.get("due"))
            if (rel is None or rel <= now) and (due is None or due >= now):
                out.append(m)
        return sorted(out, key=lambda m: _epoch(m.get("release")) or 0, reverse=True)

    # -- teacher: draft experiments (playtest + approve, Build 5) ------------- #
    def list_lessons(self) -> list[dict]:
        """All experiments incl. drafts, with status — teacher view (uses the console API, so needs a
        teacher session)."""
        status, obj = self._transport("GET", "/api/lessons", None)
        return obj if status == 200 and isinstance(obj, list) else []

    def mark_playtested(self, lesson_id: str) -> dict:
        """Record that the teacher playtested this experiment on the canvas — the gate that lets it
        be approved (assigned experiments are part of the students' grading loop)."""
        status, obj = self._transport("POST", "/api/lessons/playtest", {"id": lesson_id})
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

    def approve_lesson(self, lesson_id: str, *, release: str = "", due: str = "") -> dict:
        """Release a draft experiment to the class — the teacher's sign-off after playtesting."""
        status, obj = self._transport("POST", "/api/lessons/approve",
                                      {"id": lesson_id, "release": release, "due": due})
        return obj if isinstance(obj, dict) else {"ok": False, "error": f"Server said {status}."}

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

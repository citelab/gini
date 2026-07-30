"""Build 5 — the experiment approval gate. Compose → DRAFT → teacher playtests → APPROVE → release.

The load-bearing property: a draft experiment is NEVER visible to students. Only after the teacher
approves it (their sign-off, after playtesting the whole composition) does it reach the class.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
sys.path.insert(0, str(_TC))

import teacher as T                                       # noqa: E402
from store import Store                                   # noqa: E402


# -- the Course gate (direct, fast) ------------------------------------------ #
@pytest.fixture()
def course(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "courses" / "c1").mkdir(parents=True)
    return T.Course(tmp_path, "c1")


def _lesson_spec():
    return {"id": "exp-lan", "fragments": ["basic-lan"], "title": "Build a switched LAN"}


def test_save_creates_a_draft_not_a_release(course):
    r = course.save_lesson(_lesson_spec())
    assert r["ok"] and r["status"] == "draft"
    # the STUDENT-facing manifest excludes it…
    assert course.released_manifest() == []
    # …but the teacher sees it, marked draft
    lessons = {le["id"]: le for le in course.lessons()}
    assert lessons["exp-lan"]["status"] == "draft"


def test_approve_releases_it_to_students(course):
    course.save_lesson(_lesson_spec())
    assert course.released_manifest() == []          # invisible while draft
    # the grading-loop gate: can't approve until the teacher playtests it on the canvas
    assert not course.approve_lesson("exp-lan")["ok"]
    course.mark_playtested("exp-lan")
    r = course.approve_lesson("exp-lan")
    assert r["ok"] and r["status"] == "released"
    released = course.released_manifest()
    assert [x["id"] for x in released] == ["exp-lan"]   # now the class sees it


def test_editing_the_pack_revokes_a_prior_playtest(course):
    """A re-save that CHANGES the pack must be re-playtested — an old sign-off can't cover new
    content that students will be graded on."""
    course.save_lesson(_lesson_spec())
    course.mark_playtested("exp-lan")
    assert course.approve_lesson("exp-lan")["ok"]        # playtested → releases
    changed = {**_lesson_spec(), "title": "Build a switched LAN (v2)"}
    course.save_lesson(changed)                          # content changed → playtest revoked
    assert not course.approve_lesson("exp-lan")["ok"]    # …must playtest again


def test_unrelease_pulls_it_back_to_draft(course):
    course.save_lesson(_lesson_spec(), release_now=True)
    assert [x["id"] for x in course.released_manifest()] == ["exp-lan"]
    course.unrelease_lesson("exp-lan")
    assert course.released_manifest() == []          # students stop seeing it


def test_approving_a_missing_experiment_is_refused(course):
    r = course.approve_lesson("nope")
    assert not r["ok"]


# -- end-to-end over HTTP: a student cannot see a draft ---------------------- #
PORT = 8977
BASE = f"http://localhost:{PORT}"


def _req(path, method="GET", body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(r, timeout=8) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture()
def live(tmp_path):
    root = tmp_path / "course"
    (root / "data").mkdir(parents=True)
    (root / "courses" / "c1").mkdir(parents=True)
    (root / "courses" / "c1" / "manifest.json").write_text("[]")
    Store(root).upsert_enrolment("ravi", name="Ravi", sis_id="", token="T-ravi", group="",
                                 ai_hosted=False)
    env = {**os.environ, "COURSE_ROOT": str(root), "PORT": str(PORT), "COURSE": "c1",
           "TEACHER_ID": "prof", "TEACHER_PASSWORD": "teachteach", "AI_URL": "http://127.0.0.1:9",
           "GINI_HOME_DIR": str(root)}
    p = subprocess.Popen([sys.executable, str(_TC / "server.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/auth/whoami", timeout=1); break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.1)
    yield root
    p.terminate(); p.wait(timeout=5)


def test_a_student_never_sees_a_draft_experiment_until_approved(live):
    prof = _req("/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]["session"]
    ravi = _req("/auth/claim", "POST",
                {"id": "ravi", "enrolment_token": "T-ravi", "password": "password123"})[1]["session"]

    # teacher saves a draft
    assert _req("/api/lessons", "POST", {"spec": _lesson_spec()}, token=prof)[1]["status"] == "draft"

    # the student's manifest is empty — the draft is invisible
    man = _req("/courses/c1/manifest", token=ravi)[1]
    assert man == []

    # …the teacher can still see it as a draft
    lessons = _req("/api/lessons", token=prof)[1]
    assert any(le["id"] == "exp-lan" and le["status"] == "draft" for le in lessons)

    # approve is gated on a playtest — refused until the teacher confirms it on the canvas
    assert not _req("/api/lessons/approve", "POST", {"id": "exp-lan"}, token=prof)[1]["ok"]
    assert _req("/api/lessons/playtest", "POST", {"id": "exp-lan"}, token=prof)[1]["ok"]
    # teacher approves → the student now sees it
    assert _req("/api/lessons/approve", "POST", {"id": "exp-lan"}, token=prof)[1]["ok"]
    man2 = _req("/courses/c1/manifest", token=ravi)[1]
    assert [m["id"] for m in man2] == ["exp-lan"]

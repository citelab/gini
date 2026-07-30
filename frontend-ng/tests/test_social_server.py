"""Phases B–E over real HTTP. The unit tests prove the logic; these prove the SERVER enforces it.

The one that matters most: a TEACHER session, hitting the real endpoint, cannot see a student↔student
DM. If that ever changes, the product changed — not a detail.
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

PORT = 8947
BASE = f"http://localhost:{PORT}"


def _req(path, method="GET", body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
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
    import sys as _sys
    _sys.path.insert(0, str(_TC))
    from store import Store
    st = Store(root)
    for sid, tok, grp in (("ana", "T-ana", "g1"), ("ben", "T-ben", "g1"), ("cara", "T-cara", "g2")):
        st.upsert_enrolment(sid, name=sid.capitalize(), sis_id="", token=tok, group=grp,
                            ai_hosted=False)
    env = {**os.environ, "COURSE_ROOT": str(root), "PORT": str(PORT), "COURSE": "c1",
           "TEACHER_ID": "prof", "TEACHER_PASSWORD": "teachteach",
           "AI_URL": "http://127.0.0.1:9",              # deliberately dead: no model in CI
           }
    p = subprocess.Popen([sys.executable, str(_TC / "server.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/auth/whoami", timeout=1)
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.1)
    sess = {}
    for sid, tok in (("ana", "T-ana"), ("ben", "T-ben"), ("cara", "T-cara")):
        r = _req("/auth/claim", "POST", {"id": sid, "enrolment_token": tok,
                                         "password": "password123"})[1]
        sess[sid] = r["session"]
    sess["prof"] = _req("/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]["session"]
    yield sess
    p.terminate()
    p.wait(timeout=5)


def test_group_view_over_http(live):
    _req("/courses/c1/presence", "POST",
         {"progress": {"lesson_id": "lab01", "title": "Build a LAN", "level": 3, "met": 5,
                       "total": 7}}, token=live["ana"])
    status, g = _req("/courses/c1/group", token=live["ben"])
    assert status == 200 and g["group"] == "g1"
    ana = next(m for m in g["members"] if m["id"] == "ana")
    assert ana["online"] and ana["progress"]["level"] == 3


def test_the_teacher_endpoint_cannot_surface_a_private_dm(live):
    """THE invariant. Enforced in the server, so no console change can quietly widen it."""
    _req("/courses/c1/messages", "POST", {"to": "ben", "body": "PRIVATE-GRIPE"}, token=live["ana"])
    _req("/courses/c1/messages", "POST", {"to": "group", "body": "GROUP-TALK"}, token=live["ana"])
    _req("/courses/c1/messages", "POST", {"to": "teacher", "body": "ASK-PROF"}, token=live["ana"])

    _, staff = _req("/courses/c1/messages", token=live["prof"])
    bodies = [m["body"] for m in staff]
    assert "GROUP-TALK" in bodies and "ASK-PROF" in bodies
    assert "PRIVATE-GRIPE" not in bodies                      # the blind spot holds

    _, mine = _req("/courses/c1/messages", token=live["ben"])
    assert "PRIVATE-GRIPE" in [m["body"] for m in mine]       # …but Ben, who it was sent to, sees it


def test_you_cannot_dm_outside_your_group_over_http(live):
    _, r = _req("/courses/c1/messages", "POST", {"to": "cara", "body": "hi"}, token=live["ana"])
    assert r["ok"] is False and "own group" in r["error"]


def test_a_dangerous_question_is_refused_by_ProfAI_with_no_model_at_all(live):
    """The model URL is dead in this fixture. A deadline question must STILL be refused correctly —
    proving the guardrail is a mechanism, not a prompt."""
    _, r = _req("/courses/c1/messages", "POST",
                {"to": "teacher", "body": "when is the lab due?"}, token=live["ana"])
    assert r["ok"]
    assert r["ai"]["from"] == "ProfAI"                        # labelled, always
    assert "can't answer questions about deadlines" in r["ai"]["body"]
    assert r["ai"]["kind"] == "ai"

    # …and it's in the teacher's review queue, escalated
    _, q = _req("/api/review", token=live["prof"])
    assert q and q[0]["escalate"] is True and q[0]["student"] == "ana"


def test_a_safe_question_with_a_DEAD_model_says_so_instead_of_guessing(live):
    _, r = _req("/courses/c1/messages", "POST",
                {"to": "teacher", "body": "what is ARP?"}, token=live["ana"])
    assert r["ai"]["from"] == "ProfAI"
    assert "rather say nothing than guess" in r["ai"]["body"]


def test_a_present_teacher_gets_the_message_and_ProfAI_stays_quiet(live):
    _req("/courses/c1/presence", "POST", {}, token=live["prof"])     # the professor is at their desk
    _, r = _req("/courses/c1/messages", "POST",
                {"to": "teacher", "body": "what is ARP?"}, token=live["ana"])
    assert r["ok"] and r.get("ai") is None
    assert "instructor will see this" in r["note"]


def test_the_correction_loop_posts_as_Prof_and_can_promote_a_standing_answer(live):
    _req("/courses/c1/messages", "POST", {"to": "teacher", "body": "what is a VPC?"},
         token=live["ana"])
    _, q = _req("/api/review", token=live["prof"])
    mid = q[0]["message_id"]

    _, r = _req("/api/review/correct", "POST",
                {"student": "ana", "message_id": mid, "question": "what is a VPC",
                 "correction": "Your own private slice of the cloud network. See week 6.",
                 "promote": True}, token=live["prof"])
    assert r["ok"] and r["message"]["from"] == "Prof" and r["message"]["kind"] == "human"

    _, mine = _req("/courses/c1/messages", token=live["ana"])
    got = [m for m in mine if m["from"] == "Prof"]
    assert got and "week 6" in got[0]["body"]                 # the student gets the REAL answer

    _, persona = _req("/api/persona", token=live["prof"])
    assert any(s["q"] == "what is a VPC" for s in persona["standing_answers"])
    assert persona["version"] > 1                             # every edit is a new version


def test_reporting_shows_staff_a_private_dm_only_because_a_student_chose_to(live):
    _, r = _req("/courses/c1/messages", "POST", {"to": "ana", "body": "NASTY"}, token=live["ben"])
    mid = r["message"]["id"]
    assert _req("/api/reports", token=live["prof"])[1] == []          # staff see nothing…

    _req("/courses/c1/messages/report", "POST", {"message_id": mid, "note": "not ok"},
         token=live["ana"])
    _, reports = _req("/api/reports", token=live["prof"])
    assert len(reports) == 1 and reports[0]["message"]["body"] == "NASTY"   # …until Ana shows them


def test_hosted_student_ai_requires_the_teachers_grant(live):
    _, r = _req("/courses/c1/ai/pref", "POST", {"on": True}, token=live["ben"])
    assert r["ok"] is False and "hasn't enabled" in r["error"]

    _req("/api/roster/ai", "POST", {"id": "ben", "on": True}, token=live["prof"])
    _, r = _req("/courses/c1/ai/pref", "POST", {"on": True}, token=live["ben"])
    assert r["ok"] is True                                    # granted + consented

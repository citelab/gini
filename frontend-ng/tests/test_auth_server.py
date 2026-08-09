"""Phase A end-to-end: the real HTTP server, with real sessions.

The unit tests prove the account logic. These prove the SERVER enforces it — which is where the holes
actually were. Every test is an attack that used to succeed.
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


def _req(url, method="GET", body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture()
def server(tmp_path):
    root = tmp_path / "course"
    (root / "data").mkdir(parents=True)
    (root / "courses" / "c1").mkdir(parents=True)
    (root / "courses" / "c1" / "manifest.json").write_text(json.dumps(
        [{"id": "lab01", "title": "Build a LAN", "release": "", "due": "", "attempts": 3}]))
    from store import Store
    st = Store(root)
    st.upsert_enrolment("mahesh", name="Mahesh", sis_id="", token="TOK-mahesh", group="",
                        ai_hosted=False)
    st.upsert_enrolment("ana", name="Ana", sis_id="", token="TOK-ana", group="", ai_hosted=False)
    port = 8931
    env = {**os.environ, "COURSE_ROOT": str(root), "PORT": str(port), "COURSE": "c1",
           "TEACHER_ID": "prof", "TEACHER_PASSWORD": "teachteach"}
    p = subprocess.Popen([sys.executable, str(_TC / "server.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://localhost:{port}"
    for _ in range(50):                                    # wait for the socket
        try:
            urllib.request.urlopen(base + "/auth/whoami", timeout=1)
            break
        except urllib.error.HTTPError:
            break                                          # 401 = it's up
        except Exception:
            time.sleep(0.1)
    yield base
    p.terminate()
    p.wait(timeout=5)


def _claim(base, sid, tok, pw):
    return _req(base + "/auth/claim", "POST",
                {"id": sid, "enrolment_token": tok, "password": pw})[1]


def test_nothing_is_readable_without_a_session(server):
    assert _req(server + "/courses/c1/manifest")[0] == 401
    assert _req(server + "/students/mahesh/profile")[0] == 401
    assert _req(server + "/courses/c1/manifest", token="literally-anything")[0] == 401


def test_the_teacher_api_no_longer_hands_the_class_roster_to_strangers(server):
    """This was the master key: /api/roster serves every student's ENROLMENT TOKEN, and it was open."""
    assert _req(server + "/api/roster")[0] == 401
    assert _req(server + "/api/roster", token="anything")[0] == 401

    student = _claim(server, "mahesh", "TOK-mahesh", "correct horse")["session"]
    assert _req(server + "/api/roster", token=student)[0] == 401      # a STUDENT session is not enough

    prof = _req(server + "/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]
    status, rows = _req(server + "/api/roster", token=prof["session"])
    assert status == 200 and any(r["id"] == "mahesh" for r in rows)   # …the teacher's is


def test_a_signed_in_student_can_work(server):
    s = _claim(server, "mahesh", "TOK-mahesh", "correct horse")["session"]
    status, man = _req(server + "/courses/c1/manifest", token=s)
    assert status == 200 and man[0]["id"] == "lab01"
    assert _req(server + "/students/mahesh/profile", token=s)[0] == 200


def test_one_student_cannot_read_or_write_another_students_profile(server):
    """Authentication without authorization is just a nicer-looking hole."""
    mahesh = _claim(server, "mahesh", "TOK-mahesh", "correct horse")["session"]
    _claim(server, "ana", "TOK-ana", "battery staple")

    assert _req(server + "/students/ana/profile", token=mahesh)[0] == 403
    assert _req(server + "/students/ana/profile", "PUT",
                {"student_id": "ana", "lessons": {}}, token=mahesh)[0] == 403


def test_you_cannot_submit_a_result_under_someone_elses_name(server):
    """A submission is a claim ABOUT A PERSON, so the server decides who that person is."""
    mahesh = _claim(server, "mahesh", "TOK-mahesh", "correct horse")["session"]
    status, _ = _req(server + "/courses/c1/submissions", "POST",
                     {"student": "ana", "lesson_id": "lab01", "band": "gold"}, token=mahesh)
    assert status == 201

    # read it back through the teacher API (which re-grades from the stored submissions)
    prof = _req(server + "/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]
    status, prog = _req(server + "/api/progress", token=prof["session"])
    assert status == 200
    who = {r["student"] for r in prog["rows"] if r["completed"] or any(c["band"] for c in r["cells"])}
    assert "ana" not in who            # the forged name was overwritten…
    assert "mahesh" in who             # …with the caller's real identity


def test_the_wrong_password_does_not_get_you_in(server):
    _claim(server, "mahesh", "TOK-mahesh", "correct horse")
    r = _req(server + "/auth/login", "POST", {"id": "mahesh", "password": "wrong"})[1]
    assert not r["ok"]
    assert _req(server + "/courses/c1/manifest", token=r.get("session", ""))[0] == 401

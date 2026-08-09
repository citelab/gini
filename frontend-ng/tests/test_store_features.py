"""SQLite foundation + the new-look features it enables: durability, photos, soft-delete, live roster.

These target the store directly (fast, no HTTP) plus a couple of end-to-end server checks for the
new endpoints. The point of the migration was reliability, so the first test is the one that used to
be impossible with flat files: concurrent writes don't lose data.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
sys.path.insert(0, str(_TC))

from store import Store                                   # noqa: E402
import social as S                                        # noqa: E402
import teacher as T                                       # noqa: E402


# -- the reason we migrated -------------------------------------------------- #
def test_concurrent_writes_do_not_lose_data(tmp_path):
    """The flat-file store corrupts when two threads write at once. SQLite serializes them. 20
    threads each append a submission; all 20 must survive."""
    st = Store(tmp_path)

    def add(i):
        st.add_submission({"student": f"s{i}", "lesson_id": "lab01", "band": "gold"})

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(st.submissions()) == 20                   # nothing torn, nothing lost


def test_one_store_per_root_so_writers_see_each_others_writes(tmp_path):
    a = Store(tmp_path)
    b = Store(tmp_path)
    assert a is b                                         # same DB file → same connection
    a.upsert_enrolment("ravi", name="Ravi", sis_id="2511", token="t", group="", ai_hosted=False)
    assert any(r["id"] == "ravi" for r in b.roster())


def test_data_survives_a_reopen(tmp_path):
    """Durability: write, drop the process's handle, reopen from disk — it's still there."""
    st = Store(tmp_path)
    st.upsert_enrolment("ravi", name="Ravi", sis_id="2511", token="t", group="", ai_hosted=False)
    st.put_profile("ravi", {"student_id": "ravi", "lessons": {"lab01": {"completed": True}}})
    Store._instances.clear()                              # force a fresh connection next call
    again = Store(tmp_path)
    assert any(r["id"] == "ravi" for r in again.roster())
    assert again.profile("ravi")["lessons"]["lab01"]["completed"]


# -- soft delete (the Gmail trash) ------------------------------------------- #
def test_messages_go_to_trash_not_the_void(tmp_path):
    c = T.Course(tmp_path, "c1")
    c.enrol("ana", name="Ana", group="g1")
    c.enrol("ben", name="Ben", group="g1")
    soc = S.Social(tmp_path, c)
    m = soc.send("ana", "ben", "hi")["message"]

    assert m["id"] in [x["id"] for x in soc.inbox("ana")]
    soc.set_deleted("ana", "student", m["id"], True)
    assert m["id"] not in [x["id"] for x in soc.inbox("ana")]                     # hidden…
    assert m["id"] in [x["id"] for x in soc.inbox("ana", include_deleted=True)]   # …not destroyed
    soc.set_deleted("ana", "student", m["id"], False)                            # restore
    assert m["id"] in [x["id"] for x in soc.inbox("ana")]


def test_you_cannot_delete_a_message_you_cannot_see(tmp_path):
    c = T.Course(tmp_path, "c1")
    c.enrol("ana", name="Ana", group="g1")
    c.enrol("ben", name="Ben", group="g1")
    c.enrol("cara", name="Cara", group="g2")
    soc = S.Social(tmp_path, c)
    m = soc.send("ana", "ben", "private")["message"]
    r = soc.set_deleted("cara", "student", m["id"], True)      # cara isn't in this DM
    assert not r["ok"]
    assert m["id"] in [x["id"] for x in soc.inbox("ana")]      # untouched


# -- photos ------------------------------------------------------------------ #
def test_a_photo_belongs_to_a_claimed_account(tmp_path):
    import accounts as A
    Store(tmp_path).upsert_enrolment("ravi", name="Ravi", sis_id="", token="TOK", group="",
                                     ai_hosted=False)
    accts = A.Accounts(tmp_path)

    # not claimed yet → no photo to set (a photo is tied to an identity)
    assert not accts.set_photo("ravi", "data:image/png;base64,AAAA")["ok"]
    accts.claim("ravi", "TOK", "password123")
    assert accts.set_photo("ravi", "data:image/png;base64,AAAA")["ok"]
    assert accts.photo("ravi") == "data:image/png;base64,AAAA"


def test_oversized_photos_are_refused(tmp_path):
    import accounts as A
    Store(tmp_path).upsert_enrolment("ravi", name="Ravi", sis_id="", token="TOK", group="",
                                     ai_hosted=False)
    accts = A.Accounts(tmp_path)
    accts.claim("ravi", "TOK", "password123")
    assert not accts.set_photo("ravi", "x" * 500_000)["ok"]    # keep the DB lean


# -- live roster over HTTP --------------------------------------------------- #
PORT = 8961
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
    st = Store(root)
    st.upsert_enrolment("ravi", name="Ravi Kumar", sis_id="2511", token="T-ravi", group="g1",
                        ai_hosted=False)
    env = {**os.environ, "COURSE_ROOT": str(root), "PORT": str(PORT), "COURSE": "c1",
           "TEACHER_ID": "prof", "TEACHER_PASSWORD": "teachteach", "AI_URL": "http://127.0.0.1:9"}
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
    yield root
    p.terminate()
    p.wait(timeout=5)


def test_the_roster_api_shows_status_and_photo(live):
    prof = _req("/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]["session"]
    ravi = _req("/auth/claim", "POST",
                {"id": "ravi", "enrolment_token": "T-ravi", "password": "password123"})[1]["session"]

    # ravi signs in and uploads a photo + a heartbeat
    _req("/courses/c1/photo", "POST", {"photo": "data:image/png;base64,ZZZZ"}, token=ravi)
    _req("/courses/c1/presence", "POST", {}, token=ravi)

    status, rows = _req("/api/roster", token=prof)
    assert status == 200
    row = next(r for r in rows if r["id"] == "ravi")
    assert row["online"] is True                          # the teacher sees who's on right now
    assert row["photo"] == "data:image/png;base64,ZZZZ"   # …and a real face
    assert row["claimed"] is True and row["sis_id"] == "2511"

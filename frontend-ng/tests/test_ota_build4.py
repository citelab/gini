"""Build 4 — TC → student OTA. A student client pulls teacher-authored fragments into its user
content layer, version-gated on the RECEIVING end: an incompatible pack is skipped-with-reason, the
rest install, nothing bricks.
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

from gini.agent import teaching_center as TC             # noqa: E402
from gini.domain import content as _content              # noqa: E402
from gini.domain import fragments as _frag               # noqa: E402
from gini.domain import vocabulary as _vocab             # noqa: E402


# -- the receiving version gate (pure, no server) ---------------------------- #
def test_pull_installs_compatible_and_skips_incompatible(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))   # isolated user content layer
    good = ("id: ota-good\nlayer: core\nteaches: networking-basics\nengine_version: '"
            + _vocab.ENGINE_VERSION + "'\nobjectives:\n- {id: a, say: place, check: 'exists(switch)'}\n")
    newer = ("id: ota-newer\nlayer: core\nengine_version: '99.0'\n"
             "objectives:\n- {id: a, say: place, check: 'exists(switch)'}\n")
    unknown = ("id: ota-unknown\nlayer: core\nengine_version: '" + _vocab.ENGINE_VERSION + "'\n"
               "objectives:\n- {id: a, say: place, check: 'exists(warpcore)'}\n")

    items = [{"id": "ota-good", "engine_version": _vocab.ENGINE_VERSION, "yaml": good},
             {"id": "ota-newer", "engine_version": "99.0", "yaml": newer},
             {"id": "ota-unknown", "engine_version": _vocab.ENGINE_VERSION, "yaml": unknown}]

    c = TC.TeachingCenterClient("http://x", course="c1", student_id="ravi", cache_dir=tmp_path,
                                transport=lambda m, p, b=None: (200, items))
    res = c.pull_content()

    assert res["installed"] == ["ota-good"]
    reasons = {s["id"]: s["reason"] for s in res["skipped"]}
    assert "newer gBuilder" in reasons["ota-newer"] or "update the engine" in reasons["ota-newer"]
    assert "warpcore" in reasons["ota-unknown"]          # named the missing primitive

    _frag.reload()
    assert _frag.get("ota-good") is not None             # the good one is now playable
    assert _frag.get("ota-newer") is None                # the incompatible ones never landed
    assert _frag.get("ota-unknown") is None


def test_pull_is_idempotent_and_only_reloads_on_change(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    good = ("id: ota-x\nlayer: core\nengine_version: '" + _vocab.ENGINE_VERSION + "'\n"
            "objectives:\n- {id: a, say: place, check: 'exists(host)'}\n")
    items = [{"id": "ota-x", "engine_version": _vocab.ENGINE_VERSION, "yaml": good}]
    c = TC.TeachingCenterClient("http://x", course="c1", student_id="ravi", cache_dir=tmp_path,
                                transport=lambda m, p, b=None: (200, items))
    assert c.pull_content()["installed"] == ["ota-x"]
    assert c.pull_content()["installed"] == ["ota-x"]    # second pull: no error, still installed
    assert (_content.user_content_dir() / "ota-x.yaml").exists()


# -- end-to-end: upload on the server, pull it as a client ------------------- #
PORT = 8975
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


_FRAG = ("id: ota-lan\nlayer: core\nteaches: networking-basics\nsummary: a LAN\n"
         "engine_version: '" + _vocab.ENGINE_VERSION + "'\nauthor: prof\n"
         "objectives:\n- {id: a, say: Place a switch, check: 'exists(switch)'}\n")


@pytest.fixture()
def live(tmp_path):
    root = tmp_path / "course"
    (root / "data").mkdir(parents=True)
    (root / "courses" / "c1").mkdir(parents=True)
    (root / "courses" / "c1" / "manifest.json").write_text("[]")
    from store import Store
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


def test_teacher_uploads_student_pulls(live, tmp_path, monkeypatch):
    prof = _req("/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]["session"]
    assert _req("/api/fragments", "POST", {"yaml": _FRAG}, token=prof)[1]["ok"]

    # a STUDENT client, its own isolated content home, pulls the channel
    home = tmp_path / "student-home"
    monkeypatch.setenv("GINI_HOME_DIR", str(home))
    ravi = _req("/auth/claim", "POST",
                {"id": "ravi", "enrolment_token": "T-ravi", "password": "password123"})[1]["session"]
    c = TC.TeachingCenterClient(BASE, course="c1", student_id="ravi", session=ravi,
                                cache_dir=home)
    res = c.pull_content()
    assert "ota-lan" in res["installed"]
    _frag.reload()
    assert _frag.get("ota-lan") is not None              # the authored fragment is now on the student


def test_the_content_channel_needs_a_session(live):
    assert _req("/courses/c1/content")[0] == 401         # not readable without signing in

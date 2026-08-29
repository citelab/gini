"""Build 3 — author → Teaching Center. Upload a blessed fragment; the TC validates it against its own
vocabulary (the version gate) and composes experiments from it.

The version gate is the validation itself: a primitive the server doesn't have (authored on a newer
engine) fails with the exact missing thing — an honest refusal, not a silent mis-compose.
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
from gini.domain import vocabulary as V                  # noqa: E402


# -- vocabulary / version gate (pure) ---------------------------------------- #
def test_vocabulary_exports_the_asset_manifest():
    v = V.export()
    assert v["engine_version"]
    assert any(e["key"] == "host" for e in v["elements"])
    assert {"exists", "link", "contains_type"} <= set(v["predicates"])
    assert "reach" in v["probes"]
    assert "switched-segment" in v["capabilities"]


def test_the_version_gate_refuses_newer_content_and_allows_older_or_equal():
    assert V.is_compatible("")[0]                         # a built-in
    assert V.is_compatible(V.ENGINE_VERSION)[0]           # same engine
    ok, why = V.is_compatible("99.0")
    assert not ok and "update the engine" in why          # newer → refuse with a reason


# -- the client upload method ------------------------------------------------ #
def test_upload_fragment_client_posts_the_yaml(tmp_path):
    sent = {}

    def fake(method, path, body=None):
        sent["path"] = path; sent["body"] = body
        return 200, {"ok": True, "id": "authored-lan"}
    c = TC.TeachingCenterClient("http://localhost:8080", course="c1", student_id="prof",
                                cache_dir=tmp_path, transport=fake)
    res = c.upload_fragment("id: authored-lan\nlayer: core\n")
    assert res["ok"] and sent["path"] == "/api/fragments"
    assert "authored-lan" in sent["body"]["yaml"]


def test_upload_needs_a_teacher_session(tmp_path):
    c = TC.TeachingCenterClient("http://localhost:8080", course="c1", student_id="ravi",
                                cache_dir=tmp_path, transport=lambda *a, **k: (401, None))
    assert not c.upload_fragment("id: x")["ok"]


# -- end-to-end against the real server -------------------------------------- #
PORT = 8973
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


_FRAG = """id: authored-lan
layer: core
teaches: networking-basics
summary: a switched LAN
engine_version: '6.0'
author: prof
objectives:
- id: c-switch
  say: Place a switch
  check: exists(switch)
- id: c-wire
  say: Wire host to switch
  check: link(host, switch)
"""


@pytest.fixture()
def live(tmp_path):
    # TEACHING_CENTER_V1_SPEC.md §3.1, the open decision. v1 removed lesson authoring from the
    # server, so there is no endpoint left to upload a fragment TO. The version gate and the client
    # shape are pure and still run above; only the round trip through a live TC is retired.
    #
    # Skipped rather than deleted, for the same reason as test_ota_build4: if Missions must keep
    # syncing from the Teaching Center, this path comes back.
    pytest.skip("fragment upload retired by Teaching Center v1 — see spec §3.1 (decision pending)")
    root = tmp_path / "course"
    (root / "data").mkdir(parents=True)
    (root / "courses" / "c1").mkdir(parents=True)
    (root / "courses" / "c1" / "manifest.json").write_text("[]")
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


def test_a_teacher_uploads_a_fragment_and_the_TC_composes_from_it(live):
    prof = _req("/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]["session"]

    ok = _req("/api/fragments", "POST", {"yaml": _FRAG}, token=prof)[1]
    assert ok["ok"] and ok["id"] == "authored-lan"

    lib = _req("/api/fragments", token=prof)[1]           # now in the library
    assert any(f["id"] == "authored-lan" for f in lib)

    # …and an experiment composes from it
    preview = _req("/api/preview", "POST",
                   {"spec": {"id": "exp", "fragments": ["authored-lan"], "title": "Exp"}},
                   token=prof)[1]
    assert preview["ok"]


def test_a_fragment_using_an_unknown_primitive_is_refused_with_a_reason(live):
    prof = _req("/auth/login", "POST", {"id": "prof", "password": "teachteach"})[1]["session"]
    bad = "id: bad\nlayer: core\nobjectives:\n- {id: x, say: p, check: 'exists(warpcore)'}\n"
    res = _req("/api/fragments", "POST", {"yaml": bad}, token=prof)[1]
    assert not res["ok"] and "warpcore" in res["error"]   # names the missing primitive


def test_a_non_teacher_cannot_upload(live):
    assert _req("/api/fragments", "POST", {"yaml": _FRAG})[0] == 401
    assert _req("/api/fragments", "POST", {"yaml": _FRAG}, token="junk")[0] == 401

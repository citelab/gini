"""The Teaching Center client (GLP): lesson pull + hash verify + offline cache, git-style
profile checkout/checkin with monotonic merge, and offline submission queue + flush — all against
an injectable fake transport (no network)."""
import hashlib
import time

from gini.agent.teaching_center import TeachingCenterClient
from gini.agent.mission import Mission
from gini.domain import lesson as L, objectives as O, probes as P, profile as PR
from gini.domain.topology import Topology

_PACK = """
id: lab03
title: Private DB
time_limit: 25m
intent: {concept: vpc-networking, spirit: any mechanism}
objectives:
  - {id: in, say: in vpc, kind: structural, check: "contains(VPC1, DB1)"}
complete_when: all
"""
_PACK_HASH = hashlib.sha256(_PACK.encode()).hexdigest()


class FakeServer:
    """An in-memory GLP server: records requests and serves a manifest, a pack, a profile, and
    accepts submissions/profile PUTs. `online` toggles connectivity."""
    def __init__(self, online=True):
        self.online = online
        self.profiles = {}
        self.submissions = []
        self.manifest = [{"id": "lab03", "title": "Private DB", "release": "2020-01-01",
                          "due": "2999-01-01", "pack_hash": _PACK_HASH}]

    def __call__(self, method, path, body=None):
        if not self.online:
            return 0, None
        if path.endswith("/manifest"):
            return 200, self.manifest
        if path.endswith("/pack"):
            return 200, _PACK
        if path.endswith("/profile") and method == "GET":
            sid = path.split("/")[2]
            return 200, self.profiles.get(sid, {"student_id": sid, "lessons": {}})
        if path.endswith("/profile") and method == "PUT":
            self.profiles[path.split("/")[2]] = body
            return 204, None
        if path.endswith("/submissions"):
            self.submissions.append(body)
            return 201, {"ok": True}
        return 404, None


def _client(server, tmp_path, **o):
    return TeachingCenterClient("http://x", course="cs1", student_id="stu1",
                                token="t", cache_dir=tmp_path, transport=server, **o)


def test_manifest_and_available_lessons(tmp_path):
    c = _client(FakeServer(), tmp_path)
    assert c.online()
    avail = c.available_lessons()
    assert [m["id"] for m in avail] == ["lab03"]


def test_manifest_falls_back_to_cache_offline(tmp_path):
    server = FakeServer()
    c = _client(server, tmp_path)
    c.manifest()                                  # populates the cache
    server.online = False
    assert c.manifest()[0]["id"] == "lab03"       # served from cache while offline


def test_fetch_lesson_verifies_hash(tmp_path):
    c = _client(FakeServer(), tmp_path)
    les = c.fetch_lesson("lab03", expected_hash=_PACK_HASH)
    assert isinstance(les, L.Lesson) and les.id == "lab03"
    assert c.fetch_lesson("lab03", expected_hash="deadbeef") is None   # tamper rejected


def test_profile_checkout_merges_server_and_local(tmp_path):
    server = FakeServer()
    server.profiles["stu1"] = {"student_id": "stu1", "lessons": {
        "labX": {"lesson_id": "labX", "concept": "c", "best_band": "pass", "attempts_used": 1,
                 "best_time_s": None, "completed": True, "last_played": 1.0, "snapshot": ""}}}
    # a local working copy with different progress
    local = PR.Profile("stu1")
    local.lessons["labY"] = PR.LessonRecord("labY", "d", best_band="gold", completed=True)
    local.save(tmp_path / "profile_stu1.json")

    merged = _client(server, tmp_path).checkout_profile()
    assert "labX" in merged.lessons and "labY" in merged.lessons     # union of both


def test_checkin_pushes_profile(tmp_path):
    server = FakeServer()
    c = _client(server, tmp_path)
    prof = PR.Profile("stu1")
    prof.lessons["lab03"] = PR.LessonRecord("lab03", "vpc-networking", best_band="gold", completed=True)
    assert c.checkin_profile(prof)
    assert server.profiles["stu1"]["lessons"]["lab03"]["best_band"] == "gold"


def test_submission_queues_offline_then_flushes(tmp_path):
    server = FakeServer(online=False)
    c = _client(server, tmp_path)
    les = L.from_archetype("reachability-boundary",
                           {"inside": "WEB1", "protected": "DB1", "outsider": "NET", "box": "VPC1"},
                           id="lab03", time_limit="25m")
    t = Topology(); v = t.add_device("vpc", "VPC1")
    t.add_device("web_app", "WEB1", parent_id=v.id); t.add_device("database", "DB1", parent_id=v.id)
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    m = Mission(les, now=lambda: 0.0); m.start(); m.check(O.TopologyWorld(t), runner)

    assert c.submit("lab03", m) is False          # offline → queued
    server.online = True
    assert c.flush() == 1                          # reconnect → flushed
    assert server.submissions[0]["band"] == "gold"
    assert c.flush() == 0                           # nothing left

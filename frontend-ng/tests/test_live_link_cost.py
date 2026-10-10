"""A real Run: changing a link's cost in gBuilder moves a running lab's traffic.

Everything else about link costs is tested piece by piece; this is the whole thing, through the
same code paths a teacher uses: MainWindow._run brings the lab up in Docker via the orchestrator,
the cost is changed through the API the inspector calls, gBuilder pushes it into the running
routers, and a real `ping` from inside machine H1 shows which way the traffic went (TTL 61 through
R3, 62 straight across).

            R1 ---- cost 10 ---- R2 -- H2
   H1 -- R1   \\                /
               cost 2      cost 3
                  \\        /
                     R3

  static:  routes compiled from the costs take the detour; dropping the direct link to cost 1
           makes gBuilder recompute and push the routes, and the ping goes straight across.
  dynamic: rip_reference.lua (from ~/.gini/scripts, as a student loads it) converges on the
           detour at cost 5; dropping the direct link to 1 reaches both routers and RIP moves.

OPT-IN, because it needs Docker and takes a minute or two: GINI_LIVE_E2E=1, plus
GINI_GROUTER_IMAGE naming a router image built from this tree (a released image predates link
costs). It writes to ~/.gini like a real Run does.

  GINI_LIVE_E2E=1 GINI_GROUTER_IMAGE=gini-grouter:dev pytest tests/test_live_link_cost.py
"""
import os
import re
import shutil
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.skipif(
    os.environ.get("GINI_LIVE_E2E") != "1" or not os.environ.get("GINI_GROUTER_IMAGE"),
    reason="live Docker test: set GINI_LIVE_E2E=1 and GINI_GROUTER_IMAGE")


def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture()
def lab(monkeypatch):
    if not _docker_up():
        pytest.skip("Docker is not running")
    from PySide6.QtWidgets import QApplication
    from gini.services import orchestrator
    from gini.ui.main_window import MainWindow
    monkeypatch.setattr(orchestrator, "GROUTER_IMAGE", os.environ["GINI_GROUTER_IMAGE"])
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    api = w.api
    r1, r2, r3 = (api.add_device("router", name=n)["id"] for n in ("R1", "R2", "R3"))
    h1, h2 = api.add_device("host", name="H1")["id"], api.add_device("host", name="H2")["id"]
    direct = api.connect(r1, r2)["id"]
    left, right = api.connect(r1, r3)["id"], api.connect(r3, r2)["id"]
    api.connect(h1, r1)
    api.connect(h2, r2)
    for lid, c in ((direct, 10), (left, 2), (right, 3)):
        api.set_link_property(lid, "cost", c)

    def pump(cond, secs, step=0.5):
        end = time.time() + secs
        while time.time() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(step)
        return False

    def start(mode):
        w.ctx.topology.routing_mode = mode
        cfg = w._gloader.compile(w.ctx.topology)
        h2ip = next(m.ifaces[0].ip.split("/")[0] for m in cfg.machines if m.name == "H2")
        w._run()
        assert pump(lambda: w._running, 600), "the lab did not come up"

        def ttl():
            out = w._machine_shell("H1", f"ping -c 1 -W 2 {h2ip}")
            m = re.search(r"ttl=(\d+)", out)
            return int(m.group(1)) if m else None
        if mode == "static":                          # dynamic has no routes until RIP runs
            assert pump(lambda: ttl() is not None, 60, 1.0), "H1 cannot reach H2 at all"
        return ttl

    yield w, api, direct, pump, start
    if w._running:
        w._stop()
        pump(lambda: not w._running, 180)
    w._poll.stop()                                    # the status poller spawns a thread a tick
    import threading
    pump(lambda: not any(t.name == "MainWindow-bg" for t in threading.enumerate()), 60)


def test_static_routes_follow_the_cost_and_move_when_it_changes(lab):
    w, api, direct, pump, start = lab
    ttl = start("static")
    assert ttl() == 61                                   # the cost-5 detour through R3
    api.set_link_property(direct, "cost", 1)             # what the inspector does
    assert pump(lambda: ttl() == 62, 60, 1.0), "the pushed routes did not move the traffic"
    show = w.element_query("R1", "ifconfig show")
    assert re.search(r"^tun1\t.*\t1$", show, re.M), show  # and R1 knows the new cost


def test_rip_follows_the_cost_and_moves_when_it_changes(lab):
    w, api, direct, pump, start = lab
    ttl = start("dynamic")
    for r in ("R1", "R2", "R3"):
        assert "started" in w.element_query(r, "gpipe cp add lua /scripts/rip_reference.lua 1000")

    def h2_cost(c):
        return f"N 10.0.5.0/24 COST {c} " in w.element_query("R1", "gpipe cp status")
    assert pump(lambda: h2_cost(5), 60, 1.0), w.element_query("R1", "gpipe cp status")
    assert pump(lambda: ttl() == 61, 30, 1.0)
    api.set_link_property(direct, "cost", 1)
    assert pump(lambda: h2_cost(1) and ttl() == 62, 60, 1.0), \
        w.element_query("R1", "gpipe cp status")

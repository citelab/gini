"""A real Run: links fail and recover in gBuilder, and every end of them notices.

Through the code paths a teacher uses -- MainWindow._run in Docker, the API the inspector's Fail
now / Restore calls, gBuilder's own failure clocks -- and checked from inside the containers:

            R1 ---- cost 10 ---- R2 -- S1 -- H2
   H1 -- R1   \\                /
               cost 2      cost 3
                  \\        /
                     R3

  machine:  failing H1-R1 drops carrier on H1's interface (`ip link` shows NO-CARRIER, as on a real
            host whose cable is pulled), takes R1's interface down, and stops the ping; Restore
            brings all of it back
  switch:   failing H2-S1 takes that switch port down and drops H2's carrier
  static:   failing the cheap R1-R3 link black-holes H1 -> H2, because static routes do not react
            -- which is the lesson
  random:   with the cheap link set to fail (mean 2 s) and repair (mean 3 s), the failures and
            repairs happen on their own, and RIP keeps H1 -> H2 working through the direct link
  multicast: mcast_tree.lua with RIP, real Linux sockets on H1 and H2 (H2 behind the switch):
            one copy of every datagram along the detour, straight across from the first datagram
            after the cheap link fails, back after repair, never a duplicate; when the receiver
            quits, the kernel's leave removes the member

OPT-IN like test_live_link_cost.py: GINI_LIVE_E2E=1 and GINI_GROUTER_IMAGE (a router built from
this tree). Writes to ~/.gini like a real Run.
"""
import base64
import os
import re
import shutil
import subprocess
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.skipif(
    os.environ.get("GINI_LIVE_E2E") != "1" or not os.environ.get("GINI_GROUTER_IMAGE"),
    reason="live Docker test: set GINI_LIVE_E2E=1 and GINI_GROUTER_IMAGE")


@pytest.fixture()
def lab(monkeypatch):
    if not shutil.which("docker") or \
            subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker is not running")
    from PySide6.QtWidgets import QApplication
    from gini.services import orchestrator
    from gini.ui.main_window import MainWindow
    monkeypatch.setattr(orchestrator, "GROUTER_IMAGE", os.environ["GINI_GROUTER_IMAGE"])
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    api = w.api
    ids = {n: api.add_device(k, name=n)["id"] for n, k in
           (("R1", "router"), ("R2", "router"), ("R3", "router"), ("S1", "switch"),
            ("H1", "host"), ("H2", "host"))}
    L = {name: api.connect(ids[a], ids[b])["id"] for name, a, b in
         (("direct", "R1", "R2"), ("left", "R1", "R3"), ("right", "R3", "R2"),
          ("h1", "H1", "R1"), ("r2s", "R2", "S1"), ("h2", "H2", "S1"))}
    for name, c in (("direct", 10), ("left", 2), ("right", 3)):
        api.set_link_property(L[name], "cost", c)
    logs = []
    w.ctx.bus.log.connect(lambda lvl, msg: logs.append(msg))

    def pump(cond, secs, step=0.5):
        end = time.time() + secs
        while time.time() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(step)
        app.processEvents()
        return False

    def start(mode):
        w.ctx.topology.routing_mode = mode
        cfg = w._gloader.compile(w.ctx.topology)
        h2ip = next(m.ifaces[0].ip.split("/")[0] for m in cfg.machines if m.name == "H2")
        w._run()
        assert pump(lambda: w._running, 600), "the lab did not come up"

        def ping():
            out = w._machine_shell("H1", f"ping -c 1 -W 2 {h2ip}")
            m = re.search(r"ttl=(\d+)", out)
            return int(m.group(1)) if m else None
        return ping

    def carrier(machine):
        return "NO-CARRIER" not in w._machine_shell(machine, "ip link show gini0")

    yield w, api, L, pump, start, carrier, logs
    if w._running:
        w._stop()
        pump(lambda: not w._running, 180)
    w._poll.stop()
    pump(lambda: not any(t.name == "MainWindow-bg" for t in threading.enumerate()), 60)


def test_a_machine_and_a_switch_lose_their_link(lab):
    w, api, L, pump, start, carrier, _logs = lab
    ping = start("static")
    assert pump(lambda: ping() is not None, 60, 1.0)
    assert carrier("H1") and carrier("H2")

    api.fail_link(L["h1"])
    assert pump(lambda: not carrier("H1"), 20), "H1 kept its carrier"
    assert pump(lambda: re.search(r"^tun\d\tD\t10\.0\.\d+\.1\t", w.element_query(
        "R1", "ifconfig show"), re.M) is not None, 20), w.element_query("R1", "ifconfig show")
    assert ping() is None
    api.restore_link(L["h1"])
    assert pump(lambda: carrier("H1") and ping() is not None, 30, 1.0)

    api.fail_link(L["h2"])
    assert pump(lambda: re.search(rf"{L['h2']}\s+down", w.element_query("S1", "links"))
                is not None, 20), w.element_query("S1", "links")
    assert pump(lambda: not carrier("H2"), 20)
    api.restore_link(L["h2"])
    assert pump(lambda: carrier("H2") and ping() is not None, 30, 1.0)


def test_static_routes_black_hole_through_a_failed_link(lab):
    w, api, L, pump, start, _carrier, _logs = lab
    ping = start("static")
    assert pump(lambda: ping() == 61, 60, 1.0)            # the cheap detour
    api.fail_link(L["left"])
    time.sleep(2)
    assert ping() is None, "static routes are not supposed to route around a failure"
    api.restore_link(L["left"])
    assert pump(lambda: ping() == 61, 30, 1.0)


def test_random_failures_happen_and_rip_routes_around_them(lab):
    w, api, L, pump, start, _carrier, logs = lab
    api.set_link_property(L["left"], "fail_after", 2)
    api.set_link_property(L["left"], "repair_after", 3)
    ping = start("dynamic")
    for r in ("R1", "R2", "R3"):
        assert "started" in w.element_query(r, "gpipe cp add lua /scripts/rip_reference.lua 1000")
    assert pump(lambda: any("FAILED (random" in m for m in logs), 60, 0.5), logs[-10:]
    assert pump(lambda: any("repaired (random" in m for m in logs), 60, 0.5), logs[-10:]
    # Whatever state the cheap link is in, RIP keeps H1 -> H2 reachable: the detour when it is
    # up (61), the direct link when it is down (62). Sample across several flaps.
    seen = set()
    end = time.time() + 25
    while time.time() < end:
        t = ping()
        if t is not None:
            seen.add(t)
        w.ctx.bus.log.emit("info", "")                      # keep the event loop honest
        pump(lambda: False, 1.0, 0.2)
    assert seen <= {61, 62} and 62 in seen, seen


# A real receiver and sender, run inside the machines: Linux's own IGMP, not a stand-in.
_RX = r"""
import socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", 5000))
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
             socket.inet_aton("239.1.1.1") + socket.inet_aton(sys.argv[1]))
s.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_RECVTTL", 12), 1)
while True:
    data, anc, _f, _a = s.recvmsg(2048, 64)
    ttl = next((struct.unpack("i", d[:4])[0] for _l, t, d in anc if t == 2), -1)
    print(data.decode(), ttl, flush=True)
"""
_TX = r"""
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(sys.argv[1]))
for n in range(int(sys.argv[2]), int(sys.argv[3])):
    s.sendto(str(n).encode(), ("239.1.1.1", 5000))
    time.sleep(0.25)
"""


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def test_multicast_follows_rip_through_a_failure(lab):
    w, api, L, pump, start, _carrier, _logs = lab
    start("dynamic")
    cfg = w._gloader.compile(w.ctx.topology)
    ip = {m.name: m.ifaces[0].ip.split("/")[0] for m in cfg.machines}
    for r in ("R1", "R2", "R3"):
        assert "started" in w.element_query(r, "gpipe cp add lua /scripts/rip_reference.lua 1000")
        assert "started" in w.element_query(r, "gpipe cp add lua /scripts/mcast_tree.lua 1000")
    h1net = ip["H1"].rsplit(".", 1)[0] + ".0/24"
    assert pump(lambda: f"N {h1net} COST 5 " in w.element_query("R2", "gpipe cp status"), 60)
    w._machine_shell("H2", f"echo {_b64(_RX)} | base64 -d > /tmp/rx.py; "
                           f"nohup python3 /tmp/rx.py {ip['H2']} > /tmp/rx.log 2>&1 &")
    w._machine_shell("H1", f"echo {_b64(_TX)} | base64 -d > /tmp/tx.py")
    time.sleep(2)

    def burst(a, b):
        """Send datagrams a..b-1 (0.25 s apart; under the 8 s exec limit) -> {n: [ttl, ...]}."""
        w._machine_shell("H1", f"python3 /tmp/tx.py {ip['H1']} {a} {b}")
        time.sleep(1.5)
        got = {}
        for line in w._machine_shell("H2", "cat /tmp/rx.log").splitlines():
            parts = line.split()
            if len(parts) == 2 and a <= int(parts[0]) < b:
                got.setdefault(int(parts[0]), []).append(int(parts[1]))
        return got

    # TTL 8 at H1: 5 after three routers (the detour), 6 after two (straight across)
    got = burst(0, 10)
    assert got == {n: [5] for n in range(10)}, got
    api.fail_link(L["left"])
    pump(lambda: False, 1)
    got = burst(100, 128)
    assert all(len(v) == 1 for v in got.values()), f"duplicates: {got}"
    first = min((n for n, v in got.items() if v == [6]), default=None)
    assert first is not None and first < 112, got                 # within ~3 s of the failure
    assert all(got.get(n) == [6] for n in range(first, 128)), got
    api.restore_link(L["left"])
    pump(lambda: False, 1)
    got = burst(200, 228)
    assert all(len(v) == 1 for v in got.values()), f"duplicates: {got}"
    assert got.get(227) == [5], got
    w._machine_shell("H2", "pkill -f rx.py")                       # the kernel sends a leave
    assert pump(lambda: re.search(r"^G 239\.1\.1\.1 ", w.element_query("R2", "gpipe cp status"),
                                  re.M) is None, 10)

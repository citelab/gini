"""Sources & Sinks on the canvas — auto-attach wiring and the dotted attach edge.

The teacher never chooses "link vs attach": dropping a Source/Sink and dragging it to a Machine
just works, because `ctx.connect` reads the grammar and mounts riders with an ATTACH edge (dotted,
no traffic) while everything else stays a network cable. These are the UI-facing guarantees.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication

from gini.app.context import AppContext
from gini.ui.canvas import CanvasScene, EdgeItem
from gini.ui.theme.manager import get_theme


def _app():
    return QApplication.instance() or QApplication([])


def test_connect_auto_attaches_riders_either_way_round():
    _app()
    ctx = AppContext()
    m = ctx.add_device("host", 10, 10)
    ping = ctx.add_device("ping_probe", 40, 10)
    pcap = ctx.add_device("packet_view", 70, 10)

    l1 = ctx.connect(m.id, ping.id)      # donor first
    l2 = ctx.connect(pcap.id, m.id)      # rider first — order must not matter
    assert l1.kind == "attach" and l1.source_id == ping.id   # rider is always the source end
    assert l2.kind == "attach" and l2.source_id == pcap.id
    assert {r.type_key for r in ctx.topology.riders_on(m.id)} == {"ping_probe", "packet_view"}


def test_a_rider_cannot_be_wired_as_a_network_cable():
    _app()
    ctx = AppContext()
    m = ctx.add_device("host", 10, 10)
    ping = ctx.add_device("ping_probe", 40, 10)
    try:
        ctx.add_link(m.id, ping.id)
        assert False, "a rider must never wire as a network link"
    except ValueError:
        pass
    # two real elements still cable up normally
    sw = ctx.add_device("switch", 90, 10)
    assert ctx.connect(m.id, sw.id).kind == "link"


class _FakeOrch:
    _dc = ["docker", "compose"]
    workdir = None

    def status(self):
        return {"m1": "running"}


def test_run_rider_needs_a_running_topology_first():
    _app()
    ctx = AppContext()
    m = ctx.add_device("host", 10, 10)
    ping = ctx.add_device("ping_probe", 40, 10, properties={"Target": "M1"})
    ctx.connect(m.id, ping.id)
    res = ctx.run_rider(ping.id)                     # no orchestrator attached
    assert res["ok"] is False and "Run" in res["error"]


def test_run_rider_streams_output_stores_measurement_and_emits(monkeypatch):
    _app()
    from gini.services.rider_runner import RiderRunner
    sample = ("5 packets transmitted, 5 packets received, 0% packet loss\n"
              "round-trip min/avg/max = 0.1/0.2/0.3 ms\n")
    monkeypatch.setattr(RiderRunner, "_exec",
                        lambda self, service, argv: (0, sample), raising=True)

    ctx = AppContext()
    ctx.orchestrator = _FakeOrch()
    m = ctx.add_device("host", 10, 10)              # donor is "M1"
    ping = ctx.add_device("ping_probe", 40, 10, properties={"Target": "M1", "Count": "5"})
    ctx.connect(m.id, ping.id)

    seen, logs = [], []
    ctx.bus.rider_ran.connect(lambda did, r: seen.append((did, r)))
    ctx.bus.log.connect(lambda lvl, msg: logs.append(msg))

    res = ctx.run_rider(ping.id)
    assert res["ok"] and res["measurement"]["loss_pct"] == 0.0
    assert ctx.rider_results[ping.id]["summary"] == res["summary"]      # remembered
    assert seen and seen[0][0] == ping.id                               # emitted
    # the console now gets ONE concise summary line (raw goes to the inspector)
    assert any("loss" in m for m in logs) and not any("icmp_seq" in m for m in logs)


def test_inspector_shows_a_selected_riders_output_and_kpis(monkeypatch):
    _app()
    from gini.services.rider_runner import RiderRunner
    from gini.ui.main_window import MainWindow
    sample = ("PING M2: 56 data bytes\n64 bytes from M2: icmp_seq=0 time=0.2 ms\n"
              "5 packets transmitted, 5 packets received, 0% packet loss\n"
              "round-trip min/avg/max = 0.1/0.2/0.3 ms\n")
    monkeypatch.setattr(RiderRunner, "_exec",
                        lambda self, service, argv: (0, sample), raising=True)

    w = MainWindow(QApplication.instance())
    ctx = w.ctx
    ctx.orchestrator = _FakeOrch()
    m = ctx.add_device("host", 10, 10)                 # donor "M1"
    ping = ctx.add_device("ping_probe", 40, 10, properties={"Target": "M2"})
    ctx.connect(m.id, ping.id)
    ctx.select(ping.id)                                # inspector now shows the Ping Probe

    ctx.run_rider(ping.id)                             # emits rider_ran -> inspector updates
    insp = w.inspector
    assert "0% packet loss" in insp.live.toPlainText()     # raw stream in the per-node console
    # (isVisible() is False for any child of an unshown offscreen window — check the explicit state)
    assert not insp.kpis_lbl.isHidden()                    # measurement chips shown
    assert "loss" in insp.kpis_lbl.text()


class _FakeSessions:
    def __init__(self):
        self.running = set()

    def is_running(self, rid):
        return rid in self.running

    def available(self):
        return True

    def start(self, topo, rid, on_update):
        self.running.add(rid)
        on_update(rid, {"ok": True, "running": True, "raw": "line",
                        "measurement": {"ok": True, "loss_pct": 0.0}, "summary": "0% loss"})
        return {"ok": True, "running": True, "donor": "M1"}

    def stop(self, rid):
        self.running.discard(rid)

    def stop_all(self):
        self.running.clear()


def test_double_click_toggles_a_rider_start_then_stop():
    _app()
    ctx = AppContext()
    ctx.orchestrator = _FakeOrch()
    ctx._rider_sessions = _FakeSessions()             # inject: no Docker
    m = ctx.add_device("host", 10, 10)
    ping = ctx.add_device("ping_probe", 40, 10, properties={"Target": "M2", "Count": "0"})
    ctx.connect(m.id, ping.id)

    states = []
    ctx.bus.rider_ran.connect(lambda i, s: states.append(s.get("running")))

    ctx.toggle_rider(ping.id)                         # start
    assert ctx.is_rider_running(ping.id) and states and states[-1] is True
    ctx.toggle_rider(ping.id)                         # stop
    assert not ctx.is_rider_running(ping.id)


def test_attach_edges_render_dotted_and_never_animate():
    _app()
    theme = get_theme("dark")
    ctx = AppContext()
    scene = CanvasScene(ctx, theme)                 # scene first, as in the real app
    m = ctx.add_device("host", 60, 80)
    ctx.connect(m.id, ctx.add_device("ping_probe", 200, 40).id)
    ctx.connect(ctx.add_device("packet_view", 200, 150).id, m.id)

    attach = [e for e in scene.items()
              if isinstance(e, EdgeItem) and e.link.kind == "attach"]
    assert len(attach) == 2
    for e in attach:                                # attach edges carry no traffic
        e.flow("blue")
        assert e._packet_t is None

"""Live CPU/memory metrics in the Inspector — parsing, sampling, and the plot widget."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_parse_mem_mib_units():
    from gini.services.orchestrator import _parse_mem_mib
    assert _parse_mem_mib("512MiB") == 512.0
    assert _parse_mem_mib("1.5GiB") == 1536.0
    assert round(_parse_mem_mib("1024KiB"), 1) == 1.0
    assert _parse_mem_mib("0B") == 0.0
    assert _parse_mem_mib("garbage") == 0.0


def test_stats_none_when_not_running():
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    o = Orchestrator(rt.__path__[0])
    assert o.stats("wa1") is None        # no workdir -> nothing launched


def test_stats_parses_docker_stats(monkeypatch):
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp

    class R:
        def __init__(self, out): self.stdout, self.returncode, self.stderr = out, 0, ""

    def fake_run(cmd, **kw):
        if cmd[:3] == ["docker", "compose", "ps"]:
            return R("cid42\n")
        if cmd[:2] == ["docker", "stats"]:
            return R("37.5%|128.4MiB / 512MiB|1.2kB / 3.4kB\n")
        return R("")
    monkeypatch.setattr(sp, "run", fake_run)
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"
    s = o.stats("wa1")
    assert s["cpu"] == 37.5 and s["mem_used"] == 128.4
    assert round(s["net_bytes"]) == 4600          # 1.2kB + 3.4kB (decimal)


def test_live_metrics_widget_accumulates():
    from PySide6.QtWidgets import QApplication
    from gini.ui.theme.manager import ThemeManager
    from gini.ui.live_metrics import LiveMetrics
    app = QApplication.instance() or QApplication([])
    m = LiveMetrics(ThemeManager(app, "Dark"))
    for i in range(5):
        m.push(10.0 + i, 100.0 + i, 5.0 + i, 20.0 + i)   # cpu, mem, throughput, latency
    assert list(m._d["cpu"])[-1] == 14.0 and list(m._d["mem"])[-1] == 104.0
    assert list(m._d["thru"])[-1] == 9.0 and list(m._d["lat"])[-1] == 24.0
    m.push(1.0, 2.0)                                      # latency optional -> None
    assert list(m._d["lat"])[-1] is None
    m.reset()
    assert len(m._d["cpu"]) == 0


def test_stats_all_maps_container_names_to_services(monkeypatch):
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp

    class R:
        def __init__(self, out): self.stdout, self.returncode, self.stderr = out, 0, ""

    out = ("gini-lab-wa1-1|12.0%|50MiB / 512MiB|1kB / 1kB\n"
           "gini-lab-ca1-1|5.0%|10MiB / 256MiB|2kB / 0B\n")
    monkeypatch.setattr(sp, "run", lambda cmd, **k: R(out))
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"
    alls = o.stats_all()
    assert set(alls) == {"wa1", "ca1"}
    assert alls["wa1"]["cpu"] == 12.0 and round(alls["wa1"]["net_bytes"]) == 2000


def test_live_history_is_per_element_and_survives_selection():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    from gini.services.compiler import _svc
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    wa = w.api.add_device("web_app", x=80, y=80)["id"]
    ca = w.api.add_device("cache", x=320, y=80)["id"]
    app.processEvents()
    insp = w.inspector
    insp.set_live_running(True)
    insp.tabs.setCurrentWidget(insp._live_host)
    w.ctx.bus.selection_changed.emit(wa)
    app.processEvents()

    # one whole-lab sample feeds BOTH elements' buffers, even though WA is selected
    import time
    svc_wa, svc_ca = _svc(w.ctx.topology.devices[wa].name), _svc(w.ctx.topology.devices[ca].name)
    for _ in range(4):
        insp._on_metrics(({svc_wa: {"cpu": 30, "mem_used": 50, "net_bytes": 0},
                           svc_ca: {"cpu": 8, "mem_used": 10, "net_bytes": 0}}, time.monotonic()))
    assert len(insp._hist[svc_wa]["cpu"]) == 4 and len(insp._hist[svc_ca]["cpu"]) == 4

    # switch to the cache and back — WA's history is intact (not wiped)
    w.ctx.bus.selection_changed.emit(ca); app.processEvents()
    w.ctx.bus.selection_changed.emit(wa); app.processEvents()
    assert len(insp._hist[svc_wa]["cpu"]) == 4          # preserved across selection
    assert insp.metrics._d is insp._hist[svc_wa]        # chart now points at WA's buffer


def test_inspector_shows_metrics_only_for_running_containers():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    inst = w.api.add_device("instance", x=80, y=80)["id"]
    sw = w.api.add_device("switch", x=320, y=80)["id"]
    app.processEvents()

    insp = w.inspector
    # not running -> no metrics, the text/refresh view is shown
    w.ctx.bus.selection_changed.emit(inst)
    assert not insp.metrics.isVisibleTo(insp)        # isVisibleTo: ignores unshown window

    # running + a container element selected + Live tab -> metrics show, poll timer on
    insp.set_live_running(True)
    insp.tabs.setCurrentWidget(insp._live_host)
    w.ctx.bus.selection_changed.emit(inst)
    assert insp.metrics.isVisibleTo(insp)
    assert insp._live_timer.isActive()

    # selecting a switch (not a container) hides the plots + stops polling
    w.ctx.bus.selection_changed.emit(sw)
    assert not insp.metrics.isVisibleTo(insp)
    assert not insp._live_timer.isActive()

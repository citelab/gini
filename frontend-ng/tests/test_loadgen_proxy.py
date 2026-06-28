"""Proxy/LB auto-routing, the load-generator throttle, and the Traefik latency adapter."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _svc


def _proxy_topo(scheme=None):
    t = Topology("lb")
    lb = t.add_device("load_balancer")
    if scheme:
        lb.properties["Scheme"] = scheme
    w1 = t.add_device("web_app"); w2 = t.add_device("web_app")
    t.add_link(lb.id, w1.id); t.add_link(lb.id, w2.id)
    return t, lb, w1, w2


def test_nginx_lb_config_has_upstreams_algo_and_status():
    t, lb, w1, w2 = _proxy_topo("least_conn")
    cfg = RuntimeCompiler().compile(t)
    s = next(x for x in cfg.services if x.name == lb.name)
    conf = s.files[f"{_svc(lb.name)}/nginx.conf"]
    assert "least_conn;" in conf
    assert f"server {_svc(w1.name)}:80;" in conf and f"server {_svc(w2.name)}:80;" in conf
    assert "stub_status" in conf                       # so the fabric can read req/s
    assert any("/etc/nginx/nginx.conf" in v for v in s.volumes)


def test_traefik_proxy_routes_to_backends():
    t = Topology("p")
    pxy = t.add_device("proxy"); w = t.add_device("web_app")
    t.add_link(pxy.id, w.id)
    cfg = RuntimeCompiler().compile(t)
    s = next(x for x in cfg.services if x.name == pxy.name)
    dyn = s.files[f"{_svc(pxy.name)}/dynamic.yml"]
    assert f"http://{_svc(w.name)}:80" in dyn
    assert "--providers.file.directory=/etc/traefik/dynamic" in s.command


def test_proxy_without_backend_is_noted_not_configured():
    t = Topology("p"); p = t.add_device("load_balancer")
    cfg = RuntimeCompiler().compile(t)
    s = next(x for x in cfg.services if x.name == p.name)
    assert not s.files                                 # nothing generated
    assert any("no backends wired" in n for n in cfg.notes)


def test_traefik_adapter_reports_rps_and_latency():
    from gini.runtime import cloudfabric_agent as cf
    rates = cf.Rates()
    svc = {"name": "pxy1", "type": "proxy", "host": "h", "port": 8080}
    page1 = ("traefik_entrypoint_requests_total{e=\"web\"} 0\n"
             "traefik_entrypoint_request_duration_seconds_sum{e=\"web\"} 0\n"
             "traefik_entrypoint_request_duration_seconds_count{e=\"web\"} 0\n")
    page2 = ("traefik_entrypoint_requests_total{e=\"web\"} 100\n"
             "traefik_entrypoint_request_duration_seconds_sum{e=\"web\"} 2.0\n"
             "traefik_entrypoint_request_duration_seconds_count{e=\"web\"} 100\n")
    seq = iter([page1, page2])
    cf._http_get = lambda *a, **k: next(seq)           # monkeypatch fetch
    cf.collect_proxy(svc, rates, 0.0)                  # baseline
    out = cf.collect_proxy(svc, rates, 1.0)            # +100 reqs, +2.0s over 1s
    labels = {k["label"]: (k["value"], k["unit"]) for k in out["kpis"]}
    assert labels["req"] == (100.0, "/s")
    assert labels["latency"] == (20.0, "ms")           # 2.0s / 100 = 20ms avg
    assert out["latency_ms"] == 20.0


def test_inspector_qps_commit_rebuilds_deferred_without_crash():
    # the rate slider commits QPS, which triggers device_changed -> inspector rebuild.
    # That rebuild must be deferred (singleShot) so it doesn't delete the live slider
    # mid-signal (which segfaulted). Here we exercise the commit + deferred rebuild path.
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    lg = w.api.add_device("load_generator", x=80, y=80)["id"]
    app.processEvents()
    w.ctx.bus.selection_changed.emit(lg)               # build the LG form (with the slider)
    app.processEvents()
    w.inspector._commit("QPS", "300")                  # what sliderReleased does
    app.processEvents()                                # runs the deferred rebuild
    assert w.ctx.topology.devices[lg].properties["QPS"] == "300"


def test_loadgen_throttle_drives_fortio_on_qps_change():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    lg = w.api.add_device("load_generator", x=80, y=80)["id"]
    wa = w.api.add_device("web_app", x=320, y=80)["id"]
    w.ctx.topology.add_link(lg, wa)
    app.processEvents()

    # pretend the lab is running with a known published port for the LG
    w._running = True
    lg_name = w.ctx.topology.devices[lg].name
    wa_name = w.ctx.topology.devices[wa].name

    class S:
        def __init__(s, n): s.name, s.ports = n, [{"container": 8080, "host": 38000,
                                                   "label": "console", "web": True}]
    w._last_services = [S(lg_name)]
    calls = []
    w._gloader.drive_load = lambda hp, url, qps, conns: (calls.append((hp, url, qps)) or (True, "ok"))

    # target resolves to the connected web app's URL; QPS change re-drives
    assert w._loadgen_target(lg) == f"http://{_svc(wa_name)}/"
    w.api.set_property(lg, "QPS", "250")               # emits device_changed -> re-drive
    app.processEvents()
    assert calls and calls[-1][0] == 38000
    assert calls[-1][1] == f"http://{_svc(wa_name)}/" and calls[-1][2] == "250"

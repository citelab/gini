"""GINI Cloud Fabric: the telemetry agent, its wiring, and GUI surfacing."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import _compose, CLOUDFABRIC_HOST_PORT


# --- agent parsers (no network) ------------------------------------------- #
def test_agent_parsers():
    from gini.runtime import cloudfabric_agent as cf
    info = cf.parse_redis_info("# Server\r\ninstantaneous_ops_per_sec:1200\r\n"
                               "keyspace_hits:90\r\nkeyspace_misses:10\r\n"
                               "connected_clients:7\r\nused_memory:2097152\r\n")
    k = {x["label"]: x["value"] for x in cf.redis_kpis(info)}
    assert k["ops"] == 1200.0 and k["hit rate"] == 90.0 and k["clients"] == 7

    rk = {x["label"]: x["value"] for x in cf.rabbit_kpis(
        {"queue_totals": {"messages": 42},
         "message_stats": {"publish_details": {"rate": 9.0},
                           "deliver_get_details": {"rate": 8.0}}})}
    assert rk["queued"] == 42 and rk["publish"] == 9.0 and rk["deliver"] == 8.0

    m = cf.parse_prometheus('traefik_x_requests_total{c="200"} 70\n'
                            'traefik_x_requests_total{c="500"} 30\n')
    assert sum(v for kk, v in m.items() if kk.endswith("requests_total")) == 100.0

    ng = cf.parse_nginx_status("Active connections: 5\nserver accepts handled requests\n"
                               " 80 80 640\nReading: 0 Writing: 1 Waiting: 4")
    assert ng["active"] == 5 and ng["requests"] == 640


def test_agent_rate_tracker_and_snapshot():
    from gini.runtime import cloudfabric_agent as cf
    r = cf.Rates()
    assert r.rate("x", 100, 0.0) == 0.0          # first sample -> no rate
    assert r.rate("x", 160, 2.0) == 30.0         # +60 over 2s
    # a snapshot over unreachable services must not raise and reports them down
    snap = cf.snapshot({"services": [{"name": "db1", "type": "database",
                                      "host": "127.0.0.1", "port": 1, "creds": {}}]}, r)
    assert snap["services"]["db1"]["up"] is False
    assert snap["totals"]["services_total"] == 1


# --- compiler / orchestrator wiring --------------------------------------- #
def test_fabric_spec_lists_services_with_probes():
    t = Topology("cloud")
    db = t.add_device("database")
    ca = t.add_device("cache")
    q = t.add_device("queue")
    t.add_device("dashboard")                    # observability infra -> skipped
    cfg = RuntimeCompiler().compile(t)
    assert cfg.fabric is not None
    by = {s["name"]: s for s in cfg.fabric.services}
    from gini.services.compiler import _svc
    assert by[_svc(db.name)]["port"] == 5432 and by[_svc(db.name)]["creds"]["user"] == "gini"
    assert by[_svc(ca.name)]["port"] == 6379
    assert by[_svc(q.name)]["port"] == 15672 and by[_svc(q.name)]["creds"]["user"] == "guest"
    assert "GRAF" not in " ".join(by)            # dashboard not watched
    # no fabric when there are no cloud services
    assert RuntimeCompiler().compile(Topology("net")).fabric is None


def test_compose_emits_cloudfabric_service():
    t = Topology("cloud"); t.add_device("cache")
    compose = _compose(RuntimeCompiler().compile(t))
    assert "cloudfabric:" in compose
    assert "Dockerfile.cloudfabric" in compose
    assert "FABRIC_CONFIG:" in compose
    assert f'"{CLOUDFABRIC_HOST_PORT}:9099"' in compose


def test_write_project_includes_agent_and_dockerfile(tmp_path):
    from gini.services.orchestrator import write_project
    import gini.runtime as rt
    t = Topology("cloud"); t.add_device("cache")
    work = write_project(RuntimeCompiler().compile(t), tmp_path, rt.__path__[0])
    assert (work / "docker" / "Dockerfile.cloudfabric").exists()
    assert (work / "dataplane" / "cloudfabric_agent.py").exists()


# --- GUI surfacing -------------------------------------------------------- #
def test_dashboard_and_inspector_show_fabric_metrics():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    from gini.services.compiler import _svc
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    ca = w.api.add_device("cache", x=80, y=80)["id"]
    app.processEvents()

    snap = {"ts": 1, "totals": {"rps": 256, "services_up": 1, "services_total": 1},
            "services": {_svc(w.ctx.topology.devices[ca].name): {
                "type": "cache", "up": True,
                "kpis": [{"label": "ops", "value": 1200, "unit": "/s"},
                         {"label": "hit rate", "value": 90, "unit": "%"}]}}}

    w.dashboard.set_fabric(snap["totals"])
    assert w.dashboard.rps_lbl.text() == "256"

    # select the cache on the Live tab while "running" -> its KPIs show
    insp = w.inspector
    insp.set_live_running(True)
    insp.tabs.setCurrentWidget(insp._live_host)
    w.ctx.bus.selection_changed.emit(ca)
    insp.set_fabric_snapshot(snap)
    assert insp.kpis_lbl.isVisibleTo(insp)
    assert "ops" in insp.kpis_lbl.text() and "1200" in insp.kpis_lbl.text()

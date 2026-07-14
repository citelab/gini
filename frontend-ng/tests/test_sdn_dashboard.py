"""OVS SDN dashboard: the Router Lab opens in flow-table mode for an OVS and renders the
parsed live flow table from the gRouter `openflow …` dumps."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.domain.router_modules import RouterProgram
from gini.ui.main_window import MainWindow
from gini.ui.router_lab import RouterLab
from gini.ui.theme import ThemeManager

ENTRY = ("=========\nEntry 0\n=========\n\nMatch:\n\tInput port: 1\n"
         "\tIP source address: 10.0.0.1\n\tIP destination address: 10.0.0.2\n"
         "Priority: 100\nActions:\n\tAction 0:\n\t\tType: OFPAT_OUTPUT\n\t\tOutput port: 2\n")
STATS = ("=========\nEntry 0\n=========\n\nMatch:\n\tInput port: 1\n"
         "Packet count: 55\nByte count: 4096\n")


def _app():
    return QApplication.instance() or QApplication([])


class _Dev:
    name = "OVS1"
    type_key = "ovs"


def test_ovs_lab_renders_flow_table():
    app = _app()
    def qf(cmd):
        return ENTRY if "stats" not in cmd else STATS
    lab = RouterLab(None, ThemeManager(app), _Dev(), RouterProgram(), sdn=True, query_fn=qf)
    lab._refresh_flows()
    # the worker thread posts via a queued signal; drain it synchronously here
    from gini.domain.flowtable import flows
    lab._on_flows(flows(qf("openflow entry all"), qf("openflow stats entry all")))
    assert lab.flow_table.rowCount() == 1
    assert "10.0.0.1→10.0.0.2" in lab.flow_table.item(0, 1).text()
    assert lab.flow_table.item(0, 2).text() == "output:2"
    assert lab.flow_table.item(0, 3).text() == "55"        # packets from stats
    assert lab.sdn and lab.program.mode == "openflow"


def test_double_click_ovs_opens_sdn_lab():
    app = _app()
    w = MainWindow(app)
    oid = w.api.add_device("ovs")["id"]
    w._on_device_activated(oid)                # double-click behaviour
    assert isinstance(w._router_lab, RouterLab) and w._router_lab.sdn


def test_router_still_opens_plain_lab():
    app = _app()
    w = MainWindow(app)
    rid = w.api.add_device("router")["id"]
    w._on_device_activated(rid)
    assert isinstance(w._router_lab, RouterLab) and not w._router_lab.sdn


ROUTES = ("Index\tNetwork\t\tNetmask\t\tNexthop\t\tInterface\n"
          "[0]\t10.0.1.0\t255.255.255.0\t0.0.0.0\t\ttun1\n"
          "[1]\t10.0.2.0\t255.255.255.0\t10.0.1.2\t\ttun2\n")


class _Router:
    name = "R1"
    type_key = "router"


def test_router_lab_renders_routing_table():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Router(), RouterProgram(),
                    sdn=False, query_fn=lambda c: ROUTES)
    from gini.domain.routetable import parse_routes
    lab._on_routes(parse_routes(ROUTES))
    assert lab.route_table.rowCount() == 2
    assert lab.route_table.item(0, 0).text() == "10.0.1.0"
    assert lab.route_table.item(0, 2).text() == "direct (on-link)"   # 0.0.0.0 nexthop
    assert lab.route_table.item(1, 2).text() == "10.0.1.2"


def test_flow_table_stats_header():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Dev(), RouterProgram(), sdn=True,
                    query_fn=lambda c: "")
    lab._on_table_stats({"active": 2, "lookups": 40, "matched": 38})
    assert "2 active" in lab.flow_stats.text() and "38 matched" in lab.flow_stats.text()


def test_flow_event_log_records_install_then_expire():
    app = _app()
    from gini.domain.flowtable import FlowEntry
    lab = RouterLab(None, ThemeManager(app), _Dev(), RouterProgram(), sdn=True,
                    query_fn=lambda c: "")
    lab._on_flows([FlowEntry(0, {"IP source address": "10.0.1.10",
                                 "IP destination address": "10.0.1.12"}, ["output:2"])])
    lab._on_flows([])                       # the rule expired next poll
    events = [lab.flow_events.item(r, 1).text() for r in range(lab.flow_events.rowCount())]
    assert any("installed" in e for e in events) and any("expired" in e for e in events)

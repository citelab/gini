"""The Router Lab's router face: the flow band, the packet visualizer, the meter, the route form.

The face was relaid out for a laptop screen: a horizontal band (pipeline left → right, interfaces
down each side) and ONE tabbed area below, instead of a vertical pipeline above four always-open
panels. These pin what the new face promises. Everything here runs offline with fakes; the same
paths were verified end to end against a real gRouter when built (watch, ifstat, route add/del).

The band's packets are a REPLAY of the router's own records, so the most important property is
that each one ends where the router says it ended — forwarded out the right interface, stopped at
the module that dropped it, at Route for want of a route, at Parse when its TTL ran out.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from gini.domain import router_watch as RW
from gini.domain.router_modules import RouterProgram
from gini.ui.router_lab import RouterLab
from gini.ui.theme import ThemeManager


class _Router:
    name = "R1"
    type_key = "router"
    properties: dict = {}


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _lab(app, program=None, sent=None, **kw):
    sent = sent if sent is not None else []
    lab = RouterLab(None, ThemeManager(app), _Router(), program or RouterProgram(),
                    face="router", query_fn=lambda c: (sent.append(c), "")[1], **kw)
    lab.resize(1060, 760)
    lab.show()
    app.processEvents()
    return lab


IFS = {1: RW.IfStat(1, "tun1", "10.0.1.1", 0, 0, 0, 0),
       2: RW.IfStat(2, "tun2", "10.0.2.1", 0, 0, 0, 0)}


def _event(fate, **kw):
    base = dict(seq=1, ms=0, fate=fate, in_if=1, out_if=-1, module=-1, module_type="",
                src="10.0.1.10", dst="10.0.2.10", proto=1, ttl=63, length=84)
    base.update(kw)
    return RW.PacketEvent(**base)


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

def test_the_router_face_is_a_band_and_four_tabs(app):
    lab = _lab(app)
    assert [lab.tabs.tabText(i) for i in range(lab.tabs.count())] == \
        ["Routes", "Packets", "Traffic QoS", "Link delay"]
    assert lab.flow.height() < 300, "the band is a strip, not two fifths of the window"
    assert not hasattr(lab, "pipe_scroll"), "the router face no longer has the vertical pipeline"


def test_the_other_faces_keep_their_layouts(app):
    fw = RouterLab(None, ThemeManager(app), _Router(), RouterProgram(), face="firewall")
    assert hasattr(fw, "pipe_scroll") and not hasattr(fw, "flow")


def test_the_watch_button_needs_a_running_router(app):
    lab = _lab(app)
    assert not lab.watch_btn.isEnabled()
    assert _lab(app, stream_fn=lambda: None).watch_btn.isEnabled()


# --------------------------------------------------------------------------- #
# packets end where the router says they ended
# --------------------------------------------------------------------------- #

@pytest.fixture
def band(app):
    prog = RouterProgram(); prog.add("acl"); prog.add("block")
    lab = _lab(app, prog)
    lab._on_ifstat(IFS)
    app.processEvents()
    return lab.flow


def _chip(band, pred):
    return next(c for c in band.chips if pred(c.stage))


def test_a_forwarded_packet_ends_at_its_out_interface(band):
    tok = band.token_for(_event("F", out_if=2), 0.0)
    ro = band.rail_out
    assert tok.points[-1].y() == pytest.approx(ro.y() + ro.row_center(2))
    assert tok.points[0].y() == pytest.approx(band.rail_in.y() + band.rail_in.row_center(1))


def test_a_dropped_packet_stops_under_the_module_that_dropped_it(band):
    tok = band.token_for(_event("D", module=1, module_type="block"), 0.0)
    block = _chip(band, lambda s: s.kind == "inline" and s.index == 1)
    assert tok.points[-1] == band._lane_point(block)
    assert tok.burst


def test_a_drop_by_an_unsynced_module_is_matched_by_type(band):
    tok = band.token_for(_event("D", module=7, module_type="acl"), 0.0)
    assert tok.points[-1] == band._lane_point(_chip(band, lambda s: s.key == "acl"))


def test_no_route_stops_at_route_and_ttl_at_parse(band):
    assert band.token_for(_event("N"), 0.0).points[-1] == \
        band._lane_point(_chip(band, lambda s: s.key == "route"))
    assert band.token_for(_event("T"), 0.0).points[-1] == \
        band._lane_point(_chip(band, lambda s: s.key == "parse"))


def test_a_packet_for_the_router_ends_at_router_itself(band):
    tok = band.token_for(_event("L"), 0.0)
    ro = band.rail_out
    assert tok.points[-1].y() == pytest.approx(ro.y() + ro.row_center("local"))


def test_landing_counts_drops_and_local_packets(band):
    band.landed(band.token_for(_event("N"), 0.0))
    band.landed(band.token_for(_event("L"), 0.0))
    band.landed(band.token_for(_event("F", out_if=2), 0.0))
    assert (band.rail_out.dropped, band.rail_out.local) == (1, 1)


def test_packets_ride_a_lane_under_the_chips_not_over_their_names(band):
    tok = band.token_for(_event("F", out_if=2), 0.0)
    chip_bottom = max(c.mapTo(band, c.rect().bottomLeft()).y() for c in band.chips)
    for p in tok.points[1:-1]:
        assert p.y() > chip_bottom


def test_a_flood_is_drawn_as_a_sample_and_the_rest_counted(band):
    many = [_event("F", seq=i, out_if=2) for i in range(500)]
    left_out = band.show_packets(many)
    assert left_out > 0 and len(band.overlay.pending) <= 2 * 48


# --------------------------------------------------------------------------- #
# the module bar
# --------------------------------------------------------------------------- #

def test_adding_a_module_selects_it_for_editing(app):
    lab = _lab(app)
    lab._add_and_select("acl")
    assert lab._selected == 0 and lab.sel_box.isVisibleTo(lab)


def test_a_chip_shows_what_its_module_is_set_to(app):
    prog = RouterProgram(); prog.add("acl"); prog.inline[0].params["deny"] = "10.0.3.0/24"
    lab = _lab(app, prog)
    chip = next(c for c in lab._stage_widgets if c.stage.kind == "inline")
    assert chip.sub.text() == "10.0.3.0/24"


def test_editing_a_parameter_updates_the_chip_live(app):
    lab = _lab(app)
    lab._add_and_select("block")
    edit = next(w for w in lab.sel_box.findChildren(QtWidgets.QLineEdit))
    edit.setText("10.0.2.99")
    chip = next(c for c in lab._stage_widgets if c.stage.kind == "inline")
    assert chip.sub.text() == "10.0.2.99"
    assert lab.program.dirty, "an edit is a draft the poll must not overwrite"


def test_remove_from_the_editor(app):
    lab = _lab(app)
    lab._add_and_select("acl")
    lab._remove_selected()
    assert lab.program.inline == [] and not lab.sel_box.isVisibleTo(lab)


# --------------------------------------------------------------------------- #
# the Routes tab
# --------------------------------------------------------------------------- #

def test_the_form_shows_the_command_before_sending(app):
    lab = _lab(app)
    lab._on_ifstat(IFS)
    lab.rt_dest.setText("10.0.3.12"); lab.rt_host.setChecked(True)
    lab.rt_via.setText("10.0.2.1"); lab.rt_iface.setEditText("tun2 · 10.0.2.1")
    assert "route add -dev tun2 -net 10.0.3.12 -netmask 255.255.255.255 -gw 10.0.2.1" \
        in lab.rt_preview.text()
    assert lab.rt_add.isEnabled()


def test_a_bad_route_says_why_and_cannot_be_added(app):
    lab = _lab(app)
    lab.rt_dest.setText("10.0.3"); lab.rt_iface.setEditText("tun1")
    assert not lab.rt_add.isEnabled()
    assert "IPv4" in lab.rt_preview.text()


def test_the_interface_menu_follows_the_router(app):
    lab = _lab(app)
    lab._on_ifstat(IFS)
    assert [lab.rt_iface.itemText(i) for i in range(lab.rt_iface.count())] == \
        ["tun1 · 10.0.1.1", "tun2 · 10.0.2.1"]


def test_add_sends_exactly_the_previewed_command(app, qtbot=None):
    sent = []
    lab = _lab(app, sent=sent)
    lab.rt_dest.setText("10.0.5.0"); lab.rt_iface.setEditText("tun1")
    lab._add_route()
    for _ in range(50):
        app.processEvents()
        if "route add -dev tun1 -net 10.0.5.0 -netmask 255.255.255.0" in sent:
            break
    assert "route add -dev tun1 -net 10.0.5.0 -netmask 255.255.255.0" in sent


def test_delete_asks_first_and_cancel_sends_nothing(app, monkeypatch):
    from gini.domain.routetable import RouteEntry
    sent = []
    lab = _lab(app, sent=sent)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Cancel))
    lab._delete_route(RouteEntry(2, "10.0.3.12", "255.255.255.255", "10.0.2.1", "tun2", "S"))
    app.processEvents()
    assert not any(c.startswith("route del") for c in sent)


def test_each_route_row_has_a_delete(app):
    from gini.domain.routetable import RouteEntry
    lab = _lab(app)
    lab._on_routes([RouteEntry(0, "10.0.1.0", "255.255.255.0", "0.0.0.0", "tun1", "C")])
    assert lab.route_table.cellWidget(0, 4).text() == "Delete"


# --------------------------------------------------------------------------- #
# the feed: watch on, starting point, off on close
# --------------------------------------------------------------------------- #

class _FakeStream:
    def __init__(self):
        self.sent = []
        self.farewell = None

    def request(self, cmd):
        self.sent.append(cmd)

    def stop(self, farewell=""):
        self.farewell = farewell

    def deleteLater(self):
        pass


def _watching(app):
    lab = _lab(app, stream_fn=lambda: None)
    lab._stream = _FakeStream()
    lab.watch_btn.setChecked(True)
    return lab


def test_turning_watch_on_asks_the_router_and_looks_on(app):
    lab = _watching(app)
    assert "watch on" in lab._stream.sent
    assert lab.watch_btn.text().strip() == "Watching packets"


def test_the_first_dump_only_sets_the_starting_point(app):
    """Turning watch on must not replay whatever the ring held from last time."""
    lab = _watching(app)
    old = RW.WatchDump(on=True, next_seq=40, ok=True, events=[_event("F", seq=40, out_if=2)])
    lab._on_watch_dump(old)
    assert lab.pkt_table.rowCount() == 0 and lab._since == 40


def test_later_dumps_list_packets_newest_first(app):
    lab = _watching(app)
    lab._on_watch_dump(RW.WatchDump(on=True, next_seq=10, ok=True))
    evs = [_event("F", seq=11, out_if=2), _event("N", seq=12, dst="10.9.9.9")]
    lab._on_watch_dump(RW.WatchDump(on=True, next_seq=12, ok=True, events=evs))
    assert lab.pkt_table.rowCount() == 2
    assert lab.pkt_table.item(0, 0).text() == "12"
    assert lab.pkt_table.item(0, 3).text() == "no route"


def test_closing_the_lab_turns_watch_off_on_the_router(app):
    lab = _watching(app)
    st = lab._stream
    lab.hide()
    app.processEvents()
    assert st.farewell == "watch off"
    assert not lab.watch_btn.isChecked()


def test_an_old_router_image_is_named_not_silent(app):
    lab = _lab(app)
    lab._on_stream_state("this router image predates packet watch — rebuild gini-grouter")
    assert "rebuild" in lab.flow.rail_in.note


# --------------------------------------------------------------------------- #
# the meter
# --------------------------------------------------------------------------- #

def test_two_readings_make_a_rate_in_the_header_and_on_the_rails(app):
    lab = _lab(app)
    lab._on_ifstat(IFS)
    lab._ifstat_prev = (lab._ifstat_prev[0], lab._ifstat_prev[1] - 2.0)   # two seconds ago
    later = {1: RW.IfStat(1, "tun1", "10.0.1.1", 250_000, 0, 0, 0),
             2: RW.IfStat(2, "tun2", "10.0.2.1", 0, 0, 125_000, 0)}
    lab._on_ifstat(later)
    assert lab.flow.rail_in.rates[1] == pytest.approx(1_000_000, rel=0.01)    # 250 kB / 2 s
    assert lab.flow.rail_out.rates[2] == pytest.approx(500_000, rel=0.01)
    assert "1.00 Mb/s" in lab.bw_lbl.text() and "500.0 kb/s" in lab.bw_lbl.text()


# --------------------------------------------------------------------------- #
# the session itself
# --------------------------------------------------------------------------- #

def test_the_stream_coalesces_repeated_requests(app):
    from gini.ui.router_flow import RouterStream
    s = RouterStream(None, ["true"])
    s.proc = object()                        # pretend running; nothing is written while not ready
    s.request("ifstat"); s.request("ifstat"); s.request("watch dump 3")
    assert s.queue == ["ifstat", "watch dump 3"]


def test_the_stream_names_an_old_router(app):
    from gini.ui.router_flow import RouterStream
    s = RouterStream(None, ["true"])
    said = []
    s.state.connect(said.append)
    s._handle("ifstat", "\nifstat: command not found\n")
    s._handle("watch dump 0", "\nwatch: command not found\n")
    assert len(said) == 2 and all("rebuild gini-grouter" in x for x in said)



# --------------------------------------------------------------------------- #
# ARP and packets the router itself sends
# --------------------------------------------------------------------------- #

def test_an_arriving_arp_goes_straight_to_the_router_never_through_route(band):
    tok = band.token_for(_event("A", proto=RW.ARP, module_type="answered"), 0.0)
    ro = band.rail_out
    assert tok.points[-1].y() == pytest.approx(ro.y() + ro.row_center("local"))
    route = _chip(band, lambda s: s.key == "route")
    assert band._lane_point(route) not in tok.points, "ARP is not routed"


def test_a_sent_packet_leaves_the_router_through_egress_to_its_interface(band):
    tok = band.token_for(_event("S", in_if=-1, out_if=2, proto=RW.ARP, sport=1), 0.0)
    ro = band.rail_out
    assert tok.points[0].y() == pytest.approx(ro.y() + ro.row_center("local"))
    assert tok.points[1] == band._lane_point(_chip(band, lambda s: s.kind == "egress"))
    assert tok.points[-1].y() == pytest.approx(ro.y() + ro.row_center(2))


def test_arp_is_purple(band):
    t = band.theme.theme
    from PySide6.QtGui import QColor
    tok = band.token_for(_event("A", proto=RW.ARP), 0.0)
    assert tok.color == QColor(t.accent_for("purple"))


def test_the_router_box_counts_both_ways(band):
    band.landed(band.token_for(_event("A", proto=RW.ARP), 0.0))
    band.landed(band.token_for(_event("S", in_if=-1, out_if=1), 0.0))
    assert (band.rail_out.local, band.rail_out.sent) == (1, 1)


def test_the_packets_tab_says_who_sent_it(app):
    lab = _watching(app)
    lab._on_ifstat(IFS)
    lab._on_watch_dump(RW.WatchDump(on=True, next_seq=0, ok=True))
    evs = [_event("A", seq=1, proto=RW.ARP, sport=1, module_type="answered", dst="10.0.1.1"),
           _event("S", seq=2, in_if=-1, out_if=2, proto=RW.ARP, sport=1, dst="10.0.2.10")]
    lab._on_watch_dump(RW.WatchDump(on=True, next_seq=2, ok=True, events=evs))
    assert lab.pkt_table.item(0, 1).text() == "router → tun2"
    assert lab.pkt_table.item(1, 1).text() == "tun1 → router"
    assert lab.pkt_table.item(1, 3).text() == "ARP answered"

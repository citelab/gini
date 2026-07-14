"""M3: board staging — a lesson can pre-build part of the canvas (scaffolded / fault-injection labs)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from gini.domain import catalog as C, lesson as L, staging


def test_normalize_tolerates_shapes():
    s = staging.normalize({"devices": [{"ref": "a", "type": "host"}],
                           "links": [["a", "b"], {"source": "a", "target": "b"}]})
    assert s["devices"] and s["links"] == [["a", "b"], ["a", "b"]]
    assert staging.normalize(None)["devices"] == []
    assert staging.normalize(None)["links"] == []


def test_a_staged_board_replaces_the_canvas_by_default():
    """A staged board is a DESIGNED board — the mission is graded against exactly it. Stacking it on
    top of whatever was already there would let stale elements tick objectives (or collide with the
    injected fault), so `reset` defaults on."""
    assert staging.normalize({"devices": [{"type": "host"}]})["reset"] is True
    assert staging.normalize({"devices": [{"type": "host"}], "reset": False})["reset"] is False

    staged = L.from_archetype("fix-the-address", {}, id="fa")
    assert staging.wants_reset(staged)
    assert not staging.wants_reset(SimpleNamespace(stage=None))    # unstaged missions never wipe


def test_apply_builds_devices_and_links():
    built, links = [], []
    counter = {"n": 0}

    def add_device(tk, x, y):
        counter["n"] += 1
        inst = SimpleNamespace(id=f"id{counter['n']}", type_key=tk)
        built.append((tk, x, y))
        return inst

    placed = staging.apply(
        {"devices": [{"ref": "s1", "type": "switch"}, {"ref": "h1", "type": "host", "x": 5, "y": 6}],
         "links": [["h1", "s1"], ["h1", "ghost"]]},   # ghost ref is skipped safely
        add_device=add_device, add_link=lambda a, b: links.append((a, b)))
    assert {t for t, _, _ in built} == {"switch", "host"}
    assert links == [(placed["h1"].id, placed["s1"].id)]   # only the resolvable link


def test_staged_fragment_carries_its_stage_to_the_lesson():
    arch = C.get("fix-the-lan")
    assert staging.is_staged(arch)                          # the archetype view exposes the stage
    les = L.from_archetype("fix-the-lan", {}, id="fx")
    assert staging.is_staged(les)                           # …and it lands on the built lesson


def test_stage_can_inject_a_MISCONFIGURATION_not_just_a_missing_element():
    """The second fault class: everything is present and correctly wired, but a setting is wrong.
    The IP is authored against the PEER's ref ('the leg facing s1') because link ids don't exist
    until the board is built — staging resolves it once the link is real."""
    from gini.domain.topology import Topology
    t = Topology()
    placed = staging.apply(
        {"manual_addressing": True,
         "devices": [{"ref": "s1", "type": "switch"},
                     {"ref": "h1", "type": "host", "ips": {"s1": "10.0.1.11"}},
                     {"ref": "h2", "type": "host", "ips": {"s1": "192.168.5.13"},
                      "properties": {"Note": "worked yesterday"}}],
         "links": [["h1", "s1"], ["h2", "s1"]]},
        add_device=lambda tk, x, y: t.add_device(tk, x=x, y=y),
        add_link=lambda a, b: t.add_link(a, b), topology=t)

    # without manual addressing the compiler would auto-assign IPs and silently REPAIR the fault
    assert t.manual_addressing is True
    h1, h2, s1 = placed["h1"], placed["h2"], placed["s1"]
    # each IP landed on the link facing the switch — and on the right device
    assert list(h1.static_ips.values()) == ["10.0.1.11"]
    assert list(h2.static_ips.values()) == ["192.168.5.13"]      # the injected fault
    assert not s1.static_ips                                     # the switch was never addressed
    link_ids = set(t.links)
    assert set(h1.static_ips) <= link_ids and set(h2.static_ips) <= link_ids
    assert h2.properties["Note"] == "worked yesterday"


def test_fix_the_address_cannot_be_passed_without_fixing_the_address():
    """End-to-end: the mis-config mission is structurally perfect from the first second, so ONLY the
    live probe can fail it — and only an address repair can turn it green."""
    import ipaddress
    from gini.agent.mission import Mission
    from gini.domain import objectives as O, probes as P
    from gini.domain.topology import Topology

    les = L.from_archetype("fix-the-address", {}, id="fa")
    t = Topology()
    placed = staging.apply(les.stage, add_device=lambda tk, x, y: t.add_device(tk, x=x, y=y),
                           add_link=lambda a, b: t.add_link(a, b), topology=t)

    class Wire:                        # models real /24 semantics: off-subnet hosts can't talk
        def available(self): return True
        def _net(self, name):
            d = t.find_by_name(name)
            ip = next(iter(d.static_ips.values()), "10.0.1.99")
            return ipaddress.ip_network(ip + "/24", strict=False)
        def reach(self, s, d, port=None): return self._net(s) == self._net(d)
        def http(self, *a): return False
        def backends(self, *a): return 0
        def flow(self, *a): return False

    def band():
        m = Mission(les); m.start()
        res = m.evaluate(O.TopologyWorld(t), P.TypeRunner(Wire(), lambda: t))
        return m, res

    m, res = band()
    assert all(r.status == "met" for r in res if r.level < 4)     # the picture is already perfect…
    assert [r.status for r in res if r.level == 4] == ["unmet"]   # …but the network is not
    assert not m.complete

    h3 = placed["h3"]
    h3.static_ips[next(iter(h3.static_ips))] = "10.0.1.13"        # the repair
    m, res = band()
    assert all(r.status == "met" for r in res) and m.complete
    assert m.score().band == "gold"


def test_start_mission_prebuilds_the_board():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    a = w.assistant
    a._loop = SimpleNamespace(backend=SimpleNamespace(
        chat=lambda *A, **K: iter([SimpleNamespace(text="ok", tool_call=None)])), brief="")
    a._refresh_mode_availability()
    a._missions_btn.setChecked(True)
    a._start_preview_mission("fix-the-lan")
    types = sorted(d.type_key for d in a.ctx.topology.devices.values())
    assert types == ["host", "host", "switch"]             # the pre-built board is on the canvas
    assert len(a.ctx.topology.links) == 2                   # hosts wired to the switch


def _armed_window():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    a = w.assistant
    a._loop = SimpleNamespace(backend=SimpleNamespace(
        chat=lambda *A, **K: iter([SimpleNamespace(text="ok", tool_call=None)])), brief="")
    a._refresh_mode_availability()
    a._missions_btn.setChecked(True)
    return a


def test_staged_mission_clears_the_stale_canvas_after_asking():
    a = _armed_window()
    a.ctx.add_device("router", 10, 10)                 # leftover from the student's last session
    a._confirm_clear_board = lambda lesson: True       # they say "Clear & Start"
    assert a._start_preview_mission("fix-the-address") is None    # no complaint = it started

    types = sorted(d.type_key for d in a.ctx.topology.devices.values())
    assert types == ["host", "host", "host", "switch"]  # ONLY the staged board — the router is gone
    assert a.ctx.topology.manual_addressing             # the fault would be auto-repaired otherwise


def test_declining_the_clear_leaves_the_board_alone_and_does_not_start():
    a = _armed_window()
    a.ctx.add_device("router", 10, 10)
    a._confirm_clear_board = lambda lesson: False       # they say "Cancel" — their work is precious
    said = a._start_preview_mission("fix-the-address")
    assert said and "Kept your board" in said           # …and we tell them why nothing happened

    types = [d.type_key for d in a.ctx.topology.devices.values()]
    assert types == ["router"]                          # untouched: nothing wiped, nothing staged
    assert a._mission_ctrl is None                      # and we did NOT start on the wrong board

"""M3: board staging — a lesson can pre-build part of the canvas (scaffolded / fault-injection labs)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from gini.domain import catalog as C, lesson as L, staging


def test_normalize_tolerates_shapes():
    s = staging.normalize({"devices": [{"ref": "a", "type": "host"}],
                           "links": [["a", "b"], {"source": "a", "target": "b"}]})
    assert s["devices"] and s["links"] == [["a", "b"], ["a", "b"]]
    assert staging.normalize(None) == {"devices": [], "links": []}


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

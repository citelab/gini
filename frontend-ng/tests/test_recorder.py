"""Scan-mode recording: capture ordered steps as the teacher demonstrates on the canvas.

The improvement over reading the final board: it preserves the teacher's SEQUENCE, updates counts in
place (a second host bumps the step, doesn't add a new one), and never auto-prunes on delete (the
teacher removes stray steps by hand — predictable beats clever).
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import authoring as AU
from gini.domain.topology import Topology


def test_steps_are_captured_in_demonstrated_order():
    t = Topology(); r = AU.Recorder()
    sw = t.add_device("switch", "S1"); r.capture(t)
    h1 = t.add_device("host", "H1"); r.capture(t)
    t.add_link(h1.id, sw.id); r.capture(t)
    steps = r.result()
    assert [s["check"] for s in steps] == ["exists(switch)", "exists(host)", "link(host, switch)"]
    assert [s["level"] for s in steps] == [1, 1, 2]     # order preserved; levels auto-derived


def test_a_repeated_type_updates_the_step_in_place_not_a_new_one():
    t = Topology(); r = AU.Recorder()
    t.add_device("host", "H1"); r.capture(t)
    t.add_device("host", "H2"); r.capture(t)
    t.add_device("host", "H3"); r.capture(t)
    steps = [s for s in r.result() if s["key"] == "place:host"]
    assert len(steps) == 1                              # one step, not three
    assert steps[0]["check"] == "count(host) >= 3"      # …bumped to the current count


def test_deletes_are_not_auto_pruned():
    t = Topology(); r = AU.Recorder()
    sw = t.add_device("switch", "S1"); r.capture(t)
    h = t.add_device("host", "H1"); r.capture(t)
    t.remove_device(sw.id); r.capture(t)                # teacher removes the switch
    keys = {s["key"] for s in r.result()}
    assert "place:switch" in keys                       # the step SURVIVES — prune by hand


def test_recording_can_continue_from_existing_steps():
    """Re-recording keeps prior steps and only appends genuinely new facts (no dupes)."""
    t = Topology()
    sw = t.add_device("switch", "S1")
    r = AU.Recorder()
    r.capture(t)                                        # first pass: the switch
    # a fresh recorder seeded with the prior step, then a new action
    r2 = AU.Recorder()
    r2.steps = list(r.result())
    r2._by_key = {s["key"]: s for s in r2.steps}
    h = t.add_device("host", "H1"); r2.capture(t)
    checks = [s["check"] for s in r2.result()]
    assert checks == ["exists(switch)", "exists(host)"]   # kept the switch, added the host once


def test_a_live_check_is_a_probe_objective():
    ok = AU.live_check("web_app", "database", True)
    assert ok["kind"] == "behavioral" and ok["level"] == 4
    assert ok["probe"] == "reach(web_app -> database) == ok"
    blocked = AU.live_check("cloud", "database", False)
    assert blocked["probe"] == "reach(cloud -> database) == fail"


def test_a_recorded_fragment_saves_and_grades():
    """End-to-end: record a LAN, build the fragment, and confirm it's gradable + loads."""
    from gini.domain import fragments as F
    t = Topology(); r = AU.Recorder()
    sw = t.add_device("switch", "S1"); r.capture(t)
    h1 = t.add_device("host", "H1"); r.capture(t)
    t.add_link(h1.id, sw.id); r.capture(t)

    d = AU.build_fragment_dict(frag_id="t-recorded", teaches="networking-basics",
                               summary="a recorded LAN", spirit="",
                               objectives=r.result(), author="prof")
    assert AU.validate_dict(d) == []
    AU.save_fragment(d)
    F.reload()
    f = F.get("t-recorded")
    assert f is not None and len(f.objectives) == 3


def test_fragment_manager_is_non_modal_and_records_while_the_canvas_is_live():
    """A modal dialog would swallow canvas drag-and-drop, so recording would be impossible. The FM is
    non-modal: build on the canvas WITH it open and the steps are captured."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w._fragment_manager()
    fm = w._frag_mgr
    assert not fm.isModal() and fm.windowModality() == Qt.NonModal

    fm._create()
    fm.record_btn.setChecked(True)
    w.ctx.add_device("switch", 10, 10)          # a canvas action while the FM floats
    w.ctx.add_device("host", 20, 10)
    fm.record_btn.setChecked(False)
    assert [s["check"] for s in fm._steps] == ["exists(switch)", "exists(host)"]

    w._fragment_manager()                        # reopening focuses the same instance, no duplicate
    assert w._frag_mgr is fm

    fm.close()                                   # closing mid-nothing must not leave a live connection
    w.ctx.add_device("router", 30, 30)           # would fire _on_change if still connected → no crash


def test_add_live_check_is_confined_to_canvas_elements():
    """A live check is a condition on the FRAGMENT'S OWN elements — the source/destination lists must
    show only what's on the canvas, not all 43 element kinds."""
    import os, tempfile
    os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    from gini.domain import devices as D
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    from gini.ui.fragment_manager import FragmentManager
    fm = FragmentManager(w, w.ctx, author="prof"); fm._create()

    assert fm._canvas_types() == []                       # empty board → nothing to check
    w.ctx.add_device("web_app", 10, 10)
    w.ctx.add_device("database", 20, 10)
    w.ctx.add_device("database", 30, 10)                  # a second of the same type
    assert fm._canvas_types() == ["database", "web_app"]  # DISTINCT canvas types only…
    assert len(fm._canvas_types()) < len(D.all_devices()) # …not the whole palette


def test_fragment_ids_are_slugged_so_they_survive_url_routes():
    """Ids travel in routes matched by [\\w-]+; a space or '/' would 404. 'Simple LAN' -> 'simple-lan'."""
    from gini.domain.authoring import slug
    assert slug("Simple LAN") == "simple-lan"
    assert slug("Private DB / Web") == "private-db-web"
    assert slug("  R1—the router ") == "r1-the-router"
    assert slug("!!!") == "fragment"                 # never empty


def test_per_row_reorder_relevel_delete():
    """The Fragment Manager edits steps inline: ▲▼ reorder, click level to cycle, ✕ delete."""
    import os, tempfile
    os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    from gini.ui.fragment_manager import FragmentManager
    fm = FragmentManager(w, w.ctx, author="prof"); fm._create()
    fm._steps = [{"id": "a", "say": "A", "check": "exists(host)", "level": 1},
                 {"id": "b", "say": "B", "check": "exists(switch)", "level": 1}]
    fm._render_steps()

    fm._move(0, 1)                                    # move A down
    assert [s["id"] for s in fm._steps] == ["b", "a"]
    fm._cycle_level(0)                               # L1 → L2
    assert fm._steps[0]["level"] == 2
    fm._cycle_level(0); fm._cycle_level(0); fm._cycle_level(0)   # L2→L3→L4→L1 (wraps)
    assert fm._steps[0]["level"] == 1
    fm._del(1)                                       # delete A
    assert [s["id"] for s in fm._steps] == ["b"]

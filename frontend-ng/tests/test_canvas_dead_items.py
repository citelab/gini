"""A deleted Qt item must not take a bus handler down with it.

Seen in the wild as a bare traceback on the console:

    File ".../ui/canvas.py", line 1205, in _on_clear_stage
        node.setOpacity(1.0)
    RuntimeError: libshiboken: Internal C++ object (NodeItem) already deleted.

Python dict membership and Qt object lifetime are INDEPENDENT. `scene.nodes` holds wrappers;
Qt can destroy the underlying items on its own schedule (a scene clear, a parent going away,
deleteLater), and the wrapper stays in the dict looking perfectly fine until something touches it.

WHAT THIS IS AND IS NOT. The path that orphans an item has not been reproduced — every deletion
route in the scene clears its dict correctly, and no exercise of add/remove/spotlight/callout/
project-switch reproduces it. So this is a lifetime GUARD, not a root-cause fix.

What it does guarantee is the user-visible damage. PySide6 does not abort an emit when a slot
raises: it prints the traceback and carries on to the other slots, so nothing else listening to
present_clear was affected. The harm is inside _on_clear_stage, which dies partway through — the
tutor's callout is never removed and the edges keep their dimmed opacity, so clicking empty space
stops dismissing the overlay. Pruning makes the state converge, and the logged message names the
element so the next occurrence hands us the path.

The tests below build the stale state directly with scene.clear() — which is exactly what Qt does
to those items — rather than pretending to know how it happens in the field.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _win(app):
    from gini.ui import MainWindow
    return MainWindow(app)


def _orphan_everything(scene):
    """Delete the C++ items but leave the Python dicts populated — the broken state itself."""
    scene.clear()                      # QGraphicsScene::clear() destroys every item it owns
    assert scene.nodes, "nothing was being tracked; the test would prove nothing"


def test_the_broken_state_really_is_broken(app):
    """Guard the guard: if scene.clear() ever stops deleting the items, every test below would
    pass without exercising anything."""
    w = _win(app)
    w.api.add_device("router", x=0, y=0)
    _orphan_everything(w.canvas.scene_)
    node = next(iter(w.canvas.scene_.nodes.values()))
    with pytest.raises(RuntimeError):
        node.setOpacity(1.0)


def test_clearing_the_stage_survives_deleted_items(app):
    """The reported crash, exactly: present_clear with dead nodes still tracked."""
    w = _win(app)
    w.api.add_device("router", x=0, y=0)
    for i in range(3):
        w.api.add_device("host", x=40 * i, y=60)
    _orphan_everything(w.canvas.scene_)
    w.ctx.bus.present_clear.emit()               # must not raise


def test_the_dead_entries_are_dropped_not_merely_skipped(app):
    """Skipping would leave the scene permanently lying about what it holds, and every later
    operation would have to defend itself too."""
    w = _win(app)
    w.api.add_device("router", x=0, y=0)
    w.api.add_device("host", x=40, y=40)
    _orphan_everything(w.canvas.scene_)
    w.ctx.bus.present_clear.emit()
    assert not w.canvas.scene_.nodes, "dead nodes are still tracked"
    assert not w.canvas.scene_.edges
    assert not w.canvas.scene_.groups


def test_it_says_which_element_it_dropped(app):
    """The message is the whole point of the guard: it is the evidence for finding the real path.
    Silent recovery would make this permanently undiagnosable."""
    w = _win(app)
    d = w.api.add_device("router", x=0, y=0)
    logs = []
    w.ctx.bus.log.connect(lambda lvl, msg: logs.append((lvl, msg)))
    _orphan_everything(w.canvas.scene_)
    w.ctx.bus.present_clear.emit()
    dropped = [m for lvl, m in logs if "dropped a deleted" in m]
    assert dropped, f"recovered silently; logs were {logs}"
    assert "node" in dropped[0]


def test_one_dead_node_does_not_strand_the_rest_of_the_clear(app):
    """The actual user-visible damage, and the reason a bare traceback was not the whole story.

    PySide6 does NOT abort an emit when a slot raises — it prints the traceback and carries on to
    the other slots, so nothing else connected to present_clear was harmed. (An earlier version of
    this file claimed otherwise and had a test that passed with the guard removed; mutation
    testing caught it.) What DOES break is the remainder of _on_clear_stage itself: the exception
    lands partway through, so the callouts are never removed and the edges keep their dimmed
    opacity. The tutor's bubble stays on screen and clicking empty space will not dismiss it.

    One item is deleted precisely, leaving everything else live — the mixed state that a whole
    scene.clear() cannot express.
    """
    from shiboken6 import delete

    w = _win(app)
    r = w.api.add_device("router", x=0, y=0)
    w.api.add_device("host", x=60, y=60)
    sc = w.canvas.scene_
    ids = list(w.ctx.topology.devices)
    for a, b in zip(ids, ids[1:]):
        w.ctx.topology.add_link(a, b)
    w.ctx.bus.present_callout.emit(ids[0], "a bubble that must go away")
    assert sc._callouts, "no callout to strand"
    for e in sc.edges.values():
        e.setOpacity(0.2)

    victim = ids[-1]
    delete(sc.nodes[victim])                       # exactly one dead item, the rest untouched

    w.ctx.bus.present_clear.emit()

    assert not sc._callouts, "the callout was stranded on screen by the dead node"
    assert all(e.opacity() == 1.0 for e in sc.edges.values()), "edges left dimmed"
    assert victim not in sc.nodes


def test_a_healthy_scene_is_left_alone(app):
    """The guard must not become a way to quietly lose live nodes."""
    w = _win(app)
    w.api.add_device("router", x=0, y=0)
    w.api.add_device("host", x=40, y=40)
    before = set(w.canvas.scene_.nodes)
    logs = []
    w.ctx.bus.log.connect(lambda lvl, msg: logs.append(msg))
    w.ctx.bus.present_clear.emit()
    assert set(w.canvas.scene_.nodes) == before, "pruned a live node"
    assert not [m for m in logs if "dropped a deleted" in m], "cried wolf on a healthy scene"


def test_no_bus_signal_the_canvas_listens_to_raises_on_dead_items(app):
    """The general form of the bug, and the reason the failing test kept MOVING.

    `_on_clear_stage` was guarded; `_refresh_node_labels`, `_on_addressing` and `_on_warnings`
    swept the same dicts unguarded and raised out of the Qt event loop. Because those exceptions
    surface wherever the loop next spins, pytest-qt attributed them to whichever test happened to
    be running — so the suite failed in a different place depending on ordering, and passed
    entirely where pytest-qt was absent.

    Testing the three known handlers would just re-fix today's bug. This walks EVERY bus signal
    the scene subscribes to, so a handler added next year is covered without anyone remembering
    this file exists.
    """
    w = _win(app)
    w.api.add_device("router", x=0, y=0)
    w.api.add_device("host", x=60, y=60)
    ids = list(w.ctx.topology.devices)
    w.ctx.topology.add_link(*ids[:2])
    sc = w.canvas.scene_
    bus = w.ctx.bus

    # A plausible argument for each signal, by name — no signal is skipped silently.
    args = {
        "device_added": (ids[0],), "device_removed": (ids[0],), "device_changed": (ids[0],),
        "link_added": (next(iter(w.ctx.topology.links)),),
        "link_removed": (next(iter(w.ctx.topology.links)),),
        "present_spotlight": (ids,), "present_highlight": (ids,),
        "present_callout": (ids[0], "text"), "present_packet": (ids[0], ids[1]),
        "wizard_ghosts_requested": ("goal",), "wizard_ghosts_ready": ({},),
    }

    connected = [n for n in dir(bus) if not n.startswith("_")
                 and f"bus.{n}.connect" in _canvas_source()]
    assert len(connected) > 8, f"only found {connected}; the scan is not finding the connections"

    _orphan_everything(sc)
    failures = []
    for name in connected:
        signal = getattr(bus, name)
        for a in (args.get(name, ()), ()):          # try the typed argument, then no-arg
            try:
                signal.emit(*a)
                break
            except (TypeError, ValueError):
                continue                            # wrong arity for this signal; try the other
            except RuntimeError as e:
                failures.append(f"{name}: {e}")
                break
    assert not failures, "bus handlers raised on deleted items:\n  " + "\n  ".join(failures)


def _canvas_source() -> str:
    from pathlib import Path
    import gini.ui.canvas as c
    return Path(c.__file__).read_text(encoding="utf-8")

"""Link failures in the app: the clock fires, every end is told, and everyone can see it (Phase 3).

Driven through the real MainWindow with the console replaced by a recorder, so these check what
gBuilder SENDS -- `ifconfig down tunN` on a router, `link <id> down` to a switch and a machine --
and what it SHOWS: the canvas, the inspector's Fail now / Restore, the log and the proof chain.
The packets themselves are tested against real routers (failure_test.py) and in a real Run
(test_live_link_cost.py).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

from PySide6.QtWidgets import QApplication, QPushButton

from gini.domain import link_props as LP
from gini.domain.topology import Topology
from gini.services import link_push as LPU


def _topology():
    t = Topology("fail")
    r1, r2 = t.add_device("router", "R1"), t.add_device("router", "R2")
    s, m = t.add_device("switch", "S1"), t.add_device("host", "M1")
    rr, rs, sm = t.add_link(r1.id, r2.id), t.add_link(r2.id, s.id), t.add_link(m.id, s.id)
    return t, rr, rs, sm


def _window(t, now=0.0):
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w._set_topology(t)
    cfg = w._gloader.compile(w.ctx.topology)
    w._run_cfg = cfg
    w._run_links = (LPU.structure(w.ctx.topology), LPU.link_costs(w.ctx.topology),
                    LPU.installed_routes(cfg), True)
    w._running, w._workdir = True, "/nonexistent"
    w.canvas.scene_.running = True
    w.inspector.set_live_running(True)
    sent = []
    w._link_cmd = lambda kind, element, cmd: (sent.append((kind, element, cmd)), "")[1]
    w.element_query = lambda name, cmd: (sent.append(("query", name, cmd)), "")[1]
    logs = []
    w.ctx.bus.log.connect(lambda lvl, msg: logs.append(msg))
    return app, w, sent, logs


def _settle(app, sent=None, n=0, tries=200):
    for _ in range(tries):
        app.processEvents()
        if sent is not None and len(sent) >= n:
            break
        time.sleep(0.01)
    app.processEvents()


def test_a_random_failure_cuts_the_link_at_every_end():
    t, rr, rs, sm = _topology()
    LP.set_prop(sm, "fail_after", 30)
    app, w, sent, logs = _window(t)
    w._start_link_faults()
    ev = w._faults.next_event()
    w._fault_t0 = time.monotonic() - ev.at - 0.01            # as if that much time had passed
    w._fault_tick()
    _settle(app, sent, 2)
    assert ("fabric", "S1", f"link {sm.id} down") in sent
    assert ("machine", "M1", f"link {sm.id} down") in sent
    assert sm.id in w.ctx.failed_links
    assert any("M1 ↔ S1 FAILED (random, mean 30 s)" in m for m in logs)
    w._stop_link_faults()


def test_fail_now_and_restore_on_a_router_link():
    t, rr, rs, sm = _topology()
    app, w, sent, _logs = _window(t)
    w._start_link_faults()
    w.api.fail_link("R1-R2")
    _settle(app, sent, 2)
    assert sorted(sent) == [("router", "R1", "ifconfig down tun1"),
                            ("router", "R2", "ifconfig down tun1")]
    sent.clear()
    w.api.fail_link("R1-R2")                                 # already down: nothing to do
    _settle(app, tries=20)
    assert sent == []
    w.api.restore_link("R1-R2")
    _settle(app, sent, 2)
    assert sorted(sent) == [("router", "R1", "ifconfig up tun1"),
                            ("router", "R2", "ifconfig up tun1")]
    assert rr.id not in w.ctx.failed_links
    w._stop_link_faults()


def test_a_failure_does_not_touch_static_routes():
    """What static routing does on a failure is nothing -- that is the lesson. Only the interfaces
    go down; no route add or del is sent."""
    t, rr, rs, sm = _topology()
    app, w, sent, _logs = _window(t)
    w._start_link_faults()
    w.api.fail_link(rr.id)
    _settle(app, sent, 2)
    assert not any("route" in c for _k, _e, c in sent)
    w._stop_link_faults()


def test_the_canvas_and_inspector_show_a_failed_link():
    t, rr, rs, sm = _topology()
    app, w, sent, _logs = _window(t)
    w._start_link_faults()
    sc = w.canvas.scene_
    sc.clearSelection()
    sc.edges[rr.id].setSelected(True)
    _settle(app)
    btn = w.inspector.findChild(QPushButton, "LinkFailButton")
    assert btn is not None and btn.text() == "Fail now"
    btn.click()
    _settle(app, sent, 2)
    assert rr.id in sc.failed_links and sc.edges[rr.id]._label.startswith("✕")
    assert "DOWN" in sc.edges[rr.id].toolTip()
    btn = w.inspector.findChild(QPushButton, "LinkFailButton")
    assert btn.text() == "Restore"
    btn.click()
    _settle(app, sent, 4)
    assert rr.id not in sc.failed_links and not sc.edges[rr.id]._label.startswith("✕")
    w._stop_link_faults()


def test_stopping_the_lab_forgets_failures():
    t, rr, rs, sm = _topology()
    app, w, sent, _logs = _window(t)
    w._start_link_faults()
    w.api.fail_link(rr.id)
    _settle(app, sent, 2)
    w._stop_link_faults()
    assert not w.ctx.failed_links and not w.canvas.scene_.failed_links
    assert not w._fault_timer.isActive()


def test_a_request_while_not_running_is_refused_in_words():
    t, rr, rs, sm = _topology()
    app, w, sent, logs = _window(t)
    w._running = False
    w.api.fail_link(rr.id)
    _settle(app, tries=20)
    assert sent == [] and any("press Run first" in m for m in logs)


def test_failures_are_recorded_in_the_proof_chain(tmp_path):
    from gini.domain import narration
    from gini.domain import proof as P
    from gini.domain import proof_events as ev
    from gini.services.proof_recorder import ProofRecorder
    from test_proof_events import CODE, FakeCtx
    ctx = FakeCtx()
    rec = ProofRecorder(ctx, store=P.ChainStore(tmp_path))
    rec.attach()
    rec.arm(CODE)
    a, b = ctx.add_device("router"), ctx.add_device("router")
    link = ctx.add_link(a, b)
    ctx.bus.link_state_changed.emit(link.id, False, "random")
    ctx.bus.link_state_changed.emit(link.id, True, "manual")
    down, up = rec._chain.entries[-2], rec._chain.entries[-1]
    assert down.kind == up.kind == ev.OBSERVE
    assert narration.describe(down) == f"GINI observed on {a.name} ↔ {b.name}: link failed at random"
    assert narration.describe(up).endswith("link repaired by hand")


def test_a_pinned_seed_is_kept_with_the_lab():
    t, *_ = _topology()
    app, w, _sent, logs = _window(t)
    w.api.set_failure_seed(4242)
    assert w.ctx.topology.to_dict()["failure_seed"] == 4242
    LP.set_prop(next(iter(w.ctx.topology.links.values())), "fail_after", 60)
    w._start_link_faults()
    assert w._faults.seed == 4242
    assert any("seed 4242" in m for m in logs)
    w._stop_link_faults()

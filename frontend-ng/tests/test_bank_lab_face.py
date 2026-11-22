"""T-Lab 03's window has to fit 940 × 700 — with everything still on it.

That is the size T-Lab 02 and the Machine Lab's own pages open at. The first half of this file
asserts the minimum size the face demands on every level, start and version, and that the
controls a student needs are on the screen and inside the window. The rest walks the lab through
the face's own buttons — the widgets a student would press — rather than through the session, so
a handler wired to the wrong slot is caught here.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from gini.domain import banklab as B
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QDialog

from gini.ui import bank_lab as V
from gini.ui.theme import ThemeManager

W, H = 940, 700


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def events():
    return []


@pytest.fixture
def face(app, events):
    w = V.BankLab(None, ThemeManager(app, "Light"), on_event=lambda k, p: events.append((k, p)))
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def _level(face, level_id: str, start: str = "", version: str = "") -> B.Level:
    idx = next(i for i, lv in enumerate(face.session.lab.levels) if lv.id == level_id)
    face._level_btns[idx].click()
    if start:
        face._start_btns[face.session.level.start(start)].click()
    if version:
        face._version_btns[version].click()
    QApplication.processEvents()
    return face.session.level


def _press(face, pn: str, kind: str) -> None:
    p = face.session.world.procs.index(pn)
    btn = face._proc_btns.get((p, kind)) or face._recover_btns.get((p, kind))
    assert btn is not None, f"no button for {pn}/{kind}"
    btn.click()
    QApplication.processEvents()


def _inside(face, w) -> bool:
    p = w.mapTo(face, QPoint(0, 0))
    return (w.isVisible() and p.x() >= 0 and p.y() >= 0
            and p.x() + w.width() <= face.width() and p.y() + w.height() <= face.height())


# ---- it fits --------------------------------------------------------------------- #
def test_the_face_is_a_window_that_opens_at_940_by_700(face):
    assert isinstance(face, QDialog)
    assert (face.width(), face.height()) == (W, H)
    assert face.windowTitle() == "T-Lab 03 — Simulate deadlock handling"


def test_the_face_fits_940_by_700_on_every_level_start_and_version(face):
    for i, lv in enumerate(face.session.lab.levels):
        face._level_btns[i].click()
        for s in range(len(lv.starts)):
            if face._start_btns:
                face._start_btns[s].click()
            for vid in [v.id for v in lv.versions] or [""]:
                if vid and vid in face._version_btns:
                    face._version_btns[vid].click()
                for tid in list(face._tab_btns):
                    face._tab_btns[tid].click()
                    QApplication.processEvents()
                    hint = face.minimumSizeHint()
                    where = f"{lv.id}/{lv.starts[s].label}/{vid}/{tid}"
                    assert hint.width() <= W, f"{where} demands {hint.width()} px of width"
                    assert hint.height() <= H, f"{where} demands {hint.height()} px of height"


def test_every_process_row_is_in_view_without_scrolling(face):
    """Figure 14.9 opened with P4's row cut off at the bottom of its card: the card was squeezed
    below its own minimum while the graph took the spare height. Up to five rows must all show."""
    for i, lv in enumerate(face.session.lab.levels):
        face._level_btns[i].click()
        if not face._pills:                         # the bank level has no process rows
            continue
        for s in range(len(lv.starts)):
            face._start_btns[s].click()
            QApplication.processEvents()
            card = face._procs_card
            for name, pill in list(face._pills.items())[:5]:
                bottom = pill.mapTo(card, QPoint(0, pill.height())).y()
                assert bottom <= card.height(), (
                    f"{lv.id}/{lv.starts[s].label}: {name}'s row is cut off")


def test_nothing_has_left_the_screen_on_level_5_with_three_ways_out(face):
    _level(face, "recover", "Yours: three ways out")
    face._tab_btns["recover"].click()
    face.resize(W, H)
    QApplication.processEvents()
    for name in ("_undo", "_reset", "_random", "_explore", "_hide_graph", "_graph",
                 "_starts_box"):
        assert _inside(face, getattr(face, name)), f"{name} is not on the screen"
    for b in (face._start_btns + face._level_btns + list(face._tab_btns.values())
              + list(face._view_btns.values())):
        assert _inside(face, b), f"{b.text()!r} is not on the screen"
    assert [b.text() for b in face._tab_btns.values()] == ["Tables", "Reduce", "Recover",
                                                          "Every order"]


def test_nothing_has_left_the_screen_on_the_bank_level(face):
    _level(face, "banker", "Worked example 14.7")
    face._req_btns[1].click()
    QApplication.processEvents()
    for b in (face._req_btns[:3] + face._try_btns + list(face._decide_btns.values())
              + [face._stuck_btn]):
        assert _inside(face, b), f"{b.text()!r} is not on the screen"
    assert not face._random.isVisible(), "the banker has no Random"


# ---- every level, every start ------------------------------------------------------- #
def test_each_level_opens_from_its_button(face):
    for i, lv in enumerate(face.session.lab.levels):
        face._level_btns[i].click()
        assert face.session.level is lv
        assert face._level_btns[i].isChecked()
        assert face._level_btns[i].toolTip() == f"Level {lv.n}: {lv.title}"
        assert face._left.currentIndex() == (1 if lv.mode == "bank" else 0)
        assert set(face._chips) == {g.id for g in lv.goals}


def test_each_start_loads_from_its_button(face):
    for i, lv in enumerate(face.session.lab.levels):
        face._level_btns[i].click()
        if len(lv.starts) == 1:
            assert not face._starts_box.isVisible()
            continue
        assert [b.text() for b in face._start_btns] == [s.label for s in lv.starts]
        for s in range(len(lv.starts)):
            face._start_btns[s].click()
            assert face.session.start == s and face._start_btns[s].isChecked()


def test_the_version_buttons_are_only_on_level_2(face):
    for i, lv in enumerate(face.session.lab.levels):
        face._level_btns[i].click()
        want = ["Grant when free", "Graph algorithm"] if lv.id == "claims" else []
        assert [b.text() for b in face._version_btns.values()] == want


# ---- goals, through the buttons -------------------------------------------------------- #
def test_a_goal_chip_turns_and_sends_exactly_one_entry(face, events):
    _level(face, "graph")
    assert face._chips["deadlock"].text() == "○ Deadlock"
    _press(face, "P3", "step")
    assert face._chips["deadlock"].text() == "✓ Deadlock"
    assert [(k, p["goal"]) for k, p in events] == [("allocate", "deadlock")]
    p = events[0][1]
    assert {k: p[k] for k in ("lab", "level", "version", "start", "moves", "result")} == {
        "lab": "t-lab-03", "level": "graph", "version": "free", "start": 0,
        "moves": [[2, "step"]], "result": "deadlock: P1, P2, P3"}
    face._undo.click()
    _press(face, "P3", "step")
    # Told again — the recorder keeps one per recording (test_tlab_transcript).
    assert {(k, p["goal"]) for k, p in events} == {("allocate", "deadlock")}
    assert face._trace_end.text() == "Deadlock: nobody can move. P1, P2, P3 wait forever."


def test_try_every_order_on_the_graph_algorithm_shows_proved(face, events):
    _level(face, "claims", version="graph")
    face._explore.click()
    QApplication.processEvents()
    assert face._tab_btns["every"].isChecked()
    assert face._verdict is not None
    assert face._verdict.text() == ("Proved: counting claim edges, no order of requests ends "
                                    "in deadlock.")
    assert face._chips["proof"].text() == "✓ Proof: never stuck"
    kind, p = events[-1]
    assert kind == "orders" and p["ok"] is True
    assert {"lab", "level", "version", "start", "ok", "reason", "summary"} <= set(p)


def test_show_me_the_shortest_loads_the_order(face):
    _level(face, "claims", version="free")
    face._explore.click()
    labels = [r.label for r in V.orders_view(face.session.version,
                                             face.session.exploration()).rows]
    face._show_btns[labels.index("deadlock: P1, P2, P3")].click()
    assert len(face.session.moves) == 6
    assert len(face._trace_lines) == 6
    assert face._chips["deadlock"].text() == "✓ Deadlock"
    assert face._verdict is None, "Grant when free proves nothing, so no verdict box"


def test_a_refused_request_says_so_on_its_pill(face):
    _level(face, "claims", version="graph")
    for pn in ("P1", "P2", "P3"):
        _press(face, pn, "step")
    assert face._pills["P3"].text() == "waits: would close a cycle"
    assert face._chips["refused"].text() == "✓ Wait, though free"


def test_the_recover_buttons_break_the_deadlock(face, events):
    _level(face, "recover", "Worked example 14.8")
    face._tab_btns["recover"].click()
    assert [b.text() for b in face._recover_btns.values()] == [
        "Terminate · 20 min lost", "Terminate · 10 min lost", "Terminate · 2 h lost",
        "Terminate · 30 min lost"]
    _press(face, "P2", "kill")
    assert face.session.lines()[0].startswith("P2 is terminated")
    assert face._pills["P2"].text() == "terminated"
    assert not events, "the goals of level 5 are set on the Yours starts"


def test_the_graph_can_be_hidden_and_switched_to_the_wait_for_graph(face):
    _level(face, "recover", "Figure 14.9")
    assert face._view_btns["wf"].isVisible()
    face._view_btns["wf"].click()
    assert face._graph.view().wait_for
    face._hide_graph.click()
    assert face._graph_stack.currentIndex() == 1
    assert face._hide_graph.text() == "Show the graph"
    assert not face._view_btns["wf"].isVisible()
    face._hide_graph.click()
    assert face._graph_stack.currentIndex() == 0
    _level(face, "recover", "Worked example 14.8")
    assert not face._view_btns["wf"].isVisible(), "A has seven units: no wait-for graph"
    assert not face._graph.view().wait_for


def test_the_reduce_tab_numbers_the_graph(face):
    _level(face, "graph", "Figure 14.3")
    face._tab_btns["reduce"].click()
    assert {n.name: n.erased for n in face._graph.view().nodes if n.proc} == {
        "P2": 0, "P1": 1, "P3": 2, "P4": 3}


# ---- undo, reset, random ----------------------------------------------------------------- #
def test_undo_reset_and_random_on_a_run_level(face):
    _level(face, "lucky")
    assert not face._undo.isEnabled() and not face._reset.isEnabled()
    _press(face, "P1", "step")
    _press(face, "P2", "step")
    assert face._undo.isEnabled()
    face._undo.click()
    assert face.session.moves == ((0, "step"),) and len(face._trace_lines) == 1
    face._reset.click()
    assert face.session.moves == () and not face._reset.isEnabled()
    face._random.click()
    assert face.session.moves and face._trace_end is not None
    assert not face._random.isEnabled(), "the run finished or stuck"


# ---- the bank level ------------------------------------------------------------------------ #
def test_the_bank_check_refuses_nobody_fits_while_a_process_still_fits(face):
    _level(face, "banker", "Worked example 14.7")
    face._req_btns[0].click()                  # No request
    face._stuck_btn.click()
    assert face._check_msg is not None
    assert face._check_msg.text() == "Not yet: P2 still fits in Work = (1, 2, 2)."
    face._try_btns[1].click()
    assert [r.name for r in face._check_rows] == ["P2"] and face._check_rows[0].fits
    assert not face._try_btns[1].isEnabled(), "P2 has finished in this check"


def test_the_banker_reaches_wait_through_the_buttons(face, events):
    _level(face, "banker", "Yours")
    assert face._bank_cells[("P1", "Need", 0)].text() == "?"
    assert face._avail.text() == "Avail = ?"
    face._need_btn.click()
    assert face._bank_cells[("P1", "Need", 0)].text() == "3"
    face._req_btns[2].click()                  # P3 requests (1, 0, 1)
    assert face._bank_cells[("P3", "Hold", 0)].text() == "2 → 3"
    for b in list(face._try_btns):
        b.click()
    face._stuck_btn.click()
    face._decide_btns["wait"].click()
    assert face._bank_verdict.text() == "Right: the state would be unsafe."
    assert face._chips["wait"].text() == "✓ Wait"
    kind, p = events[-1]
    assert kind == "bank" and p["goal"] == "wait" and p["decision"] == "wait"
    assert p["request"] == (2, (1, 0, 1)) and len(p["rows"]) == 4
    face._undo.click()
    assert face._bank_verdict is None and face.session.work.stuck
    face._reset.click()
    assert face.session.peek_work().sel is None


def test_try_every_order_on_the_bank_level_lists_the_safe_sequences(face):
    _level(face, "banker", "Worked example 14.7")
    face._explore.click()
    assert face._tab_btns["every"].isChecked()
    texts = [face._side_col.itemAt(i).widget().text()
             for i in range(face._side_col.count()) if face._side_col.itemAt(i).widget()]
    assert any("⟨P2, P3, P1, P4⟩" in t for t in texts)


# ---- opening it ---------------------------------------------------------------------------- #
def test_the_hub_opens_this_window_for_a_bank_lab():
    from gini.ui.user_code_lab import tlab_window
    assert tlab_window("bank") is V.BankLab


def test_the_lab_opens_with_no_provider_and_no_container(app):
    w = V.open_bank_lab(None, ThemeManager(app, "Light"))
    try:
        assert w.isVisible()
    finally:
        w.close()


def test_the_window_names_the_machine_it_was_opened_from(app):
    class _Dev:
        name = "M1"
    w = V.BankLab(None, ThemeManager(app, "Light"), device=_Dev())
    try:
        assert w.windowTitle().endswith(" — M1")
    finally:
        w.close()


def test_every_theme_can_draw_it(app):
    from gini.ui.theme import THEMES
    for name in THEMES:
        w = V.BankLab(None, ThemeManager(app, name))
        try:
            w.show()
            w._level_btns[1].click()
            w._version_btns["graph"].click()
            w._explore.click()
            w._level_btns[2].click()
            QApplication.processEvents()
            w.grab()
        finally:
            w.close()


def test_every_level_leaves_room_for_wider_fonts(face):
    """Fitting 940 px exactly is not enough: Linux's fonts are about 3% wider than the Mac's (T-Lab
    04 fit here and asked CI for 949 px). Every level and tab must leave 4% spare."""
    for i in range(len(face.session.lab.levels)):
        face._level_btns[i].click()
        for name in list(face._tab_btns):
            face._tab_btns[name].click()
            QApplication.processEvents()
            need = face.minimumSizeHint().width()
            assert need <= W / 1.04, f"level {i + 1}/{name} needs {need} px of {W}"

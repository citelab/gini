"""T-Lab 04's window has to fit 940 × 700 — with everything still on it.

That is the size the Machine Lab's pages and T-Lab 02 open at. The first half of this file asserts
the minimum size the face demands on every level, start and tab, after every Try every, and that
nothing has been pushed off: the header buttons, the starts, the tabs, and each level's own
controls are visible and inside the window.

The rest walks the lab through the face's own handlers and widgets — a mouse click on the painted
disk map and on the painted i-node, the R and W buttons, the rows of the Table tab — rather than
through the session, so that a handler wired to the wrong slot is caught here.
"""
from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from gini.domain import fslab as F
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from gini.ui import fs_lab as V
from gini.ui.theme import ThemeManager

W, H = 940, 700
MAIN = [2, 40, 3, 41, 4, 42, 6, 55, 8]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def events():
    return []


@pytest.fixture
def face(app, events):
    w = V.FsLab(None, ThemeManager(app, "Light"), on_event=lambda k, p: events.append((k, p)))
    w.session.rng = random.Random(11)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def _level(face, lid: str) -> F.Level:
    idx = next(i for i, lv in enumerate(face.session.lab.levels) if lv.id == lid)
    face._level_btns[idx].click()
    QApplication.processEvents()
    return face.session.level


def _start(face, label: str) -> None:
    i = next(i for i, s in enumerate(face.session.level.starts) if s.label == label)
    face._start_btns[i].click()
    QApplication.processEvents()


def _inside(face, w) -> bool:
    p = w.mapTo(face, QPoint(0, 0))
    return (w.isVisible() and p.x() >= 0 and p.y() >= 0
            and p.x() + w.width() <= face.width() and p.y() + w.height() <= face.height())


def _fits(face, what: str) -> None:
    QApplication.processEvents()
    hint = face.minimumSizeHint()
    assert hint.width() <= W, f"{what} demands {hint.width()} px of width"
    assert hint.height() <= H, f"{what} demands {hint.height()} px of height"


# ---- it fits --------------------------------------------------------------------- #
def test_the_face_opens_at_940_by_700_on_level_1(face):
    assert (face.width(), face.height()) == (W, H)
    assert face.session.level.id == "walk"
    assert face._stack.currentIndex() == 0
    assert face.windowTitle() == "T-Lab 04 — Simulate a file system"


def test_the_face_fits_on_every_level_start_and_tab_with_try_every_shown(face):
    for i, lv in enumerate(face.session.lab.levels):
        face._level_btns[i].click()
        for j in range(len(lv.starts)):
            face._start_btns[j].click()
            face._every.click()
            for name in list(face._tab_btns):
                face._tab_btns[name].click()
                _fits(face, f"{lv.id}/{lv.starts[j].label}/{name}")


def test_nothing_has_left_the_screen_on_any_level(face):
    common = ("_undo", "_reset", "_random", "_every", "_starts_box")
    per_level = {
        "walk": ("_path_box", "_walk_show", "_walk_watch", "_walk_map", "_walk_inside"),
        "byte": ("_off_edit", "_eof_btn", "_byte_show", "_ptr_view"),
        "make": ("_mk_n", "_mk_off", "_mk_write", "_make_ptr", "_make_stats"),
        "append": ("_op_box", "_ap_n", "_strip", "_ap_map", "_allin", "_ap_show", "_ap_watch"),
    }
    for lid, names in per_level.items():
        _level(face, lid)
        face.resize(W, H)
        QApplication.processEvents()
        for name in common + names:
            assert _inside(face, getattr(face, name)), f"{lid}: {name} is not on the screen"
        for b in face._level_btns + face._start_btns + list(face._tab_btns.values()):
            assert _inside(face, b), f"{lid}: {b.text()!r} is not on the screen"
    face._on_ap_block(54)           # the R and W buttons appear once a block is picked
    QApplication.processEvents()
    assert _inside(face, face._r_btn) and _inside(face, face._w_btn)


def test_the_disk_map_draws_twenty_blocks_a_row_and_fits_its_card(face):
    m = face._walk_map
    assert m.blocks() == 80
    assert m.cell_rect(20).y() > m.cell_rect(19).y()
    assert m.cell_rect(19).right() <= m.width()
    assert m.block_at(m.cell_rect(42).center().x(), m.cell_rect(42).center().y()) == 42


# ---- level 1 --------------------------------------------------------------------- #
def _click_block(m, b: int) -> None:
    c = m.cell_rect(b).center()
    QTest.mouseClick(m, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
    QApplication.processEvents()


def test_main_c_walked_with_the_mouse_turns_its_chip_and_tells_the_chain_once(face, events):
    for b in MAIN:
        _click_block(face._walk_map, b)
    assert face._chips["main"].text() == "✓ main.c in order"
    assert len(events) == 1
    kind, p = events[0]
    assert kind == "fs_walk"
    assert {k: p[k] for k in ("lab", "level", "start", "seed", "path", "clicks", "goal",
                              "hints")} == {
        "lab": "t-lab-04", "level": "walk", "start": "Tutorial", "seed": None,
        "path": "/users/raj/lab/main.c", "clicks": MAIN, "goal": "main", "hints": 0}
    assert face._walk_count.text() == "9 R"
    assert face._walk_banner.text() == "/users/raj/lab/main.c is open: 9 reads."
    assert face._walk_map.marks()[8].txt == "R9"
    assert not face._walk_show.isEnabled()


def test_a_wrong_block_shows_its_sentence_and_changes_nothing(face, events):
    face._on_walk_block(2)
    face._on_walk_block(19)
    assert face._walk_banner.property("kind") == "bad"
    assert face._walk_banner.text().startswith("Not block 19. The kernel needs the data of /")
    assert face.session.walk_done() == 1
    assert list(face._walk_map.marks()) == [2]
    assert "i-node block 19" in face._walk_inside.text(), "the strip says what block 19 is"
    assert "0 wrong clicks" not in face._walk_total.text()
    assert events == []


def test_show_me_makes_the_walk_ineligible_for_its_goal(face, events):
    face._walk_show.click()
    for b in MAIN[1:]:
        face._on_walk_block(b)
    assert face._chips["main"].text() == "○ main.c in order"
    assert face._walk_banner.text().endswith("(with Show me).")
    assert events == []


def test_watch_runs_the_walk_on_a_timer_and_any_control_stops_it(face, events):
    face._walk_watch.click()
    assert face._watch.isActive() and face._walk_watch.text() == "Stop"
    face._watch_tick()
    assert face.session.walk_done() == 2
    face._undo.click()
    assert not face._watch.isActive()
    face._walk_watch.click()
    while face._watch.isActive():
        face._watch_tick()
    assert face.session.walk_complete() and events == []


def test_the_table_tab_opens_a_name_and_can_hide_the_i_node_blocks(face):
    assert face._tab_btns["table"].isChecked()
    rows = {r.path: r for r in face._path_rows}
    assert "i-node block 4" in rows["/users/raj/m"].m.text()
    rows["/users/raj/m"].click()
    QApplication.processEvents()
    assert face.session.walk.path == "/users/raj/m"
    assert face._path_box.currentText() == "/users/raj/m"
    face._hide_ib_btn.click()
    QApplication.processEvents()
    assert all("i-node block ?" in r.m.text() for r in face._path_rows)


def test_try_every_path_fills_its_tab(face):
    face._every.click()
    assert face._tab_btns["every"].isChecked()
    assert face._every_rows[-1] == ("/users/raj/m", 10, 7)
    assert len(face._open_btns) == 7
    face._open_btns[1].click()
    assert face.session.walk.path == "/users"


def test_each_start_loads_and_random_has_a_fixed_seed(face):
    _start(face, "Yours")
    assert face._path_box.currentText() == "/courses/os/labs/fs.c"
    face._random.click()
    s = face.session
    assert s.start.label == "Random"
    assert face._start_btns[2].text() == f"Random · {s.seed()}"
    assert s.walk.path == F.start_world(s.lab, "walk", "Random", s.seed()).path


# ---- level 2 --------------------------------------------------------------------- #
def _click_region(view, kind: str, value) -> None:
    QApplication.processEvents()
    view.repaint()
    s = view._scale()
    r = next(r for r, k, v in view.regions() if k == kind and v == value)
    c = r.center()
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier,
                     QPoint(round(c.x() * s), round(c.y() * s)))
    QApplication.processEvents()


def test_three_reads_clicked_on_the_painted_i_node_and_grids(face, events):
    _level(face, "byte")
    face._off_btns[4].click()
    assert face.session.byte.offset == 599500
    _click_region(face._ptr_view, "slot", "double")
    assert face._byte_banner.text() == "The kernel reads block 65: 1 read so far. Which entry?"
    _click_region(face._ptr_view, "entry", (65, 1))
    _click_region(face._ptr_view, "entry", (66, 61))
    assert face._chips["three"].text() == "✓ Three reads"
    assert [(k, p["clicks"], p["offset"]) for k, p in events] == [
        ("fs_read", ["double", 1, 61], 599500)]
    assert "double[1][61]" in face._byte_table.text()
    assert face._byte_banner.property("kind") == "ok"


def test_a_wrong_pointer_shows_what_it_serves_and_reads_nothing(face, events):
    _level(face, "byte")
    face._on_offset(60000)
    face._on_slot("double")
    assert face._byte_banner.property("kind") == "bad"
    assert face._byte_banner.text().startswith("Not that pointer. double serves")
    assert face.session.byte.stage == 0 and events == []


def test_past_the_end_is_a_button(face, events):
    _level(face, "byte")
    face._on_offset(601000)
    face._eof_btn.click()
    assert face._chips["eof"].text() == "✓ Past the end"
    assert events[-1][1]["clicks"] == ["eof"]
    assert not face._eof_btn.isEnabled()


def test_a_typed_offset_is_read_with_its_separators(face):
    _level(face, "byte")
    face._off_edit.setText("5,000")
    face._on_off_edit()
    assert face.session.byte.offset == 5000
    assert face._off_btns[1].isChecked()


def test_try_every_block_fills_the_grids_on_level_2(face):
    _level(face, "byte")
    face._every.click()
    assert face._bands is not None and face._bands.cell_count() == 12 + 256 * 3
    assert any("hole, 1 R · 511" in lbl.text() for lbl in face._side_labels)


# ---- level 3 --------------------------------------------------------------------- #
def test_big_and_thin_from_the_preset_turns_its_chip(face, events):
    _level(face, "make")
    face._mk_presets[4].click()
    assert face._chips["thin"].text() == "✓ Big and thin"
    assert events == [("fs_make", {"lab": "t-lab-04", "level": "make",
                                   "writes": [[1000000, 1]], "goal": "thin"})]
    assert "Took 3 blocks: 61 (index), 62 (index), 63." in face._make_banner.text()
    assert "1,000,001" in face._make_stats.text()


def test_a_typed_write_and_a_refused_one(face):
    _level(face, "make")
    face._mk_n.setText("0")
    face._mk_off.setText("10")
    face._mk_write.click()
    assert face._make_banner.property("kind") == "bad"
    face._mk_n.setText("100")
    face._mk_off.setText("12,288")
    face._mk_write.click()
    assert face.session.make.writes == [(12288, 100)]
    assert face._make_banner.text().startswith("Bytes 12,288–12,387: file block 12")


def test_try_every_block_fills_the_grids_on_level_3(face):
    _level(face, "make")
    face._every.click()
    assert face._bands is not None and face._bands.cell_count() == 12 + 256 * 4


# ---- level 4 --------------------------------------------------------------------- #
def test_the_trace_built_with_the_map_and_the_r_and_w_buttons_reaches_index_block_first(face,
                                                                                        events):
    _level(face, "append")
    res = face.session.write_res()
    for r in res.rows:
        _click_block(face._ap_map, r.block)
        assert face._r_btn.isVisible() and face._w_btn.isVisible()
        (face._r_btn if r.op == "R" else face._w_btn).click()
    assert face._chips["index"].text() == "✓ Index block first"
    assert len(events) == 1 and events[0][0] == "fs_trace" and events[0][1]["goal"] == "index"
    assert face._ap_count.text() == "3 R + 7 W"
    assert face._ap_banner.text().startswith("Complete: 3 R + 7 W.")
    assert not face._pending.isVisible()
    assert face._ap_map.marks()[54].txt == "1·2"
    assert face._ap_map.kind(62) == "data", "a block written takes its new colour"


def test_the_row_being_built_stays_in_view_as_the_trace_grows(face):
    _level(face, "append")
    for r in face.session.write_res().rows[:6]:
        face._on_ap_block(r.block)
        face._on_commit(r.op)
    face._on_ap_block(61)
    QApplication.processEvents()
    bar = face._ap_scroll.verticalScrollBar()
    assert bar.value() == bar.maximum()
    assert face._r_btn.visibleRegion().boundingRect().height() > 0


def test_a_refused_step_is_red_and_counted_and_changes_nothing(face, events):
    _level(face, "append")
    face._on_ap_block(62)
    face._w_btn.click()
    assert face._ap_banner.property("kind") == "bad"
    assert face._ap_banner.text().startswith("Not yet: read the superblock first")
    assert face.session.append.done == [] and face._ap_total.text().startswith("1 refused")
    assert events == []


def test_thats_all_and_show_me_on_level_4(face, events):
    _level(face, "append")
    face._allin.click()
    assert face._ap_banner.text().startswith("Not yet: 10 more block accesses to go.")
    face._ap_show.click()
    assert face.session.append.done == [(54, "R")]
    assert "Show me used 1 time" in face._ap_total.text()


def test_the_presets_and_the_kind_of_write_switch_the_call(face):
    _level(face, "append")
    assert [b.text() for b in face._ap_presets] == ["+3,000", "+788", "+789", "1,024 at 1,024",
                                                    "1,024 at 1,000"]
    face._ap_presets[3].click()
    assert face.session.append.op == "at" and face._ap_off.isVisible()
    assert face._op_box.currentData() == "at"
    face._op_box.setCurrentIndex(0)
    face._on_op_box(0)
    assert face.session.append.op == "append" and not face._ap_off.isVisible()


def test_the_plan_tab_answers_only_when_asked(face):
    _level(face, "append")
    assert face._plan_btn is not None
    face._plan_btn.click()
    QApplication.processEvents()
    assert face._plan_btn is None
    assert any("61 (index), 62, 63, 64" in lbl.text() for lbl in face._side_labels)


def test_try_every_length_draws_the_chart_and_the_intervals(face):
    _level(face, "append")
    face._every.click()
    assert face._chart is not None
    assert any("1–788" in lbl.text() and "2 R + 2 W" in lbl.text()
               for lbl in face._side_labels)


def test_the_file_strip_shows_the_blocks_around_the_write(face):
    _level(face, "append")
    assert [c.fb for c in face._strip.cells()] == [9, 10, 11, 12, 13, 14, 15]


# ---- the frame ------------------------------------------------------------------- #
def test_hovering_a_block_says_what_it_holds_and_leaving_restores_the_strip(face):
    face._on_hover(4)
    assert face._walk_inside.text().startswith("<b>i-node block 4</b> holds i-nodes 16–23")
    face._on_hover(None)
    assert "Point at a block" in face._walk_inside.text()


def test_the_level_buttons_and_chips_carry_their_words(face):
    assert face._level_btns[3].toolTip() == "Level 4: Append and overwrite"
    assert face._chips["link"].toolTip().endswith("(not yet)")
    assert all(isinstance(c, QLabel) for c in face._chips.values())
    assert face._every.text() == "Try every path"


def test_the_window_names_the_machine_it_was_opened_from(app):
    class _Dev:
        name = "M1"
    w = V.FsLab(None, ThemeManager(app, "Light"), device=_Dev())
    try:
        assert w.windowTitle().endswith(" — M1")
    finally:
        w.close()


def test_the_lab_opens_with_no_provider_and_no_container(app):
    w = V.open_fs_lab(None, ThemeManager(app, "Light"))
    try:
        assert w.isVisible()
    finally:
        w.close()


def test_the_hub_opens_t_lab_04_in_this_window(app):
    from gini.domain import tlab_catalog as TC

    from gini.ui.user_code_lab import tlab_window
    assert tlab_window(TC.get("t-lab-04").engine) is V.FsLab


def test_every_theme_can_draw_every_level(app):
    from gini.ui.theme import THEMES
    for name in THEMES:
        w = V.FsLab(None, ThemeManager(app, name))
        try:
            w.show()
            for i in range(4):
                w._level_btns[i].click()
                w._every.click()
                w.grab()
        finally:
            w.close()


def test_a_point_on_the_pointer_view_maps_back_through_its_scale(face):
    _level(face, "byte")
    face.resize(780, 600)
    QApplication.processEvents()
    view = face._ptr_view
    view.repaint()
    assert view._scale() <= 1.0
    r = next(r for r, k, v in view.regions() if k == "slot" and v == "single")
    s = view._scale()
    hit = view._hit(QPointF(r.center().x() * s, r.center().y() * s), ("slot",))
    assert hit == ("slot", "single")


def test_the_disk_map_is_whole_on_level_4_after_visiting_the_other_levels(face):
    """Coming to level 4 from levels 1–3 left its disk map's card squeezed to 14 px — the page was
    laid out while hidden in the stack — and the map is what a student clicks on that level."""
    for i in (0, 1, 2, 3):
        face._pick_level(i)
        QApplication.processEvents()
        face.grab()
    m = face._ap_map
    card = m.parentWidget()
    assert m.isVisible() and card.height() >= m.height(), (
        f"the disk map's card is {card.height()} px for a {m.height()} px map")


def test_every_level_leaves_room_for_wider_fonts(face):
    """Fitting 940 px exactly is not enough: Linux's fonts are about 3% wider than the Mac's, and
    level 2's goals row asked CI for 949 px while it fit here. Every level and tab must leave
    5% spare."""
    for i in range(len(face.session.lab.levels)):
        face._pick_level(i)
        for name in list(face._tab_btns):
            face._tab_btns[name].click()
            QApplication.processEvents()
            need = face.minimumSizeHint().width()
            assert need <= W / 1.05, f"level {i + 1}/{name} needs {need} px of {W}"

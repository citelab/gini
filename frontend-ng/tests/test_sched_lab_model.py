"""T-Lab 02's view model, asserted without a widget.

Everything the face draws comes out of pure functions over the engine's replay and exploration,
and `Session` holds what the student chose. So the trace text, the pills, the graph's walls and
crosses, the results rows and the Fix it rules are all checked here, in the same spirit as
`lock_lab.bar_rows` — the window is just these drawn, and `test_sched_lab_face.py` checks that it
fits and that nothing left it.
"""
from __future__ import annotations

import random

import pytest
from gini.domain import schedlab as S
from gini.ui import sched_lab as V


@pytest.fixture(scope="module")
def lab():
    lab = S.get("t-lab-02")
    assert lab is not None
    return lab


@pytest.fixture
def session(lab):
    return V.Session(lab)


def level(session, level_id: str, version: str = "") -> S.Level:
    """Switch the session to a level (and version) and hand the level back."""
    idx = next(i for i, lv in enumerate(session.lab.levels) if lv.id == level_id)
    session.pick_level(idx)
    if version:
        session.pick_version(version)
    return session.level


# ---- the trace ------------------------------------------------------------------- #
def test_the_notes_trace_p_p_c_c_p_c_is_what_the_trace_rows_say(session):
    lv = level(session, "lost")
    session.show((0, 0, 1, 1, 0, 1))
    rows = V.trace_rows(session.replay())
    assert [(r.t, r.text) for r in rows] == [
        (0, "register1 = counter"), (0, "register1 = register1 + 1"),
        (1, "register2 = counter"), (1, "register2 = register2 - 1"),
        (0, "counter = register1"), (1, "counter = register2")]
    assert [r.state for r in rows] == ["5 / r1 = 5", "5 / r1 = 6", "5 / r2 = 5", "5 / r2 = 4",
                                       "6 / r1 = 6", "4: the ++ is lost"]
    assert [r.bad for r in rows] == [False] * 5 + [True]
    assert [r.n for r in rows] == [1, 2, 3, 4, 5, 6]
    assert V.progress(lv, session.replay(), 6) == ("Finished · ends at 4 (wrong)", "bad")


def test_a_note_is_folded_into_the_state_column_with_a_middle_dot(session):
    level(session, "lost", "locked")
    session.show((0, 1))
    rows = V.trace_rows(session.replay())
    assert rows[1].state == "5 / r2 = – · lock −1 · consumer blocks"
    assert rows[1].kind == "blocked"


def test_the_progress_line_counts_steps_then_says_how_it_ended(session):
    lv = level(session, "rdv")
    assert V.progress(lv, session.replay(), 0) == ("", "plain")
    session.run(0)
    assert V.progress(lv, session.replay(), 1) == ("1 step", "plain")
    session.show((0, 0, 1, 1))
    assert V.progress(lv, session.replay(), 4) == ("Deadlock: nobody can move", "bad")


# ---- the lanes -------------------------------------------------------------------- #
def test_a_blocked_thread_gets_a_grey_pill_and_a_disabled_run_button(session):
    lv = level(session, "lost", "locked")
    session.show((0, 1))
    ln = V.lane(lv, session.code, session.replay(), 1)
    assert (ln.status, ln.kind) == ("blocked on lock", "blocked")
    assert ln.run_enabled is False
    assert ln.run_label == "consumer is blocked"
    other = V.lane(lv, session.code, session.replay(), 0)
    assert other.run_label == "Run 2: register1 = counter"
    assert other.run_enabled is True
    assert [x.here for x in other.lines] == [False, True, False, False, False]
    assert [x.done for x in other.lines] == [True, False, False, False, False]


def test_a_spinning_process_and_one_in_its_critical_section_say_so(session):
    lv = level(session, "lockvar", "plain")
    session.show((0, 0, 1))
    assert V.lane(lv, session.code, session.replay(), 1).status == "spinning"
    session.show((0, 0))
    assert V.lane(lv, session.code, session.replay(), 0).status == "in critical section"


def test_when_nobody_can_move_both_run_buttons_say_so(session):
    lv = level(session, "rdv")
    session.show((0, 0, 1, 1))
    for t in (0, 1):
        ln = V.lane(lv, session.code, session.replay(), t)
        assert ln.run_enabled is False
        assert ln.status == "blocked on " + ("bArrived" if t == 0 else "aArrived")


def test_a_finished_thread_has_finished(session):
    lv = level(session, "lost", "line")
    session.run(0)
    ln = V.lane(lv, session.code, session.replay(), 0)
    assert (ln.status, ln.run_label, ln.run_enabled) == ("done", "producer has finished", False)


# ---- the shared-state column ---------------------------------------------------------- #
def test_the_state_column_shows_the_counter_and_each_threads_register(session):
    lv = level(session, "lost", "instr")
    rows = V.state_rows(lv, session.code, session.replay())
    assert [(r.name, r.kind_label, r.value) for r in rows] == [
        ("counter", "shared", "5"), ("register1", "producer only", "–"),
        ("register2", "consumer only", "–")]
    level(session, "lost", "line")
    assert [r.name for r in V.state_rows(session.level, session.code, session.replay())] == \
        ["counter"]


def test_a_semaphore_shows_its_count_and_who_is_queued(session):
    lv = level(session, "lost", "locked")
    session.show((0, 1))
    sem = next(r for r in V.state_rows(lv, session.code, session.replay()) if r.name == "lock")
    assert (sem.kind_label, sem.value, sem.chips, sem.accent) == \
        ("semaphore · a mutex: starts at 1", "−1", ("consumer",), True)


def test_test_and_set_draws_the_key_on_its_hook_and_the_plain_lock_is_an_ordinary_variable(session):
    lv = level(session, "lockvar", "tas")
    row = V.state_rows(lv, session.code, session.replay())[0]
    assert (row.icon, row.kind_label) == ("key_free", "key on the hook")
    session.run(0)
    row = V.state_rows(lv, session.code, session.replay())[0]
    assert (row.icon, row.kind_label) == ("key_held", "held by P0")
    level(session, "lockvar", "plain")
    row = V.state_rows(session.level, session.code, session.replay())[0]
    assert (row.icon, row.kind_label) == ("", "ordinary variable")


def test_the_buffer_is_drawn_last_with_its_slots(session):
    lv = level(session, "buffer")
    rows = V.state_rows(lv, session.code, session.replay())
    assert [r.name for r in rows] == ["mutex", "empty", "full", "buffer"]
    assert (rows[-1].kind_label, rows[-1].slots, rows[-1].value) == ("2 slots", (2, 2), "2")
    session.pick_start(2)
    assert V.state_rows(lv, session.code, session.replay())[-1].slots == (0, 2)


# ---- the progress graph --------------------------------------------------------------- #
def test_the_level_2_graph_shows_the_test_and_set_wall(session):
    lv = level(session, "lockvar", "tas")
    g = V.graph(lv, session.code, session.replay(), session.exploration())
    assert (g.m, g.n) == (3, 3)
    assert g.squares[(1, 1)] == "both_wall", "both in the critical section, and no schedule there"
    assert [k for k, v in g.squares.items() if v in ("wall", "both_wall")] == [(1, 1)]
    assert g.points[(1, 1)] == "unreachable"
    assert g.points[(0, 0)] == "current"
    level(session, "lockvar", "plain")
    g = V.graph(session.level, session.code, session.replay(), session.exploration())
    assert g.squares[(2, 2)] == "both", "the lock variable lets both in: tinted, not walled"
    assert "wall" not in {v for v in g.squares.values()}


def test_level_4s_stuck_point_is_marked_with_a_cross(session):
    lv = level(session, "buffer")
    g = V.graph(lv, session.code, session.replay(), session.exploration())
    assert g.points[(1, 1)] == "stuck"
    session.show((0, 0, 1, 1))
    g = V.graph(lv, session.code, session.replay(), session.exploration())
    assert g.points[(1, 1)] == "current_stuck"
    assert g.stuck is True
    assert g.path == ((0, 0), (1, 0), (1, 0), (1, 1), (1, 1))
    assert g.edges[((0, 0), (1, 0))] == "path"
    assert g.edges[((1, 0), (1, 1))] == "path"
    assert g.aria.endswith("1 of 5 lines of consumer, and is stuck.")


def test_in_level_1_every_interior_square_is_tinted_so_only_the_edge_paths_are_safe(session):
    lv = level(session, "lost", "instr")
    g = V.graph(lv, session.code, session.replay(), session.exploration())
    assert set(g.squares.values()) == {"both"}
    assert g.edges[((0, 0), (0, 1))] == "live"


# ---- every schedule ---------------------------------------------------------------- #
def test_level_1_counts_schedules_and_draws_a_bar_per_outcome(session):
    level(session, "lost", "instr")
    assert session.results() is None, "nothing shown until the button is pressed"
    session.explore_all()
    res = session.results()
    assert res.summary == "GINI tried all 20 schedules of this code."
    assert [(r.label, r.count, r.kind, r.show_label) for r in res.rows] == [
        ("ends at 5", "2 of 20", "good", "Show me one"),
        ("ends at 4", "9 of 20", "bad", "Show me one"),
        ("ends at 6", "9 of 20", "bad", "Show me one"),
        ("deadlock", "impossible", "good", "")]
    assert res.rows[0].frac == pytest.approx(0.1)
    assert res.verdict == "", "Instructions is not a version the level claims is correct"
    assert session.tab == "all"


def test_a_shipped_correct_version_is_proved_and_its_goal_is_met(session):
    level(session, "lost", "locked")
    session.explore_all()
    res = session.results()
    assert (res.verdict, res.verdict_text) == \
        ("proved", "Proved: with the lock, every schedule ends at 5.")
    assert session.goal_met("proof")
    assert V.goal_chips(session.level, session.achieved)[2].done is True


def test_a_level_without_counting_says_possible_and_reports_both_inside(session):
    level(session, "lockvar", "plain")
    session.explore_all()
    res = session.results()
    assert res.summary.startswith("GINI visited all ")
    assert [(r.label, r.count, r.show_label) for r in res.rows] == [
        ("both finish", "possible", "Show me one"),
        ("both inside the critical section", "possible", "Show me the shortest"),
        ("deadlock", "impossible", "")]
    assert res.rows[1].moves == (0, 1, 0, 1)


def test_when_no_schedule_finishes_the_first_row_says_never(session):
    level(session, "rdv")
    session.explore_all()
    rows = session.results().rows
    assert (rows[0].label, rows[0].count) == ("finishes", "never")
    assert (rows[1].label, rows[1].count, rows[1].moves) == ("deadlock", "possible", (0, 0, 1, 1))


def test_show_me_loads_the_schedule_and_undo_takes_a_step_off_it(session):
    level(session, "lost", "instr")
    session.explore_all()
    session.show(session.results().rows[1].moves)
    assert session.moves == (0, 0, 1, 0, 1, 1)
    assert session.goal_met("g4")
    session.undo()
    assert session.moves == (0, 0, 1, 0, 1)
    assert session.results() is not None, "a loaded schedule does not clear the results"


def test_switching_level_version_or_start_clears_the_schedule_and_the_results(session):
    level(session, "buffer")
    session.show((0, 0, 1, 1))
    session.explore_all()
    session.pick_start(1)
    assert session.moves == () and session.results() is None
    session.run(0); session.explore_all()
    session.pick_version("fix")
    assert session.moves == () and session.results() is None
    session.run(0)
    session.pick_level(0)
    assert session.moves == ()
    session.pick_level(3)
    assert session.version_id == "fix", "the version is remembered per level"
    assert session.start == 1, "and so is the start"


# ---- fix it ------------------------------------------------------------------------ #
def test_a_pinned_tile_cannot_be_picked_up_and_refuses_a_swap(session):
    level(session, "rdv", "fix")
    session.pick_tile(0, 0)                       # a1 is pinned
    assert session.selected is None
    session.pick_tile(0, 1)                       # wb
    assert session.selected == (0, 1)
    assert V.fix_bar(session.level, session.code, session.selected) == \
        ("Thread A, line 2: bArrived.wait()", False, True)
    assert session.move_sel(-1) is False, "a1 above it is pinned"
    assert session.move_sel(+1) is True
    assert session.code[0] == ("a1", "sa", "wb", "a2")
    assert session.selected == (0, 2), "the selection follows the tile"
    assert session.move_sel(+1) is False, "a2 below it is pinned"
    assert V.fix_bar(session.level, session.code, None) == (V.NO_SELECTION, False, False)


def test_picking_the_selected_tile_again_puts_it_down(session):
    level(session, "rdv", "fix")
    session.pick_tile(1, 2)
    session.pick_tile(1, 2)
    assert session.selected is None


def test_level_3_is_fixed_by_moving_the_signals_above_the_waits(session):
    level(session, "rdv", "fix")
    session.pick_tile(0, 1); session.move_sel(+1)
    session.pick_tile(1, 1); session.move_sel(+1)
    proof = session.explore_all()
    assert proof.proved is True
    res = session.results()
    assert res.verdict == "proved"
    assert res.verdict_text.startswith("Proved: no schedule deadlocks")
    assert session.goal_met("fix")


def test_one_side_signalling_first_is_already_enough(session):
    level(session, "rdv", "fix")
    session.pick_tile(0, 1); session.move_sel(+1)
    assert session.explore_all().proved is True


def test_level_4_is_fixed_by_waiting_for_space_before_the_lock(session):
    level(session, "buffer", "fix")
    session.pick_tile(0, 1); session.move_sel(-1)          # wait(&empty) above wait(&mutex)
    assert session.code[0] == ("pwe", "pwm", "ins", "psm", "psf")
    proof = session.explore_all()
    assert proof.proved is True
    assert session.results().verdict_text.endswith("From every starting buffer.")
    assert session.goal_met("fix")


def test_releasing_the_mutex_before_insert_is_not_fixed_and_the_reason_names_the_start(session):
    level(session, "buffer", "fix")
    session.pick_tile(0, 1); session.move_sel(-1)
    session.pick_tile(0, 3); session.move_sel(-1)          # signal(&mutex) above insert_item
    assert session.code[0] == ("pwe", "pwm", "psm", "ins", "psf")
    assert session.explore_all().proved is False
    res = session.results()
    assert res.verdict == "not_yet"
    assert res.verdict_text == ("Not fixed yet: with the buffer starting half full, "
                                "a schedule has both in the buffer code at once.")
    other = [r for r in res.rows if r.label.startswith("Buffer starting")]
    assert [(r.label, r.count, r.show_label) for r in other] == [
        ("Buffer starting half full", "fails", "Show me"),
        ("Buffer starting empty", "proved", "")]
    session.show(other[0].moves, other[0].start)
    assert session.start == 1
    assert session.replay().rows[-1].bad is True


def test_inserting_before_the_waits_is_not_fixed_either(session):
    level(session, "buffer", "fix")
    session.pick_tile(0, 2); session.move_sel(-1); session.move_sel(-1)
    assert session.code[0][0] == "ins"
    assert session.explore_all().proved is False
    assert session.results().verdict_text.startswith("Not fixed yet: ")


def test_start_over_returns_the_broken_version(session):
    level(session, "buffer", "fix")
    session.pick_tile(0, 1); session.move_sel(-1)
    session.restore()
    assert session.code == session.level.code_of("swapped")
    assert session.selected is None


def test_an_edit_clears_the_schedule_and_the_results(session):
    level(session, "buffer", "fix")
    session.run(0); session.explore_all()
    session.pick_tile(0, 1); session.move_sel(-1)
    assert session.moves == () and session.results() is None


def test_the_hint_changes_while_fixing(session):
    lv = level(session, "rdv")
    assert V.hint(lv, False) == "Press a Run button: you decide who goes next."
    assert V.hint(lv, True) == V.FIX_HINT


# ---- goals and events ------------------------------------------------------------------- #
def test_goal_chips_say_where_each_goal_is_met(session):
    chips = V.goal_chips(level(session, "lost"), set())
    assert [(c.text, c.done, c.tooltip) for c in chips] == [
        ("End at 4", False, "Make counter end at 4 (Instructions)"),
        ("End at 6", False, "Make counter end at 6 (Instructions)"),
        ("Proof: always 5", False,
         "Show that with the lock every schedule ends at 5 (With a lock, then Try every schedule)")]
    chips = V.goal_chips(level(session, "rdv"), set())
    assert chips[1].tooltip == \
        "Move the tiles so that no schedule deadlocks (Fix it, then Try every schedule)"


def test_a_goal_met_mid_schedule_stays_met_after_undo(session):
    level(session, "rdv")
    session.show((0, 0, 1, 1))
    assert session.goal_met("dl")
    session.undo()
    assert session.goal_met("dl")


def test_the_session_tells_its_listener_about_goals_and_proofs(lab):
    seen = []
    s = V.Session(lab, on_event=lambda kind, p: seen.append((kind, p)))
    level(s, "lost", "instr")
    s.show((0, 0, 1, 1, 0, 1))
    assert [(k, p["goal"], p["result"]) for k, p in seen] == [("goal", "g4", "ends at 4")]
    assert seen[0][1]["moves"] == (0, 0, 1, 1, 0, 1)
    assert seen[0][1]["code"] == s.code
    s.show((0, 0, 1, 1, 0, 1))
    assert len(seen) == 2 and seen[1][0] == "goal", "told again; the recorder keeps one"
    del seen[1]
    s.explore_all()
    assert len(seen) == 2 and seen[1][0] == "explored", "not provable: what it showed, no proof"
    assert seen[1][1]["played"] == 1 and seen[1][1]["summary"][0]["total"] == 20
    level(s, "rdv", "fix")
    assert seen[2][0] == "level" and seen[2][1]["level"] == "rdv"
    s.explore_all()
    assert seen[-1][0] == "proved" and seen[-1][1]["ok"] is False
    assert seen[-1][1]["reason"] == "no schedule finishes"
    s.pick_tile(0, 1); s.move_sel(+1)
    s.explore_all()
    assert seen[-1][1]["ok"] is True and seen[-1][1]["starts"] == (0,)
    level(s, "buffer", "fix")
    s.explore_all()
    assert seen[-1][1]["starts"] == (0, 1, 2)


# ---- random ----------------------------------------------------------------------------- #
def test_random_runs_the_schedule_to_the_end_and_is_reproducible(session):
    lv = level(session, "lost", "instr")
    session.random(random.Random(7))
    rep = session.replay()
    assert rep.finished
    assert len(session.moves) == 6
    again = V.random_extend(lv, session.code, 0, (), random.Random(7))
    assert again == session.moves


def test_random_extends_what_is_there_and_stops_when_stuck(session):
    level(session, "rdv")
    session.run(0)
    session.random(random.Random(1))
    assert session.moves[0] == 0
    assert session.replay().deadlock is True
    assert len(session.moves) <= V.RANDOM_CAP


def test_random_cannot_wedge_on_a_spin_loop(session):
    lv = level(session, "lockvar", "plain")
    moves = V.random_extend(lv, session.code, 0, (), random.Random(3), cap=5)
    assert len(moves) <= 5

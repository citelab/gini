"""T-Lab 03's view model, asserted without a widget.

Everything the face draws comes out of pure functions over the engine's state, and `Session`
holds what the student chose. So the pills' words, which circles are deadlocked and which edges
thick, the trace's last line, the Reduce, Recover and Every order tabs, the bank tables and the
hand-run check are all checked here — the window is just these drawn, and
`test_bank_lab_face.py` checks that it fits and that its buttons are wired.

The chain is checked here too: one entry per goal per level, the first time it is reached, with
the fields the recorder's builders take — and an `allocate` entry's moves replay, through the
engine the Teaching Center has, to a state that meets the goal.
"""
from __future__ import annotations

import random

import pytest
from gini.domain import banklab as B
from gini.domain import proof_events as ev

from gini.ui import bank_lab as V


@pytest.fixture(scope="module")
def lab():
    lab = B.get("t-lab-03")
    assert lab is not None
    return lab


@pytest.fixture
def seen():
    return []


@pytest.fixture
def session(lab, seen):
    return V.Session(lab, on_event=lambda kind, p: seen.append((kind, p)))


def level(session, level_id: str, start: str = "", version: str = "") -> B.Level:
    idx = next(i for i, lv in enumerate(session.lab.levels) if lv.id == level_id)
    session.pick_level(idx)
    if start:
        session.pick_start(session.level.start(start))
    if version:
        session.pick_version(version)
    return session.level


def play(session, *moves) -> None:
    w = session.world
    for pn, kind in moves:
        assert session.play(w.procs.index(pn), kind), f"{pn}/{kind} is not possible"


# ---- the levels ------------------------------------------------------------------------
def test_every_level_opens_on_its_first_start_with_the_right_tabs(session, lab):
    for i, lv in enumerate(lab.levels):
        session.pick_level(i)
        assert session.start == 0 and session.moves == ()
        tabs = [t for t, _ in session.tabs()]
        if lv.mode == "bank":
            assert tabs == ["check", "every"] and session.tab == "check"
        else:
            assert tabs[:2] == ["tables", "reduce"] and tabs[-1] == "every"
            assert session.tab == "tables"


def test_every_start_of_every_level_loads(session, lab):
    for i, lv in enumerate(lab.levels):
        session.pick_level(i)
        for s in range(len(lv.starts)):
            session.pick_start(s)
            assert session.start_label == lv.starts[s].label
            if lv.mode == "run":
                assert V.proc_rows(session.world, session.version, session.state())
            else:
                assert V.bank_table(session.world, s, None, True).rows


def test_the_recover_tab_exists_only_where_the_start_declares_recovery(session):
    level(session, "recover", "Figure 14.9")
    assert "recover" not in [t for t, _ in session.tabs()]
    level(session, "recover", "Worked example 14.8")
    assert "recover" in [t for t, _ in session.tabs()]
    session.tab = "recover"
    level(session, "recover", "Figure 14.9")
    assert session.tab == "tables", "a tab the start does not have falls back to the first"


def test_the_wait_for_switch_is_offered_on_level_5_where_every_resource_has_one_unit(session):
    lv = level(session, "recover", "Figure 14.9")
    assert V.wait_for_offered(lv, session.world)
    lv = level(session, "recover", "Worked example 14.8")
    assert not V.wait_for_offered(lv, session.world)
    lv = level(session, "claims")
    assert not V.wait_for_offered(lv, session.world), "single units, but not the detect level"


def test_switching_level_version_or_start_clears_the_order_and_the_results(session):
    level(session, "claims", version="graph")
    play(session, ("P1", "step"))
    session.explore_all()
    assert session.exploration() is not None
    session.pick_version("free")
    assert session.moves == () and session.exploration() is None


# ---- the processes and the graph ----------------------------------------------------------
def test_worked_example_14_1_says_who_waits_for_what_and_who_can_move(session):
    level(session, "graph")
    rows = V.proc_rows(session.world, session.version, session.state())
    assert [(r.name, r.status, r.kind) for r in rows] == [
        ("P1", "waits for printer", "blocked"), ("P2", "waits for plotter", "blocked"),
        ("P3", "running", "active")]
    assert [m.label for m in rows[2].moves] == ["P3 asks for scanner", "P3 finishes"]
    assert rows[2].moves[0].ask and not rows[2].moves[1].ask
    assert rows[0].moves == ()


def test_when_the_chain_closes_every_circle_says_deadlocked_and_the_cycle_is_thick(session):
    lv = level(session, "graph")
    play(session, ("P3", "step"))
    g = V.graph_view(lv, session.world, session.version, session.state())
    procs = {n.name: (n.state, n.caption) for n in g.nodes if n.proc}
    assert procs == {p: ("deadlocked", "deadlocked") for p in ("P1", "P2", "P3")}
    assert all(e.thick for e in g.edges)
    boxes = {n.name: n.units for n in g.nodes if not n.proc}
    assert boxes == {"scanner": 1, "printer": 1, "plotter": 1}
    assert g.aria.startswith("Resource-allocation graph. ")


def test_a_cycle_through_two_unit_resources_is_thick_but_nobody_is_deadlocked(session):
    lv = level(session, "graph", "Figure 14.3")
    g = V.graph_view(lv, session.world, session.version, session.state())
    assert {n.state for n in g.nodes if n.proc} == {"blocked", "active"}
    assert any(e.thick for e in g.edges) and not all(e.thick for e in g.edges)


def test_claim_edges_are_drawn_on_level_2_under_both_versions(session):
    for vid in ("free", "graph"):
        lv = level(session, "claims", version=vid)
        g = V.graph_view(lv, session.world, session.version, session.state())
        assert sum(e.kind == "claim" for e in g.edges) == 6
    lv = level(session, "lucky")
    g = V.graph_view(lv, session.world, session.version, session.state())
    assert not any(e.kind == "claim" for e in g.edges), "Max alone does not draw claim edges"


def test_a_refused_grant_shades_the_cycle_it_would_close_and_says_why_in_words(session):
    lv = level(session, "claims", version="graph")
    play(session, ("P1", "step"), ("P2", "step"), ("P3", "step"))
    g = V.graph_view(lv, session.world, session.version, session.state())
    assert g.ghost and g.ghost[0] == g.ghost[-1]
    p3 = next(n for n in g.nodes if n.name == "P3")
    assert (p3.state, p3.caption) == ("refused", "waits — would close a cycle")
    row = V.proc_rows(session.world, session.version, session.state())[2]
    assert (row.status, row.kind) == ("waits: would close a cycle", "refused")


def test_the_reduce_tab_numbers_the_circles_in_erase_order(session):
    lv = level(session, "graph", "Figure 14.3")
    g = V.graph_view(lv, session.world, session.version, session.state(), erased=True)
    assert {n.name: n.erased for n in g.nodes if n.proc} == {"P2": 0, "P1": 1, "P3": 2, "P4": 3}


def test_the_wait_for_graph_drops_the_boxes_and_keeps_the_cycle(session):
    lv = level(session, "recover", "Figure 14.9")
    g = V.graph_view(lv, session.world, session.version, session.state(), wait=True)
    assert g.wait_for and all(n.proc for n in g.nodes)
    assert sorted((e.a, e.b) for e in g.edges) == [("P1", "P2"), ("P2", "P3"), ("P3", "P4"),
                                                   ("P4", "P2")]
    assert {(e.a, e.b) for e in g.edges if e.thick} == {("P2", "P3"), ("P3", "P4"), ("P4", "P2")}


# ---- the trace and the side tabs ----------------------------------------------------------
def test_the_trace_ends_by_saying_who_waits_forever(session):
    level(session, "graph")
    play(session, ("P3", "step"))
    lines, end = V.trace(session.world, session.state(), session.lines())
    assert lines == ("P3 asks for scanner: waits",)
    assert end == ("Deadlock: nobody can move. P1, P2, P3 wait forever.", "bad")


def test_the_trace_says_when_everyone_has_finished(session):
    level(session, "graph")
    play(session, ("P3", "finish"))
    assert V.trace(session.world, session.state(), session.lines())[1] is None
    play(session, ("P2", "step"), ("P1", "step"))
    lines, end = V.trace(session.world, session.state(), session.lines())
    assert end == ("Everyone has finished.", "good")
    assert lines[:2] == ("P3 finishes and returns what it held; P2 gets plotter",
                         "P2 finishes and returns what it held; P1 gets printer")


def test_the_tables_tab_shows_hold_request_and_max(session):
    level(session, "claims")
    play(session, ("P1", "step"))
    t = V.tables(session.world, session.state())
    assert t.heads == ("", "Hold", "Request", "Max")
    assert t.rows[0] == ("P1", "(1, 0, 0)", "–", "(1, 1, 0)")
    assert t.avail == "(0, 1, 1)" and t.order_note == "Vectors list R1, R2, R3."


def test_the_reduce_tab_lists_the_cycles_and_the_erase_rows(session):
    level(session, "graph", "Figure 14.3")
    r = V.reduce_view(session.world, session.state())
    assert r.cycles == ("P1 → R1 → P3 → R2 → P1",)
    assert [row[1] for row in r.rows] == ["P2", "P1", "P3", "P4"]
    assert (r.box, r.box_kind) == ("Everyone is erased: no deadlock.", "ok")
    level(session, "recover", "Worked example 14.8")
    r = V.reduce_view(session.world, session.state())
    assert r.box == "Nobody else can be erased: P2, P3, P4 are deadlocked."


def test_the_recover_tab_gives_each_way_out_its_cost(session):
    level(session, "recover", "Worked example 14.8")
    r = V.recover_view(session.world, session.state())
    assert r.thrown == "Work thrown away so far: 0 min" and r.box == ""
    assert [(row.name, [m.label for m in row.moves]) for row in r.rows] == [
        ("P1", ["Terminate · 20 min lost"]), ("P2", ["Terminate · 10 min lost"]),
        ("P3", ["Terminate · 2 h lost"]), ("P4", ["Terminate · 30 min lost"])]


def test_the_recover_tab_says_whether_nobody_could_have_got_out_for_less(session):
    level(session, "recover", "Worked example 14.8")
    play(session, ("P3", "kill"))
    r = V.recover_view(session.world, session.state())
    assert r.thrown == "Work thrown away so far: 2 h (1 action)"
    assert r.box_kind == "info" and "cheaper way out" in r.box
    session.reset()
    play(session, ("P2", "kill"))
    r = V.recover_view(session.world, session.state())
    assert r.box_kind == "ok" and r.box.endswith("Nobody could have got out for less.")


def test_try_every_order_on_the_graph_algorithm_is_a_proof(session, seen):
    level(session, "claims", version="graph")
    ex = session.explore_all()
    o = V.orders_view(session.version, ex)
    assert [r.label for r in o.rows] == ["everyone finishes"]
    assert o.verdict == "proved" and o.verdict_text.startswith("Proved: counting claim edges")
    assert session.goal_met("proof") and session.tab == "every"
    kind, p = seen[-1]
    assert kind == "orders" and p["ok"] is True and p["version"] == "graph"
    assert p["summary"]["states"] == ex.states
    assert [lab for lab, _ in p["summary"]["outcomes"]] == ["everyone finishes"]
    assert p["summary"]["outcomes"][0][1] > 1


def test_a_version_that_proves_nothing_has_no_verdict_and_sends_no_orders_entry(session, seen):
    level(session, "claims", version="free")
    ex = session.explore_all()
    o = V.orders_view(session.version, ex)
    assert o.verdict == "" and any(r.label == "deadlock: P1, P2, P3" for r in o.rows)
    assert not any(k == "orders" for k, _ in seen)


def test_show_me_loads_the_shortest_order_and_undo_takes_a_step_off_it(session):
    level(session, "claims", version="free")
    ex = session.explore_all()
    dl = next(o for o in ex.outcomes if not o.ok)
    session.show(dl.moves)
    assert len(session.moves) == 6 and session.goal_met("deadlock")
    session.undo()
    assert len(session.moves) == 5
    assert session.exploration() is ex, "the results belong to the start, not to the order"


# ---- goals and the chain --------------------------------------------------------------------
def test_goal_chips_say_where_each_goal_is_met(session):
    chips = V.goal_chips(level(session, "claims"), set())
    assert [(c.text, c.tooltip) for c in chips] == [
        ("Deadlock", ("With every request granted when free, reach a deadlock "
                      "(Worked example 14.4, Grant when free)")),
        ("Wait, though free", ("Make a process wait although its resource is free "
                               "(Worked example 14.4, Graph algorithm)")),
        ("Proof: never stuck", ("Show that no order of requests deadlocks under the graph "
                                "algorithm (Worked example 14.4, Graph algorithm, then Try "
                                "every order)"))]
    chips = V.goal_chips(level(session, "graph"), {"graph:deadlock"})
    assert [c.done for c in chips] == [True, False, False]


def test_a_goal_sends_one_allocate_entry_the_first_time_and_never_again(session, seen):
    level(session, "graph")
    play(session, ("P3", "step"))
    assert [(k, p["goal"]) for k, p in seen] == [("allocate", "deadlock")]
    p = seen[0][1]
    assert p == {"lab": "t-lab-03", "level": "graph", "start": 0, "version": "free",
                 "moves": [[2, "step"]], "goal": "deadlock", "result": "deadlock: P1, P2, P3"}
    assert set(p) >= {"lab", "level", "version", "start", "moves", "goal", "result"}
    session.reset()
    play(session, ("P3", "step"))
    assert len(seen) == 1, "once per goal, not once per click"


def test_a_goal_on_another_start_is_not_met_from_this_one(session, seen):
    level(session, "graph", "Figure 14.3")
    play(session, ("P4", "step"), ("P2", "step"))
    assert names_deadlocked(session) and not seen, "the knot goal is set on Yours"


def names_deadlocked(session):
    return [session.world.procs[p] for p in B.deadlocked(session.world, session.state())]


def test_an_allocate_entry_replays_to_a_state_that_meets_its_goal(session, seen):
    level(session, "graph", "Yours")
    play(session, ("P1", "step"), ("P2", "step"), ("P3", "step"), ("P4", "step"))
    lv = session.level
    assert [p["goal"] for _, p in seen] == ["cycle", "knot"]
    for kind, p in seen:
        assert kind == "allocate"
        s = lv.start(lv.starts[p["start"]].label)
        w = B.world(lv, s)
        st, _ = B.replay(w, w.version(p["version"]), s, [tuple(m) for m in p["moves"]])
        assert B.reach_met(w, lv.goal(p["goal"]), st)
        built = ev.allocate(**{k: p[k] for k in ("lab", "level", "version", "start", "moves",
                                                 "goal", "result")})
        assert built[0] == ev.ALLOCATE


def test_a_goal_met_mid_order_is_recorded_with_the_moves_up_to_that_step(session, seen):
    level(session, "claims", version="graph")
    play(session, ("P1", "step"), ("P2", "step"), ("P3", "step"), ("P1", "step"))
    assert [p["moves"] for _, p in seen] == [[[0, "step"], [1, "step"], [2, "step"]]]


def test_a_recovery_goal_is_met_through_the_recover_moves(session, seen):
    level(session, "recover", "Yours: terminate")
    assert any(m.kind == "kill" for m in session.options())
    for p in range(len(session.world.procs)):
        session.reset()
        session.play(p, "kill")
    assert [p["goal"] for _, p in seen] == ["victim"]
    assert seen[0][1]["result"].startswith("no deadlock, ")


# ---- random, undo, reset ----------------------------------------------------------------------
def test_random_runs_to_the_end_and_is_reproducible(session):
    level(session, "lucky", "Yours")
    session.random(random.Random(7))
    st = session.state()
    assert B.finished(st) or not B.moves(session.world, st, include_kill=False)
    again = V.random_extend(session.world, session.version, session.start, (), random.Random(7))
    assert again == session.moves
    assert not session.can_random()


def test_random_never_takes_a_recovery_action(session):
    level(session, "recover", "Yours: three ways out")
    session.random(random.Random(1))
    assert session.moves == ()


def test_undo_and_reset_on_a_run_level(session):
    level(session, "lucky")
    play(session, ("P1", "step"), ("P2", "step"))
    assert session.can_undo() and session.can_reset()
    session.undo()
    assert session.moves == ((0, "step"),)
    session.reset()
    assert session.moves == () and not session.can_undo()


def test_a_move_that_is_not_possible_is_refused(session):
    level(session, "graph")
    assert session.play(0, "step") is False
    assert session.moves == ()


# ---- the bank level ----------------------------------------------------------------------------
def test_yours_hides_need_and_avail_until_the_student_shows_them(session):
    level(session, "banker", "Yours")
    t = V.bank_table(session.world, session.start, None, session.need_shown)
    assert t.hidden and t.avail == "Avail = ?"
    assert all(c.text == "?" for _, _, _, nd in t.rows for c in nd)
    assert [c.text for c in t.rows[0][2]] == ["2", "0", "1"], "Hold is never hidden"
    session.toggle_need()
    t = V.bank_table(session.world, session.start, None, session.need_shown)
    assert not t.hidden and t.avail == "Avail = (2, 1, 3)"


def test_a_provisional_grant_is_written_old_arrow_new(session):
    level(session, "banker", "Worked example 14.7")
    session.pick_request(0)                       # P2 requests (1, 0, 1)
    t = V.bank_table(session.world, session.start, session.provisional(), True)
    p2 = t.rows[1]
    assert [c.text for c in p2[2]] == ["2 → 3", "0", "1 → 2"]
    assert [c.text for c in p2[3]] == ["1 → 0", "2", "1 → 0"]
    assert p2[2][0].changed and not p2[2][1].changed
    assert t.avail == "Avail = (1, 2, 2) → (0, 2, 1) (provisional)"


def test_the_tests_line_follows_the_notes_order(session):
    level(session, "banker", "Worked example 14.7")
    w = session.world
    assert V.test_lines(w, session.start, 0, True) == (
        ("1. Req ≤ Need2? (1, 0, 1) ≤ (1, 2, 1):", "yes"),
        ("2. Req ≤ Avail? (1, 0, 1) ≤ (1, 2, 2):", "yes"),
        ("3. Allocate provisionally (written 2 → 3 in the tables) and run the safety check.",
         ""))
    lines = V.test_lines(w, session.start, 3, True)       # P4 asks for more than it declared
    assert lines[0][1] == "no" and lines[-1][0] == "No safety check is needed: decide."
    assert V.test_lines(w, session.start, 0, False)[0][0].endswith("≤ Need:")


def test_the_check_writes_a_row_whether_the_process_fits_or_not(session):
    level(session, "banker", "Worked example 14.5")
    session.pick_request(0)
    for p in (0, 1, 2, 0, 1):
        assert session.try_process(p)
    cv = V.check_view(session.world, session.check(), False)
    assert [(r.name, r.fits) for r in cv.rows] == [("P1", False), ("P2", False), ("P3", True),
                                                   ("P1", True), ("P2", True)]
    assert cv.rows[2] == V.CheckRow("P3", "1 ≤ 1", True, "finishes; Work = 2")
    assert cv.done and cv.box == "Safe: ⟨P3, P1, P2⟩ is a safe sequence."
    assert session.try_process(0) is False, "nothing left to try"


def test_nobody_fits_is_refused_while_a_process_still_fits(session):
    level(session, "banker", "Worked example 14.7")
    session.pick_request(-1)
    assert session.claim_stuck() is False
    assert session.work.msg == "Not yet: P2 still fits in Work = (1, 2, 2)."
    assert not session.work.stuck


def test_nobody_fits_is_accepted_when_it_is_true(session):
    level(session, "banker", "Yours")
    session.pick_request(1)                       # P3 requests (1, 0, 1)
    for p in range(4):
        session.try_process(p)
    assert session.claim_stuck() is True and session.work.stuck
    cv = V.check_view(session.world, session.check(), True)
    assert cv.box == "Unsafe: no unfinished process fits in Work = (1, 1, 2)."
    assert cv.box_kind == "bad"


def test_a_complete_check_of_yours_with_no_request_is_the_safe_sequence_goal(session, seen):
    level(session, "banker", "Yours")
    session.pick_request(-1)
    a, hold, nd = B.bank_state(session.world, session.start)
    for p in B.safe_sequence(a, hold, nd):
        session.try_process(p)
    assert session.goal_met("safe")
    kind, p = seen[-1]
    assert kind == "bank" and p["goal"] == "safe" and p["request"] is None
    assert p["decision"] == "" and p["verdict"].startswith("Safe: ⟨")
    assert all(len(r) == 4 for r in p["rows"]) and p["rows"][0][2] in ("yes", "no")
    built = ev.bank(**{k: p[k] for k in ("lab", "level", "start", "request", "rows", "decision",
                                         "verdict", "goal")})
    assert built[0] == ev.BANK


def test_grant_counts_only_after_a_check_run_to_the_end(session, seen):
    level(session, "banker", "Yours")
    session.pick_request(0)                       # P2 requests (1, 0, 1): safe
    session.decide("grant")
    assert session.work.right and not session.goal_met("grant"), "no check, no goal"
    c = session.check()
    for p in B.safe_sequence(c.avail, c.hold, c.need):
        session.try_process(p)
    session.decide("grant")
    assert session.goal_met("grant")
    kind, p = seen[-1]
    assert (kind, p["goal"], p["decision"]) == ("bank", "grant", "grant")
    assert p["request"] == (1, (1, 0, 1)) and p["verdict"] == "Right: the state stays safe."


def test_wait_counts_only_after_nobody_fits_was_accepted(session, seen):
    level(session, "banker", "Yours")
    session.pick_request(1)
    session.decide("wait")
    assert session.work.right and not session.goal_met("wait")
    for p in range(4):
        session.try_process(p)
    session.claim_stuck()
    session.decide("wait")
    assert session.goal_met("wait") and seen[-1][1]["goal"] == "wait"
    assert len(seen[-1][1]["rows"]) == 4


def test_a_wrong_decision_says_what_the_banker_would_do(session):
    level(session, "banker", "Worked example 14.7")
    session.pick_request(3)                       # P4 asks for more than it declared
    session.decide("grant")
    assert not session.work.right
    assert session.work.verdict == ("Not this time. The banker would call it an error: P4 asks "
                                    "for more than it declared.")
    assert session.check() is None, "test 1 failed: there is nothing to check"


def test_bank_undo_takes_back_the_decision_then_the_claim_then_the_rows(session):
    level(session, "banker", "Yours")
    session.pick_request(1)
    for p in range(4):
        session.try_process(p)
    session.claim_stuck()
    session.decide("wait")
    session.undo()
    assert session.work.decided == "" and session.work.stuck
    session.undo()
    assert not session.work.stuck and len(session.work.picks) == 4
    session.undo()
    assert len(session.work.picks) == 3
    session.reset()
    assert session.peek_work().sel is None and not session.can_reset()


def test_every_order_on_the_bank_level_lists_every_safe_sequence(session):
    level(session, "banker", "Worked example 14.7")
    o = V.safe_orders(session.world, session.start, None)
    assert o.count >= 2 and "⟨P2, P3, P1, P4⟩" in o.sequences
    assert any(q.startswith("⟨P4") for q in o.sequences)
    session.explore_all()
    assert session.tab == "every"

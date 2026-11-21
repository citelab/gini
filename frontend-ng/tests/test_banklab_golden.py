"""The golden checks for T-Lab 03's engine, ported from the reference engine's own test file.

Every number here is printed in Chapter 14 of the course notes (Part III) or was agreed with the
professor on the design mockup: `docs/design/t-lab-03-reference/test_banklab.py` passes these
seventeen checks against the reference engine, and this file passes the same checks against the
port in `gini.domain.banklab`, reading the level file the lab ships. If one of these moves, the
lab no longer matches the notes.

Two more things are checked here because the port is what ships: that the shipped level file
gives the engine exactly the levels, starts and goals the reference level file does (skipped
where the reference folder is not checked out — it is not in the public repository), and that
every goal of every level can be reached by some order of moves, so no chip is unwinnable.

State COUNTS are not asserted beyond what the reference asserts: they depend on what a port keeps
in a state, and nothing on the screen promises them.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest
import yaml
from gini.domain import banklab as B
from gini.domain import tlab_catalog as TC

REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "design" / "t-lab-03-reference"


@pytest.fixture(scope="module")
def L():
    lab = TC.get("t-lab-03")
    assert lab is not None and lab.engine == "bank"
    return {lv.id: lv for lv in lab.levels}


def run(L, level_id, start_label, version_id, seq):
    """seq: list of (process name, kind). Returns the world, version, start, final state, lines."""
    lv0 = L[level_id]
    s = lv0.start(start_label)
    lv = B.world(lv0, s)
    v = lv.version(version_id)
    path = [(lv.procs.index(pn), kind) for pn, kind in seq]
    st, lines = B.replay(lv, v, s, path)
    return lv, v, s, st, lines


def names(lv, ps):
    return [lv.procs[p] for p in ps]


def goal(lv, gid):
    return next(g for g in lv.goals if g.id == gid)


# ---- level 1 · Read the graph ------------------------------------------------------------
def test_worked_example_14_1_turns_the_story_into_a_graph_that_closes_when_p3_asks(L):
    lv, _v, _s, st, _ = run(L, "graph", "Worked example 14.1", "free", [])
    assert B.deadlocked(lv, st) == ()                      # (a) P3 waits for nothing
    lv, _v, _s, st, lines = run(L, "graph", "Worked example 14.1", "free", [("P3", "step")])
    assert names(lv, B.deadlocked(lv, st)) == ["P1", "P2", "P3"]     # (b) the chain closes
    assert lines == ["P3 asks for scanner: waits"]


def test_worked_example_14_2_reduces_figure_14_3_and_then_makes_it_a_knot(L):
    lv, _v, _s, st, _ = run(L, "graph", "Figure 14.3", "free", [])
    assert B.has_cycle(B.edges(lv, st, False)) and B.deadlocked(lv, st) == ()     # (a)
    rows, left = B.reduce(lv, st)
    assert names(lv, [r[0] for r in rows]) == ["P2", "P1", "P3", "P4"] and left == ()
    lv, _v, _s, st, _ = run(L, "graph", "Figure 14.3", "free", [("P4", "step")])
    assert B.deadlocked(lv, st) == ()                                                # (b)
    lv, _v, _s, st, _ = run(L, "graph", "Figure 14.3", "free", [("P4", "step"), ("P2", "step")])
    assert names(lv, B.deadlocked(lv, st)) == ["P1", "P2", "P3", "P4"]               # (c) a knot


def test_level_1_goals_are_reachable_from_yours(L):
    lv, _v, _s, st, _ = run(L, "graph", "Yours", "free", [("P1", "step"), ("P2", "step")])
    assert B.reach_met(lv, goal(lv, "cycle"), st)
    lv, v, s, st, _ = run(L, "graph", "Yours", "free",
                          [("P1", "step"), ("P2", "step"), ("P3", "step"), ("P4", "step")])
    assert B.reach_met(lv, goal(lv, "knot"), st)
    ex = B.explore(lv, v, s)
    labels = [o.label for o in ex.outcomes]
    assert "everyone finishes" in labels and any(lab.startswith("deadlock") for lab in labels)


# ---- level 2 · Claim edges -----------------------------------------------------------------
def test_worked_example_14_4_the_graph_algorithm_refuses_p3_although_r3_is_free(L):
    seq = [("P1", "step"), ("P2", "step"), ("P3", "step"), ("P1", "step"), ("P2", "step")]
    _lv, _v, _s, _st, lines = run(L, "claims", "Worked example 14.4", "graph", seq)
    assert lines == ["P1 asks for R1: granted",
                     "P2 asks for R2: granted",
                     "P3 asks for R3: waits, would close a cycle through claim edges",
                     "P1 asks for R2: waits",
                     "P2 asks for R3: granted"]
    lv3, _, _, st3, _ = run(L, "claims", "Worked example 14.4", "graph", seq[:3])
    assert B.reach_met(lv3, goal(lv3, "refused"), st3)     # P3 waits although R3 is free
    _lv, _v, _s, st, lines = run(L, "claims", "Worked example 14.4", "graph",
                              seq + [("P2", "step"), ("P1", "step"), ("P3", "step"),
                                     ("P3", "step")])
    assert B.finished(st)                                  # P2, then P1, then P3: everybody done


def test_level_2_deadlocks_without_avoidance_and_is_proved_with_it(L):
    lv = L["claims"]
    s = lv.start("Worked example 14.4")
    free = B.explore(lv, lv.version("free"), s)
    assert any(o.label == "deadlock: P1, P2, P3" for o in free.outcomes)
    dl = next(o for o in free.outcomes if o.label.startswith("deadlock"))
    assert len(dl.moves) == 6                              # each takes one, each asks for the next
    graph = B.explore(lv, lv.version("graph"), s)
    assert graph.proved and [o.label for o in graph.outcomes] == ["everyone finishes"]


# ---- level 3 · The banker --------------------------------------------------------------------
def bank(L, start_label):
    lv0 = L["banker"]
    s = lv0.start(start_label)
    return B.world(lv0, s), s


def test_worked_example_14_5_the_banker_says_yes_and_the_check_keeps_its_no_rows(L):
    lv, s = bank(L, "Worked example 14.5")
    t = B.tests(lv, s, 0, (1,))
    assert t["verdict"] == "grant"
    a2, hold2, nd2 = t["provisional"]
    c = B.check_begin(a2, hold2, nd2)
    for p in (0, 1, 2, 0, 1):
        B.check_pick(c, p)
    assert [(r[0], r[3]) for r in c.rows] == [(0, False), (1, False), (2, True), (0, True),
                                              (1, True)]
    assert [r[4] for r in c.rows if r[3]] == [(2,), (5,), (6,)]     # Work = 2, 5, 6
    assert B.check_done(c)


def test_worked_example_14_6_one_changed_maximum_makes_the_banker_say_wait():
    # One number changed from 14.5: P2's maximum is 5, and P2 asks.
    a, hold, nd = (2,), ((2,), (1,), (1,)), ((3,), (4,), (1,))
    hold2, nd2, a2 = ((2,), (2,), (1,)), ((3,), (3,), (1,)), (1,)
    assert B.is_safe(a, hold, nd) and not B.is_safe(a2, hold2, nd2)


def test_worked_example_14_7_has_several_resource_types_and_four_verdicts(L):
    lv, s = bank(L, "Worked example 14.7")
    a, hold, nd = B.bank_state(lv, s)
    assert a == (1, 2, 2)
    assert B.safe_sequence(a, hold, nd) == (1, 2, 0, 3)               # <P2, P3, P1, P4>
    assert any(q[0] == 3 for q in B.all_safe_sequences(a, hold, nd))  # P4 could also go first
    verdicts = [B.tests(lv, s, p, vec)["verdict"] for p, vec in lv.starts[s].requests]
    assert verdicts == ["grant", "wait", "wait", "error"]             # (b) (c) (d) (e)
    assert B.tests(lv, s, 2, (2, 0, 0))["why"] == "the units are not there"


def test_your_turn_14_11_after_the_grant_p4s_request_must_wait(L):
    lv, s = bank(L, "Worked example 14.7")
    _a, hold, nd = B.bank_state(lv, s)
    hold = (hold[0], (3, 0, 2), hold[2], hold[3])                     # (b) granted
    nd = (nd[0], (0, 2, 0), nd[2], nd[3])
    hold2 = hold[:3] + ((1, 2, 3),)
    nd2 = nd[:3] + ((1, 0, 0),)
    assert not B.is_safe((0, 1, 0), hold2, nd2)                       # P4 (0,1,1): waits


def test_level_3_yours_has_one_answer_of_each_kind(L):
    lv, s = bank(L, "Yours")
    a, hold, nd = B.bank_state(lv, s)
    assert a == (2, 1, 3) and B.is_safe(a, hold, nd)
    got = [(lv.procs[p], vec, B.tests(lv, s, p, vec)["verdict"], B.tests(lv, s, p, vec)["why"])
           for p, vec in lv.starts[s].requests]
    assert got == [("P2", (1, 0, 1), "grant", "the state stays safe"),
                   ("P3", (1, 0, 1), "wait", "the state would be unsafe"),      # the same units
                   ("P3", (0, 2, 0), "error", "P3 asks for more than it declared"),
                   ("P1", (3, 0, 0), "wait", "the units are not there")]
    assert lv.starts[s].hide_need
    t = B.tests(lv, s, 2, (1, 0, 1))
    c = B.check_begin(*t["provisional"])
    for p in range(4):
        B.check_pick(c, p)
    assert c.work == (1, 1, 2) and B.check_stuck(c)


# ---- level 4 · Unsafe, but lucky -------------------------------------------------------------
def test_your_turn_14_12_an_unsafe_state_can_still_finish(L):
    lv0 = L["lucky"]
    s = lv0.start("Worked example 14.6")
    lv = B.world(lv0, s)
    ex = B.explore(lv, lv.version("free"), s)
    labels = [o.label for o in ex.outcomes]
    assert "everyone finishes" in labels and "deadlock: P1, P2" in labels


def test_level_4_goals_are_reachable_from_yours(L):
    lv0 = L["lucky"]
    s = lv0.start("Yours")
    lv = B.world(lv0, s)
    a = B.avail(lv, B.start_state(lv, s))
    assert a == (1,)
    nd = [B.need(lv, B.start_state(lv, s), p) for p in range(3)]
    assert not B.is_safe(a, [(3,), (3,), (1,)], nd)                   # the start is unsafe
    _, _, _, st, _ = run(L, "lucky", "Yours", "free",
                         [("P3", "finish"), ("P2", "step"), ("P2", "step"), ("P2", "step"),
                          ("P1", "step"), ("P1", "step"), ("P1", "step"), ("P1", "step")])
    assert B.reach_met(lv, goal(lv, "lucky"), st)
    _, _, _, st, _ = run(L, "lucky", "Yours", "free",
                         [("P3", "step"), ("P1", "step"), ("P2", "step"), ("P3", "step")])
    assert B.reach_met(lv, goal(lv, "unlucky"), st)


# ---- level 5 · Detect and recover ------------------------------------------------------------
def test_worked_example_14_8_says_who_is_deadlocked_and_the_cheapest_victim(L):
    lv, _v, _s, st, _ = run(L, "recover", "Worked example 14.8", "free", [])
    assert names(lv, B.deadlocked(lv, st)) == ["P2", "P3", "P4"]
    assert names(lv, B.victims(lv, st)) == ["P2", "P3", "P4"]
    assert lv.procs[B.cheapest_victim(lv, st)] == "P2"


def test_level_5_waiting_is_not_deadlocked_and_the_cheapest_process_is_not_a_victim(L):
    lv, _v, _s, st, _ = run(L, "recover", "Yours: terminate", "free", [])
    rows, left = B.reduce(lv, st)
    assert names(lv, [r[0] for r in rows]) == ["P2", "P1"]           # P1 waits, not deadlocked
    assert names(lv, left) == ["P3", "P4", "P5"]
    assert names(lv, B.victims(lv, st)) == ["P3", "P4"]              # P5 holds A; they want B, C
    assert lv.procs[B.cheapest_victim(lv, st)] == "P4"
    g = goal(lv, "victim")
    _, _, _, st5, _ = run(L, "recover", "Yours: terminate", "free", [("P5", "kill")])
    assert B.deadlocked(lv, st5) and not B.reach_met(lv, g, st5)
    _, _, _, st4, lines = run(L, "recover", "Yours: terminate", "free", [("P4", "kill")])
    assert B.reach_met(lv, g, st4)
    assert lines[0].startswith("P4 is terminated")


def test_all_cycles_lists_each_cycle_once(L):
    lv, _v, _s, st, _ = run(L, "graph", "Figure 14.3", "free", [])
    assert B.all_cycles(B.edges(lv, st, False)) == [["P1", "R1", "P3", "R2", "P1"]]
    lv, _v, _s, st, _ = run(L, "graph", "Figure 14.3", "free", [("P4", "step"), ("P2", "step")])
    assert len(B.all_cycles(B.edges(lv, st, False))) == 4   # the knot: every pairing of the units


def test_figure_14_9_the_wait_for_graph_drops_the_resources(L):
    lv, _v, _s, st, _ = run(L, "recover", "Figure 14.9", "free", [])
    assert names(lv, B.deadlocked(lv, st)) == ["P1", "P2", "P3", "P4"]
    wf = sorted((a, b) for _, a, b, _ in B.wait_for(lv, st))
    assert wf == [("P1", "P2"), ("P2", "P3"), ("P3", "P4"), ("P4", "P2")]
    assert B.has_cycle(B.wait_for(lv, st))            # P2 -> P3 -> P4 -> P2; P1 only waits behind


def test_three_ways_out_the_cheapest_is_a_preemption(L):
    lv0 = L["recover"]
    s = lv0.start("Yours: three ways out")
    lv = B.world(lv0, s)
    st = B.start_state(lv, s)
    breaks = {m.label.split(" · ")[0]:
              (not B.deadlocked(lv, B.apply(lv, lv.version("free"), st, m)[0]), m.cost)
              for m in B.recovery_moves(lv, st)}
    assert breaks["Roll P2 back to minute 30"] == (False, 10)     # its checkpoint is after B
    assert breaks["Terminate P4"] == (False, 6)                   # nobody wants D
    assert breaks["Roll P4 back to minute 3"] == (False, 3)
    assert breaks["Roll P1 back to minute 10"] == (True, 20)
    assert breaks["Preempt A from P1"] == (True, 15)
    assert B.min_recovery(lv, st) == (15, ((0, "preempt:0"),))
    g = goal(lv, "recovery")
    _, _, _, st1, _ = run(L, "recover", "Yours: three ways out", "free", [("P1", "rollback")])
    assert not B.deadlocked(lv, st1) and not B.reach_met(lv, g, st1)   # works, but 20 > 15
    _, _, _, st2, lines = run(L, "recover", "Yours: three ways out", "free",
                              [("P1", "preempt:0")])
    assert B.reach_met(lv, g, st2)
    assert lines == ["P1 loses A and is rolled back to minute 15, giving back A; P3 gets A"]
    ex = B.explore(lv, lv.version("free"), s)
    assert [o.label for o in ex.outcomes] == ["deadlock: P1, P2, P3, P4"]   # untouched, stuck
    _, _, _, st3, _ = run(L, "recover", "Yours: three ways out", "free",
                          [("P1", "preempt:0"), ("P3", "step"), ("P2", "step"), ("P1", "step"),
                           ("P1", "step"), ("P4", "step"), ("P1", "step")])
    assert B.finished(st3)


# ---- the shipped file ------------------------------------------------------------------------
def test_the_shipped_file_is_released_with_the_hub_summary_and_names_its_engine():
    lab = TC.get("t-lab-03")
    assert lab.released and lab.engine == "bank"
    assert lab.summary == ("Grant or refuse requests, draw the graph, run the safety check by "
                           "hand, and break a deadlock.")
    assert [lv.id for lv in lab.levels] == ["graph", "claims", "banker", "lucky", "recover"]
    assert [lv.n for lv in lab.levels] == [1, 2, 3, 4, 5]


def test_the_shipped_file_says_nothing_about_the_reference_folder():
    text = (Path(B.__file__).parent / "tlabs" / "t-lab-03.yaml").read_text(encoding="utf-8")
    assert "test_banklab.py" not in text and "reference engine" not in text
    assert "one way" not in text.lower()


@pytest.mark.skipif(not (REFERENCE / "t-lab-03.yaml").exists(),
                    reason="the design reference folder is not in the public repository")
def test_the_shipped_file_gives_the_engine_what_the_reference_file_does(L):
    ref = B.levels_from_dict(yaml.safe_load((REFERENCE / "t-lab-03.yaml").read_text("utf-8")))
    assert [lv.id for lv in ref] == list(L)
    for want in ref:
        got = L[want.id]
        assert got == want, f"level {want.id} differs from the reference"
        assert got.starts == want.starts and got.goals == want.goals


def _reachable(lv0, g, cap: int = 6000) -> bool:
    """Some order of moves (recovery included) from the goal's start reaches the goal."""
    s = lv0.start(g.start)
    lv = B.world(lv0, s)
    if g.kind == "prove":
        return B.explore(lv, lv.version(g.version), s).proved
    if lv.mode == "bank":
        a, hold, nd = B.bank_state(lv, s)
        if g.kind == "safe_sequence":
            return B.is_safe(a, hold, nd)
        for p, vec in lv.starts[s].requests:
            t = B.tests(lv, s, p, vec)
            if g.kind == "grant" and t["verdict"] == "grant":
                return True
            if g.kind == "wait_unsafe" and t["why"] == "the state would be unsafe":
                c = B.check_begin(*t["provisional"])
                while not B.check_stuck(c):
                    B.check_pick(c, next(i for i in range(len(c.fin))
                                         if not c.fin[i] and B.vle(c.need[i], c.work)))
                return True
        return False
    v = lv.version(g.version)
    s0 = B.start_state(lv, s)
    seen, todo = {B.key(s0) + str(s0["acts"])}, deque([s0])
    while todo and len(seen) < cap:
        st = todo.popleft()
        if B.reach_met(lv, g, st):
            return True
        for mv in B.moves(lv, st):
            nx = B.apply(lv, v, st, mv)[0]
            k = B.key(nx) + str(nx["acts"])
            if k not in seen:
                seen.add(k)
                todo.append(nx)
    return False


def test_every_goal_of_every_level_can_be_reached(L):
    for lv in L.values():
        for g in lv.goals:
            assert _reachable(lv, g), f"{lv.id}/{g.id} cannot be reached"


def test_count_orders_agrees_with_try_every_order_on_its_outcomes(L):
    lv = L["claims"]
    s = lv.start("Worked example 14.4")
    for vid in ("free", "graph"):
        ex = B.explore(lv, lv.version(vid), s)
        counts = B.count_orders(lv, lv.version(vid), s)
        assert set(counts) == {o.label for o in ex.outcomes}
        assert all(n >= 1 for n in counts.values())

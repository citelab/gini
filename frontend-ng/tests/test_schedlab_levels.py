"""A T-Lab level is data (`gini.domain.schedlab`), and the port says what the mockup said.

Three things are pinned here. The shipped `t-lab-02.yaml` loads into exactly the levels the
reference engine defines, and the Python engine agrees with that engine on every level, version
and starting state — the fixture under `tests/data/` was exported from the JavaScript once, by
`tests/data/t-lab-02-engine_export.js` run against the reference engine, so the check needs no
node here. Re-export it only if the reference engine itself changes, never to make a port pass. The
loader refuses, in one sentence each, every level file that would not run. And a fifth level can
be added by writing YAML alone, which is the whole reason the format exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from gini.domain import schedlab as S

FIXTURE = Path(__file__).parent / "data" / "t-lab-02-engine_2026-10-23.json"


@pytest.fixture(scope="module")
def lab():
    return S.get("t-lab-02")


# ---- the shipped file ----------------------------------------------------------------------- #
def test_the_shipped_file_loads_into_the_four_levels_the_mockup_defines(lab):
    assert [lv.id for lv in lab.levels] == ["lost", "lockvar", "rdv", "buffer"]
    assert [lv.n for lv in lab.levels] == [1, 2, 3, 4]
    assert lab.level("lost").version_ids() == ("line", "instr", "locked")
    assert lab.level("lost").default == "instr"
    assert lab.level("lockvar").version_ids() == ("plain", "tas")
    assert lab.level("rdv").fix is not None and lab.level("rdv").fix.pinned == ("a1", "a2", "b1", "b2")
    assert lab.level("buffer").fix is not None and lab.level("buffer").fix.pinned == ()
    assert lab.level("buffer").start_labels() == ("full", "half full", "empty")
    assert lab.level("lost").count_schedules and not lab.level("lockvar").count_schedules
    assert lab.level("lockvar").mutex_check == "both inside the critical section"
    assert lab.level("buffer").mutex_check == "both in the buffer code"


def test_every_level_has_the_words_the_face_shows(lab):
    for lv in lab.levels:
        assert lv.title and lv.source and lv.setup and lv.asks and lv.hint, lv.id
        assert lv.state_head and lv.state and lv.outcome, lv.id
        assert len(lv.threads) == 2
        assert lv.goals, lv.id


def test_the_catalog_lists_it_and_the_hub_fields_are_filled(lab):
    """Only the scheduler labs: T-Lab 03 and 04 have engines of their own, and the hub lists all
    three through `tlab_catalog` (test_tlab_plumbing)."""
    assert [t.id for t in S.catalog()] == ["t-lab-02"]
    assert lab.title.startswith("T-Lab 02")
    assert lab.summary and lab.released


def _cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['level']}/{c['code_name']}/{c['start']}")
def test_the_port_explores_exactly_as_the_reference_engine_did(lab, case):
    """Totals, witnesses, outcomes, graph points — everything a student sees. Not state counts."""
    lv = lab.level(case["level"])
    code = tuple(tuple(th) for th in case["code"])
    r = S.explore(lv, code, case["start"])
    e = case["explore"]
    assert r.total == e["total"]
    assert r.deadlock_paths == e["dlPaths"]
    assert r.certified == e["certified"]
    assert (list(r.deadlock) if r.deadlock is not None else None) == e["deadlock"]
    assert (list(r.violation) if r.violation is not None else None) == e["violation"]
    assert [{"label": o.label, "correct": o.correct, "paths": o.paths, "moves": list(o.moves)}
            for o in r.outcomes] == e["outcomes"]
    assert sorted(map(list, r.points)) == e["points"]
    assert sorted(map(list, r.stuck_points)) == e["dlPoints"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['level']}/{c['code_name']}/{c['start']}")
def test_the_port_writes_the_same_trace_rows_as_the_reference_engine(lab, case):
    lv = lab.level(case["level"])
    code = tuple(tuple(th) for th in case["code"])
    e = case["explore"]
    for name, rep in case["replays"].items():
        if name == "deadlock":
            moves = e["deadlock"]
        elif name == "violation":
            moves = e["violation"]
        elif name.startswith("outcome"):
            moves = e["outcomes"][int(name[len("outcome"):])]["moves"]
        else:                                           # "played 001101": a hand-played schedule
            moves = [int(ch) for ch in name.split(" ")[1]]
        rp = S.replay(lv, code, moves, case["start"])
        assert [[x.t, x.text, x.kind, x.state, x.note, x.bad] for x in rp.rows] == rep["rows"], name
        assert [list(p) for p in rp.path] == rep["path"], name
        assert (rp.done, rp.deadlock) == (rep["done"], rep["deadlock"]), name


def test_exploration_is_deterministic_run_to_run(lab):
    """The Teaching Center re-checks a submitted proof against this: same code, same witnesses."""
    lv = lab.level("buffer")
    code = lv.code_of("swapped")
    a, b = S.explore(lv, code, 0), S.explore(lv, code, 0)
    assert a == b


# ---- goals ---------------------------------------------------------------------------------- #
def test_a_reach_goal_is_met_at_any_step_not_only_the_last(lab):
    lv = lab.level("lost")
    code = lv.code_of("instr")
    # P P C C P C ends at 4; the goal "end at 4" is met on the last step, "end at 6" never.
    assert S.goals_reached(lv, "instr", code, [0, 0, 1, 1, 0, 1]) == ("g4",)
    # Finding the deadlock and then playing on (nothing can move) still counts.
    rdv = lab.level("rdv")
    assert S.goals_reached(rdv, "bad", rdv.code_of("bad"), [0, 0, 1, 1]) == ("dl",)
    # A goal on another version is never credited to this one.
    assert S.goals_reached(lv, "locked", lv.code_of("locked"), [0, 0, 1, 1, 0, 1]) == ()


def test_the_both_inside_goal_is_met_the_moment_both_are_in(lab):
    lv = lab.level("lockvar")
    assert S.goals_reached(lv, "plain", lv.code_of("plain"), [0, 1, 0, 1]) == ("both",)
    assert S.goals_reached(lv, "plain", lv.code_of("plain"), [0, 1, 0]) == ()


# ---- fix it --------------------------------------------------------------------------------- #
def test_a_move_swaps_with_the_neighbour_and_is_refused_at_the_ends(lab):
    lv = lab.level("buffer")
    code = lv.code_of("swapped")
    moved = S.move(lv, code, 0, 1, -1)
    assert moved[0] == ("pwe", "pwm", "ins", "psm", "psf") and moved[1] == code[1]
    assert S.move(lv, code, 0, 0, -1) == code
    assert S.move(lv, code, 1, 4, +1) == code
    assert code == lv.code_of("swapped"), "the level's own code is never edited in place"


def test_a_pinned_tile_refuses_to_be_moved_or_moved_over(lab):
    lv = lab.level("rdv")
    code = lv.code_of("bad")                       # a1 wb sa a2 — a1 and a2 are pinned
    assert not S.can_move(lv, code, 0, 0, +1)      # a1 itself
    assert not S.can_move(lv, code, 0, 1, -1)      # wb over a1
    assert not S.can_move(lv, code, 0, 2, +1)      # sa over a2
    assert S.can_move(lv, code, 0, 1, +1)          # wb and sa may swap
    assert S.move(lv, code, 0, 1, +1)[0] == ("a1", "sa", "wb", "a2")


def test_a_level_without_a_fix_moves_nothing(lab):
    lv = lab.level("lost")
    assert not S.can_move(lv, lv.code_of("instr"), 0, 0, +1)


def test_the_rendezvous_fix_is_proved_through_the_moves_the_face_allows(lab):
    lv = lab.level("rdv")
    code = S.move(lv, lv.code_of("bad"), 0, 1, +1)
    assert S.prove(lv, code).proved is True, "one side signalling first is already enough"
    code = S.move(lv, code, 1, 1, +1)
    assert S.prove(lv, code).proved is True


# ---- why not -------------------------------------------------------------------------------- #
def test_not_fixed_yet_gives_the_first_reason_in_the_faces_order(lab):
    lv = lab.level("rdv")
    assert S.prove(lv, lv.code_of("bad")).reason == "no schedule finishes"
    lost = lab.level("lost")
    assert S.prove(lost, lost.code_of("instr")).reason == "a schedule gives the wrong answer"
    assert S.prove(lost, lost.code_of("locked")).reason == ""


# ---- the loader's refusals ------------------------------------------------------------------ #
MINIMAL = """
id: toy
title: Toy
levels:
  - id: one
    title: One
    threads: [A, B]
    shared: {x: 0}
    semaphores: {s: 1}
    tiles:
      a: {text: "x = 1", op: set, var: x, value: 1}
      w: {text: "wait(&s)", op: wait, sem: s}
      g: {text: "signal(&s)", op: signal, sem: s}
    versions:
      - {id: v, label: V, code: [[a], [w, g]]}
    state: "x = {x}"
    outcome: "ends with x = {x}"
    goals:
      - {id: done, version: v, chip: Done, text: Finish, reach: {finished: true}}
"""


def _refuses(text: str, sentence: str) -> None:
    with pytest.raises(S.LevelError) as e:
        S.from_yaml(text)
    assert sentence in str(e.value), str(e.value)


def test_the_minimal_level_loads_and_runs():
    lab = S.from_yaml(MINIMAL)
    lv = lab.level("one")
    r = S.explore(lv, lv.code_of("v"))
    assert r.certified and [o.label for o in r.outcomes] == ["ends with x = 1"]


def test_a_tile_in_code_that_is_not_defined_is_refused():
    _refuses(MINIMAL.replace("code: [[a], [w, g]]", "code: [[a, zz], [w, g]]"),
             "uses a tile 'zz' that is not defined")


def test_a_pinned_tile_that_is_not_defined_is_refused():
    _refuses(MINIMAL + "    fix: {from: v, pinned: [nope]}\n",
             "pins a tile 'nope' that is not defined")


def test_a_fix_from_a_version_that_does_not_exist_is_refused():
    _refuses(MINIMAL + "    fix: {from: ghost, pinned: []}\n",
             "fix starts from a version 'ghost' it does not have")


def test_a_goal_on_a_version_that_does_not_exist_is_refused():
    _refuses(MINIMAL.replace("version: v, chip: Done", "version: fix, chip: Done"),
             "goal 'done' is on a version 'fix' the level does not have")


def test_a_goal_on_the_fix_is_fine_once_there_is_one():
    lab = S.from_yaml(MINIMAL.replace("version: v, chip: Done", "version: fix, chip: Done")
                      + "    fix: {from: v, pinned: []}\n")
    assert lab.level("one").goals[0].version == "fix"


def test_a_semaphore_named_in_a_tile_but_not_declared_is_refused():
    _refuses(MINIMAL.replace("op: wait, sem: s", "op: wait, sem: t"),
             "names a semaphore 't' that is not declared")


def test_a_semaphore_named_in_a_note_but_not_declared_is_refused():
    _refuses(MINIMAL + "    semaphore_notes: {t: whatever}\n",
             "note for a semaphore 't' it does not declare")


def test_a_semaphore_named_in_a_start_but_not_declared_is_refused():
    _refuses(MINIMAL + "    starts:\n      - {label: x, semaphores: {t: 1}}\n",
             "start 'x' sets a semaphore 't' that is not declared")


def test_an_op_the_engine_does_not_know_is_refused():
    _refuses(MINIMAL.replace("op: set,", "op: setv,"), "'setv' is not an op the engine knows")


def test_a_level_with_other_than_two_threads_is_refused():
    _refuses(MINIMAL.replace("threads: [A, B]", "threads: [A, B, C]"),
             "must name exactly two threads")
    _refuses(MINIMAL.replace("code: [[a], [w, g]]", "code: [[a], [w], [g]]"),
             "exactly two threads")


def test_a_goal_must_either_reach_or_prove():
    _refuses(MINIMAL.replace("reach: {finished: true}", "reach: {finished: true}, prove: true"),
             "must say either what to reach or that it is a proof")


def test_both_inside_cannot_be_asked_of_a_level_that_does_not_check_it():
    _refuses(MINIMAL.replace("reach: {finished: true}", "reach: both_inside"),
             "without mutex_check")


def test_a_fifth_level_is_yaml_alone(lab):
    """The format is the contract later labs are written against: a new level needs no code."""
    text = Path(S.TLABS_DIR / "t-lab-02.yaml").read_text(encoding="utf-8")
    text += MINIMAL.split("levels:\n", 1)[1].replace("  - id: one", "  - id: five")
    bigger = S.from_yaml(text)
    assert [lv.id for lv in bigger.levels] == ["lost", "lockvar", "rdv", "buffer", "five"]
    assert bigger.level("five").n == 5
    assert S.explore(bigger.level("five"), bigger.level("five").code_of("v")).certified


def test_every_shipped_level_file_is_packaged():
    """`catalog()` reads `tlabs/*.yaml` from the installed package, so the wheel must carry it."""
    text = (Path(S.__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tlabs/*.yaml"' in text

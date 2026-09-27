"""The grader's cooked view: the facts that decide a grade, read out of the raw log.

A TA marking thirty submissions had one long transcript per student — every console open, every
kernel observation (48 of 67 lines in the busiest real chain), every lab face — and had to read
all of it to find five facts. The Teaching Center now shows a COOKED view first and the raw log,
unchanged, underneath.

The rule these pin is the maintainer's: the raw log is evidence and is never edited. The cooked
view is only a reading of it, and every line in it must come from recorded entries. The one
change to the raw log is ADDING the test output the chain always captured and the transcript
never printed.

Chains are built with the real `proof_events` constructors, so they have exactly the shape
gBuilder records.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from gini.domain import grader_view as G
from gini.domain import proof_events as ev

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

LAB = "A-Lab 01 — sysinfo"
T0 = 1_790_000_000.0


def _src(**hashes):
    return {n: {"sha256": h, "lines": 10} for n, h in hashes.items()}


def _grade(metric, ok=True, pending=False, summary=""):
    return ev.measure(f"{LAB} · {metric}", {"ok": ok, "pending": pending, "summary": summary})


def _chain(*steps, gap=60.0):
    """(kind, data) steps → a proof dict, one minute apart unless a step says otherwise."""
    entries, t = [], T0
    all_steps = [("genesis", {"ticket": "ABCD1234EFGH", "assignment": "syscall-sysinfo"}),
                 *steps, ("submit", {"artifact": {}, "objectives": []})]
    for i, step in enumerate(all_steps):
        if isinstance(step, float):              # a pause
            t += step
            continue
        kind, data = step
        entries.append({"kind": kind, "seq": i, "t": t, "data": data, "prev": ""})
        t += gap
    return {"entries": entries}


def _alab_chain():
    """A realistic evening: two failed Loads, a working one, a revert, a Part C that never landed,
    three test runs, a check of their work, then the graded submit."""
    return _chain(
        ev.tune("M1", "assignment", "", "syscall-sysinfo"),
        ev.build("M1", "syscall-sysinfo", False, _src(**{"syscall.h": "a1", "sysproc.c": "b1"}),
                 ["wiring 3/16 · A 3/6 · B 0/6 · C 0/4", "error: expected ';'"]),
        ev.build("M1", "syscall-sysinfo", False, _src(**{"syscall.h": "a2", "sysproc.c": "b1"}),
                 ["wiring 6/16 · A 6/6 · B 0/6 · C 0/4", "undefined reference"]),
        ev.build("M1", "syscall-sysinfo", True, _src(**{"syscall.h": "a2", "sysproc.c": "b2"}),
                 ["wiring 6/16 · A 6/6 · B 0/6 · C 0/4", "loaded"]),
        ev.spawn("M1", "sysinfotest", out=["free bytes:  0"], test=True),
        ev.build("M1", "syscall-sysinfo", True, _src(**{"syscall.h": "a2", "sysproc.c": "b2"}),
                 ["wiring 6/16 · A 6/6 · B 0/6 · C 0/4", "kalloc.c put back — press Load to build it"],
                 action="revert"),
        ev.build("M1", "syscall-sysinfo", True,
                 _src(**{"syscall.h": "a2", "sysproc.c": "b3", "kalloc.c": "k3"}),
                 ["wiring 14/16 · A 6/6 · B 6/6 · C 2/4", "loaded"]),
        ev.spawn("M1", "sysinfotest", out=["free bytes:  133480448"], test=True),
        _grade("freemem_tracks"), _grade("load_responds", ok=False),   # "Check my work"
        ev.spawn("M1", "sysinfotest", out=["free bytes:  133480448", "processes: 3"], test=True),
        ev.answer("q1", "Why a delta?", "Because the two counts sit apart but move together."),
        # the graded reading at submit — the LAST of each metric is the one that counts
        _grade("named"), _grade("freemem_tracks"), _grade("freemem_plausible"),
        _grade("nproc_agrees"), _grade("states_agree"), _grade("uptime_agrees"),
        _grade("load_responds", ok=False, summary="the 5s average never rose"),
    )


def _report(proof, shadows=None, questions=None):
    from gini_teaching_center import activities as ACT
    row = {"data": json.dumps({"proof": proof, "shadows": shadows or {}}), "receipt": "R1",
           "activity": "lab1", "started": T0, "finished": T0 + 3600, "verdict": "pass"}
    return ACT.report(row, {"title": LAB}, [], [], questions or [])


QUESTIONS = [{"id": "q1", "prompt": "Why a delta?", "answer": "counts move together"},
             {"id": "q2", "prompt": "Why no floating point?", "answer": "FP regs not saved"}]
SHADOWS = {"M1/syscall.h": {"sha256": "a2", "lines": 30},
           "M1/sysproc.c": {"sha256": "b9", "lines": 80},     # edited after the last Load
           "M1/kalloc.c": {"sha256": "k3", "lines": 90}}


# --------------------------------------------------------------------------- #
# the cooked view
# --------------------------------------------------------------------------- #

@pytest.fixture
def cooked():
    return _report(_alab_chain(), SHADOWS, QUESTIONS)["cooked"]


def test_grading_is_the_reading_at_submit_not_every_check(cooked):
    g = cooked["grading"]
    assert (g["passed"], g["total"]) == (6, 7)
    assert [f["name"] for f in g["failed"]] == ["load_responds"]
    assert g["failed"][0]["summary"] == "the 5s average never rose"
    assert g["rounds"] == 2, "they checked their work once before submitting"


def test_wiring_is_per_part_at_the_last_load(cooked):
    assert cooked["wiring"]["parts"] == {"A": [6, 6], "B": [6, 6], "C": [2, 4]}


def test_builds_say_how_it_went_and_when_it_first_worked(cooked):
    b = cooked["builds"]
    assert (b["loads"], b["failed"], b["built"]) == (4, 2, 2)
    assert b["last_ok"] is True
    assert b["first_ok_minutes_in"] == 4.0          # genesis, tune, 2 failures, then it built


def test_a_revert_is_named_by_the_file_it_put_back(cooked):
    assert [r["file"] for r in cooked["reverts"]] == ["kalloc.c"]


def test_the_test_shows_what_it_printed_last(cooked):
    t = cooked["tests"]
    assert (t["prog"], t["runs"]) == ("sysinfotest", 3)
    assert t["last_out"] == ["free bytes:  133480448", "processes: 3"]


def test_files_changed_during_the_work_are_listed_as_exactly_that(cooked):
    assert cooked["changed_between_loads"] == ["kalloc.c", "syscall.h", "sysproc.c"]


def test_look_at_names_what_a_grader_must_not_miss(cooked):
    look = " | ".join(cooked["look_at"])
    assert "load_responds failed — the 5s average never rose" in look
    assert "Part C 2/4" in look
    assert "sysproc.c changed after the last successful Load" in look
    assert "Unanswered: question 2" in look


def test_answers_are_in_the_cooked_view(cooked):
    assert cooked["answers"][0]["given"].startswith("Because the two counts")
    assert cooked["answers"][1]["answered"] is False


def test_time_and_sittings():
    proof = _chain(ev.tune("M1", "assignment", "", "x"), 45 * 60.0,
                   ev.spawn("M1", "sysinfotest", out=["ok"], test=True))
    c = G.cook(proof["entries"])
    assert c["sittings"] == 2, "a 45-minute silence is a second sitting"


# --------------------------------------------------------------------------- #
# the flags that change how a submission reads
# --------------------------------------------------------------------------- #

def test_a_submission_with_no_working_kernel_says_so():
    proof = _chain(ev.build("M1", "s", False, {}, ["wiring 2/16", "error"]),
                   ev.build("M1", "s", False, {}, ["wiring 2/16", "error"]))
    look = " ".join(G.cook(proof["entries"])["look_at"])
    assert "No Load ever built" in look


def test_a_failed_last_load_warns_the_graded_kernel_is_older():
    proof = _chain(ev.build("M1", "s", True, {}, ["wiring 16/16"]),
                   ev.build("M1", "s", False, {}, ["wiring 16/16", "error"]))
    assert any("LAST Load failed" in x for x in G.cook(proof["entries"])["look_at"])


def test_an_older_gbuilders_total_only_wiring_still_reads():
    proof = _chain(ev.build("M1", "s", True, {}, ["wiring 12/16", "loaded"]))
    c = G.cook(proof["entries"])
    assert (c["wiring"]["passed"], c["wiring"]["total"], c["wiring"]["parts"]) == (12, 16, {})
    assert any("Wiring 12/16" in x for x in c["look_at"])


def test_a_test_never_run_is_flagged_for_an_os_lab():
    proof = _chain(ev.build("M1", "s", True, {}, ["wiring 16/16"]))
    assert any("never run" in x for x in G.cook(proof["entries"])["look_at"])


def test_a_network_lab_gets_its_own_row_and_no_build_rows():
    proof = _chain(ev.place("d1", "host", "M1"), ev.place("d2", "router", "R1"),
                   ev.connect("M1", "host", "R1", "router"), ev.run(True, "ok"),
                   ev.witness("reach(M1 -> R1)", "ok"))
    c = G.cook(proof["entries"])
    assert c["network"]["placed"] == 2 and c["network"]["checks_passed"] == 1
    assert "builds" not in c and "tests" not in c


# --------------------------------------------------------------------------- #
# the raw log stays the raw log
# --------------------------------------------------------------------------- #

def test_the_raw_log_is_not_edited_by_the_cooked_view():
    from gini_teaching_center import activities as ACT
    proof = _alab_chain()
    rep = _report(proof, SHADOWS, QUESTIONS)
    assert rep["narration"] == ACT.narrate(proof), "the report must render the log as-is"
    # every recorded entry still has its line in the transcript
    timeline = rep["narration"].split("WHAT THEY DID", 1)[1].split("WHAT THE CHAIN SHOWS")[0]
    assert len([ln for ln in timeline.splitlines() if ln.startswith("  ") and ":" in ln[:8]]) \
        == len(proof["entries"])


def test_the_raw_log_now_shows_what_the_test_printed():
    rep = _report(_alab_chain())
    assert "Launched sysinfotest on M1 — the assignment's test." in rep["narration"]
    assert "| free bytes:  133480448" in rep["narration"]


def test_answers_stay_in_the_raw_log_too():
    rep = _report(_alab_chain(), questions=QUESTIONS)
    assert "Because the two counts sit apart but move together." in rep["narration"]


def test_a_chain_the_cooked_view_cannot_read_still_renders_the_report(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad chain")
    monkeypatch.setattr(G, "cook", boom)
    rep = _report(_alab_chain())
    assert "could not build the grader's view" in rep["cooked"]["error"]
    assert rep["narration"].startswith("PROOF OF ACTIVITY")

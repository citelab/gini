"""What GINI measured, as something a marker can read.

The chain has carried these since the probes and riders shipped: `measure` from a Source/Sink
rider, `witness` from a behavioural probe, `objective` from an objective changing state. The
report had no section for them, so they reached a teacher only as a clause inside the narration —
"…3 measurement(s)" — which is prose to read rather than a result to check. A networking lab's
numbers were effectively invisible at marking time, and an OS lab's will be too the moment
A-Labs start measuring anything.

The rule these follow is the one `check_sources` already follows, and it is the point of v1:
**describe, never judge.** `ok` is what GINI observed. Whether three of five probes passing is a
pass is the teacher's call, and nothing here computes a mark.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))


def _chain(*entries) -> dict:
    return {"entries": [{"kind": k, "seq": i, "t": float(i), "data": d}
                        for i, (k, d) in enumerate(entries)]}


def _probe(name, verdict):
    return "witness", {"probe": name, "verdict": verdict}


def _rider(name, ok=True, **measurement):
    return "measure", {"name": name, "ok": ok, "measurement": measurement, "summary": ""}


def _objective(oid, say="", before="open", after="met"):
    return "objective", {"id": oid, "say": say, "from": before, "to": after}


# --------------------------------------------------------------------------- #
# the rows
# --------------------------------------------------------------------------- #

def test_a_probe_verdict_becomes_a_row():
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_probe("reach(M1 -> M2)", "ok")))
    assert rows == [{"kind": "probe", "name": "reach(M1 -> M2)", "ok": True, "pending": False,
                     "detail": "ok", "summary": "", "seq": 0, "t": 0.0}]


def test_a_riders_reading_keeps_its_numbers():
    """The measurement is the point — a marker wants the loss percentage, not "a rider ran"."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_rider("S1", loss="0%", pps="480")))
    assert rows[0]["kind"] == "measurement"
    assert rows[0]["detail"] == "loss=0%, pps=480"


def test_an_objective_reports_the_change_it_made():
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_objective("obj-1", "subnets reach each other")))
    assert rows[0]["detail"] == "open -> met"
    assert rows[0]["summary"] == "subnets reach each other"


def test_an_objective_carries_no_verdict():
    """It moved from one status to another; the statuses are the activity's own words. Calling
    that "passed" would be the report inventing a judgement, which is the one thing it does not
    do — and it read as "1/1 objective passed" until this was fixed."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_objective("obj-1")))
    assert rows[0]["ok"] is None
    assert ACT.measurement_tally(rows)["objective"] == {"ok": 0, "pending": 0, "total": 1}


def test_every_kind_lands_in_ONE_list():
    """A C-Lab's throughput, a T-Lab's reachability and an OS lab's kernel metric are the same
    thing to a marker. Three sections would make a teacher learn which lab produces which."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_probe("p", "ok"), _rider("r"), _objective("o")))
    assert [r["kind"] for r in rows] == ["probe", "measurement", "objective"]


def test_entries_that_are_not_measurements_are_ignored():
    from gini_teaching_center import activities as ACT
    chain = _chain(("build", {"ok": True}), ("spawn", {"what": "ping"}), _probe("p", "ok"))
    assert len(ACT.measurements(chain)) == 1


# --------------------------------------------------------------------------- #
# the rules that matter for marking
# --------------------------------------------------------------------------- #

def test_a_repeat_is_kept_and_in_order():
    """A probe that failed and then passed is the story of the attempt. Collapsing to the last
    verdict hides the work and flatters the student; collapsing to the first does the opposite."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_probe("reach", "fail"), _probe("reach", "ok")))
    assert [r["detail"] for r in rows] == ["fail", "ok"]
    assert [r["seq"] for r in rows] == [0, 1]


def test_pending_is_not_a_failure():
    """"They pressed Check with nothing running" is recorded on purpose. Reporting it as a
    failure would mark a student down for an attempt they never made."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_probe("reach", "pending")))
    assert rows[0]["pending"] is True and rows[0]["ok"] is False
    tally = ACT.measurement_tally(rows)
    assert tally["probe"] == {"ok": 0, "pending": 1, "total": 1}


def test_the_tally_counts_what_the_rows_say():
    """Counted FROM the rows, so the summary at the top of the section and the list under it can
    never disagree about what happened."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(
        _probe("a", "ok"), _probe("b", "fail"), _probe("c", "pending"),
        _rider("S1"), _rider("S2", ok=False), _objective("o")))
    assert ACT.measurement_tally(rows) == {
        "probe": {"ok": 1, "pending": 1, "total": 3},
        "measurement": {"ok": 1, "pending": 0, "total": 2},
        "objective": {"ok": 0, "pending": 0, "total": 1}}


def test_nothing_here_computes_a_mark():
    """v1 describes and the teacher judges. A percentage in this dict would be a score, and the
    whole report is built on not having one."""
    from gini_teaching_center import activities as ACT
    rows = ACT.measurements(_chain(_probe("a", "ok"), _probe("b", "fail")))
    tally = ACT.measurement_tally(rows)
    flat = repr(rows) + repr(tally)
    for word in ("score", "grade", "mark", "percent", "%)"):
        assert word not in flat.lower(), f"{word!r} appeared in a section that only describes"


# --------------------------------------------------------------------------- #
# a lab that measures nothing must look exactly as it always did
# --------------------------------------------------------------------------- #

def test_a_chain_with_no_measurements_produces_no_section():
    from gini_teaching_center import activities as ACT
    assert ACT.measurements({"entries": []}) == []
    assert ACT.measurements({}) == []
    assert ACT.measurement_tally([]) == {}
    assert ACT.measurement_tally(None) == {}


def test_a_malformed_entry_does_not_take_the_report_down():
    """A report must still render. The same reasoning as `narrate`'s except-and-say-so."""
    from gini_teaching_center import activities as ACT
    chain = {"entries": [{"kind": "witness"}, {"kind": "measure", "data": None},
                         {"kind": "objective", "data": {}}]}
    rows = ACT.measurements(chain)
    assert len(rows) == 3
    assert rows[0]["name"] == "" and rows[1]["detail"] == ""


def test_the_report_carries_the_section():
    """Wired into `report()`, not just available to call."""
    import json

    from gini_teaching_center import activities as ACT
    payload = {"proof": _chain(_probe("reach(M1 -> M2)", "ok"), _rider("S1", loss="0%"))}
    rep = ACT.report({"receipt": "R", "data": json.dumps(payload)}, {}, [])
    assert [m["name"] for m in rep["measurements"]] == ["reach(M1 -> M2)", "S1"]
    assert rep["measurement_tally"]["probe"]["ok"] == 1


def test_the_console_renders_it():
    """The section exists for a person to read; JSON nobody draws is not a feature."""
    html = (_TC / "gini_teaching_center" / "static" / "console.html").read_text(encoding="utf-8")
    assert "measBlock(r.measurements, r.measurement_tally)" in html, "never called"
    assert "function measBlock(" in html, "never defined"
    assert "What GINI measured" in html

"""What T-Lab 03 and 04 share with every lab: the hub finds them, the chain records them, and the
Teaching Center shows them as "not checked" rather than accusing anyone.

T-Lab 02's engine read every level file in `tlabs/`; T-Lab 03 and 04 have engines of their own, so
`tlab_catalog` picks one per file and `schedlab.catalog` keeps only the files it can read — the
Teaching Center replays `schedule` entries through it and must keep finding exactly those.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from gini.domain import narration as N
from gini.domain import proof as P
from gini.domain import proof_events as ev
from gini.domain import schedlab as S
from gini.domain import ticket as T
from gini.domain import tlab_catalog as TC

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))


# --------------------------------------------------------------------------- #
# the catalog
# --------------------------------------------------------------------------- #

def test_every_shipped_t_lab_loads_through_its_own_engine():
    labs = TC.catalog()
    assert [lab.id for lab in labs][:3] == ["t-lab-02", "t-lab-03", "t-lab-04"]
    assert all(lab.engine in TC.ENGINES for lab in labs)


def test_the_scheduler_engine_sees_only_scheduler_labs():
    assert all(lab.engine == "sched" for lab in S.catalog())
    assert S.get("t-lab-02") is not None


def test_a_level_file_without_an_engine_is_a_scheduler_lab():
    assert TC.engine_of({"id": "x"}) == "sched"


def test_an_unknown_engine_is_refused_in_one_sentence():
    with pytest.raises(TC.CatalogError, match="no T-Lab engine called 'quantum'"):
        TC.from_dict({"id": "x", "engine": "quantum"})


# --------------------------------------------------------------------------- #
# the chain
# --------------------------------------------------------------------------- #

def _entry(kind, data, t=1.0):
    class E:
        pass
    e = E()
    e.kind, e.data, e.t = kind, data, t
    return e


def test_each_new_kind_is_an_operation_and_narrates_in_one_line():
    built = [ev.allocate("t-lab-03", "graph", "free", 0, [[0, "ask"], [1, "finish"]],
                         "deadlock", "P2, P3 deadlocked"),
             ev.bank("t-lab-03", "banker", 2, (0, (0, 1, 0)), [["P2", "yes"]], "wait",
                     "right", "wait"),
             ev.fs_walk("t-lab-04", "walk", "Tutorial", None, "/users/raj/lab/main.c",
                        [2, 40, 3], "main_c"),
             ev.fs_read("t-lab-04", "find", "Yours", None, 300000, ["double", 1, 61], "three"),
             ev.fs_make("t-lab-04", "holes", [[0, 2000], [1000000, 1]], "big_thin"),
             ev.fs_trace("t-lab-04", "append", "Random", 7, "append", 11500, 3000,
                         [[54, "R"], [62, "W"]], "index_first", hints=1)]
    for kind, data in built:
        assert kind in ev.OPERATION and kind in ev.TLAB_GOALS
        line = N.describe(_entry(kind, data))
        assert "\n" not in line and data["lab"] in line and line.endswith(".")
    kind, data = ev.orders("t-lab-03", "claims", "graph", 0, True,
                           summary={"states": 40, "outcomes": [["all done", 12]]})
    assert kind in ev.WITNESSED and "proved" in N.describe(_entry(kind, data))


def test_a_random_start_keeps_its_seed_and_clicks_are_clipped():
    _k, d = ev.fs_trace("t-lab-04", "append", "Random", 41, "append", 0, 1,
                        [[i, "R"] for i in range(500)], "g")
    assert d["seed"] == 41 and len(d["steps"]) == 200


class _Ctx:
    """Just enough of AppContext for a recorder that is armed and never attached."""
    topology = None


def test_the_recorder_takes_a_window_payload_by_the_builders_own_field_names(tmp_path):
    from gini.domain.topology import Topology

    from gini.services.proof_recorder import ProofRecorder
    ctx = _Ctx()
    ctx.topology = Topology()
    r = ProofRecorder(ctx, store=P.ChainStore(tmp_path))
    assert r.arm(T.mint().pretty)[0]
    r.note_tlab("allocate", {"lab": "t-lab-03", "level": "graph", "version": "free",
                             "start": 1, "moves": [[0, "ask"]], "goal": "knot",
                             "result": "", "smuggled": "not a field"})
    r.note_tlab("nonsense", {"lab": "x"})
    last = r._chain.entries[-1]
    assert last.kind == ev.ALLOCATE and last.data["goal"] == "knot"
    assert "smuggled" not in last.data


# --------------------------------------------------------------------------- #
# the Teaching Center
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
def test_the_teaching_center_shows_them_as_not_checked_never_as_failing():
    from gini_teaching_center import activities as A
    kinds = [ev.allocate("t-lab-03", "graph", "free", 0, [[0, "ask"]], "deadlock", ""),
             ev.orders("t-lab-03", "claims", "graph", 0, True),
             ev.fs_make("t-lab-04", "holes", [[0, 1]], "big_thin")]
    proof = {"entries": [{"kind": k, "seq": i, "t": float(i), "data": d}
                         for i, (k, d) in enumerate(kinds)]}
    rows = A.recheck_tlabs(proof)
    assert len(rows) == 3
    assert all(r["confirmed"] is None and r["why"].startswith("not checked") for r in rows)
    shown = A.measurements(proof)
    assert {m["kind"] for m in shown} == {"schedule", "proof"}

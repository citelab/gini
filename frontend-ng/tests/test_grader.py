"""Server-side re-grade + gradebook: structural objectives re-evaluated from the snapshot are
authoritative (catch tampering), behavioral results are trusted from the client, and submissions
aggregate into a roster×lesson gradebook."""
from gini.domain import grader as G, lesson as L
from gini.domain.topology import Topology


def _lesson():
    # a lesson mixing a structural objective (server re-grades it authoritatively) with two
    # behavioral ones (server trusts the client until the Docker re-grader lands)
    return L.from_dict({
        "id": "lab03", "time_limit": "25m",
        "intent": {"concept": "vpc-networking", "spirit": "reachability"},
        "objectives": [
            {"id": "in-boundary", "say": "DB inside the VPC", "kind": "structural",
             "check": "contains(VPC1, DB1)"},
            {"id": "reaches", "say": "web reaches db", "kind": "behavioral",
             "probe": "reach(WEB1 -> DB1) == ok"},
            {"id": "shielded", "say": "db shielded", "kind": "behavioral",
             "probe": "reach(NET -> DB1) == fail"}],
        "complete_when": "all"})


def _complete_snapshot():
    t = Topology(); v = t.add_device("vpc", "VPC1")
    t.add_device("web_app", "WEB1", parent_id=v.id)
    t.add_device("database", "DB1", parent_id=v.id)
    return G.snapshot_of(t)


def test_snapshot_world_evaluates_structural():
    w = G.world_from_snapshot(_complete_snapshot())
    from gini.domain import objectives as O
    assert O.evaluate_check("contains(VPC1, DB1)", w)
    assert O.evaluate_check("exists(database)", w)


def test_regrade_structural_leaves_behavioral_pending():
    res = {r.id: r.status for r in G.regrade_structural(_lesson(), _complete_snapshot())}
    assert res["in-boundary"] == "met"
    assert res["reaches"] == "pending" and res["shielded"] == "pending"


def test_official_result_honest_submission():
    sub = {"snapshot": _complete_snapshot(),
           "objective_results": [("in-boundary", "met"), ("reaches", "met"), ("shielded", "met")],
           "time_taken": 300, "band": "gold"}
    r = G.official_result(_lesson(), sub)
    assert r.band == "gold" and not r.regraded_down


def test_official_result_catches_structural_tampering():
    # client claims in-boundary met, but the snapshot has no VPC containment
    t = Topology(); t.add_device("web_app", "WEB1"); t.add_device("database", "DB1")
    sub = {"snapshot": G.snapshot_of(t),
           "objective_results": [("in-boundary", "met"), ("reaches", "met"), ("shielded", "met")],
           "time_taken": 300, "band": "gold"}
    r = G.official_result(_lesson(), sub)
    assert r.band != "gold" and r.regraded_down and "re-grade disagreed" in r.note


def test_late_submission_is_not_gold():
    sub = {"snapshot": _complete_snapshot(),
           "objective_results": [("in-boundary", "met"), ("reaches", "met"), ("shielded", "met")],
           "time_taken": 9999, "band": "gold"}       # over the 25m limit
    r = G.official_result(_lesson(), sub)
    assert r.band == "pass"                            # complete but not on time


def test_gradebook_aggregates_best_band():
    gb = G.gradebook([
        {"student": "alice", "lesson_id": "lab03", "band": "partial"},
        {"student": "alice", "lesson_id": "lab03", "band": "gold"},
        {"student": "bob", "lesson_id": "lab03", "band": "pass"},
        {"student": "bob", "lesson_id": "lab01", "band": "incomplete"},
    ])
    assert gb.band("alice", "lab03") == "gold"        # best across attempts
    assert gb.students() == ["alice", "bob"]
    assert set(gb.lessons()) == {"lab01", "lab03"}
    assert gb.completed_count("bob") == 1             # lab03 pass counts, lab01 incomplete doesn't

"""DOs / DON'Ts: extracting negative intent, honoring it in assembly, and reporting infeasibility
when a requested thing inherently needs an excluded thing."""
from gini.domain import assembly as A, constraints as C, fragments as F, lesson as L


def test_negation_scan_extracts_dont_wants():
    ex = C.from_text("a multi-LAN to the Internet via a firewall, no metrics and dashboards")
    assert "observe" in ex.layers
    assert {"metrics", "dashboard"} <= ex.types


def test_negation_scan_stops_at_a_but_clause():
    # "no dashboard but add a load generator" — only the dashboard is excluded
    ex = C.from_text("no dashboard but add a load generator")
    assert "observe" in ex.layers
    assert "exercise" not in ex.layers            # the load generator is AFTER 'but' → not excluded


def test_positive_mentions_are_not_excluded():
    # a plain build that just names a dashboard is NOT a "don't" — no negation cue
    ex = C.from_text("build a dashboard fed by a metrics source")
    assert not ex


def test_assembly_suppresses_an_excluded_layer():
    ex = C.from_text("switched LAN, no dashboards or metrics")
    les = A.assemble(["basic-lan"], lesson_id="lan", exclude=ex)
    layers = {F.get(fid).layer for fid in les.fragments}
    assert "observe" not in layers               # the observe layer was left out
    assert not any(t in o.check for o in les.objectives for t in ("metrics", "dashboard"))
    assert L.is_valid(les)


def test_objective_conflict_flags_infeasibility():
    # observe-it (a dashboard core) needs a metrics element; excluding metrics makes it infeasible
    ex = C.from_text("a dashboard but no metrics")
    les = A.assemble(["observe-it"], lesson_id="ob", exclude=ex)
    conflicts = C.objective_conflicts(les.objectives, ex)
    assert "metrics" in conflicts

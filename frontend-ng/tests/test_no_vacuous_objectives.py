"""No mission objective may be green on an EMPTY canvas.

A bare negative predicate (`not path(cloud, database)`) passes vacuously when nothing exists — the
student opens a fresh board and an objective is already ticked. Every negative must be guarded by the
existence of the thing it protects. This test guards the whole fragment library, so a future pack
can't reintroduce the bug.
"""
from gini.domain import fragments as F, objectives as O
from gini.domain.topology import Topology


def test_nothing_is_satisfied_on_an_empty_canvas():
    empty = O.TopologyWorld(Topology())
    offenders = []
    for frag in F.all_fragments():
        for t in frag.objectives:
            if t.kind != "structural" or not t.check:
                continue
            try:
                if O.evaluate_check(t.check, empty):
                    offenders.append(f"{frag.id}:{t.id} — {t.check}")
            except O.PredicateError:
                pass
    assert not offenders, ("these objectives are TRUE on an empty board (vacuous pass): "
                           + "; ".join(offenders))


def test_the_shield_objective_requires_the_database_to_exist():
    check = next(t.check for t in F.get("reachability-boundary").objectives if t.id == "shielded")
    empty = O.TopologyWorld(Topology())
    assert not O.evaluate_check(check, empty)          # not green before you build anything

    t = Topology()
    v = t.add_device("vpc", "V")
    w = t.add_device("web_app", "W", parent_id=v.id)
    d = t.add_device("database", "D", parent_id=v.id)
    t.add_link(w.id, d.id)
    assert O.evaluate_check(check, O.TopologyWorld(t))  # green once the DB exists and is unexposed

    net = t.add_device("cloud", "NET")                  # wire the DB straight to the Internet…
    t.add_link(net.id, d.id)
    assert not O.evaluate_check(check, O.TopologyWorld(t))   # …and the shield correctly breaks

"""Behavioral probes: parsing the probe language, evaluating against a runner (GINI as oracle),
and the objective/mission integration where behavioral objectives are pending without a runtime
and resolve once one is present. Uses a hand-authored behavioral lesson (the seed archetypes are
structural / completable-without-Docker)."""
from gini.domain import lesson as L, objectives as O, probes as P
from gini.domain.topology import Topology

# a lesson mixing a structural objective with two behavioral probes (fixed names, since a probe
# targets specific endpoints — a real lesson gets those from staging / the resolver)
_BEHAVIORAL = {
    "id": "lab03", "time_limit": "25m",
    "intent": {"concept": "vpc-networking", "spirit": "reachability"},
    "objectives": [
        {"id": "in-boundary", "say": "DB inside the VPC", "kind": "structural",
         "check": "contains(VPC1, DB1)"},
        {"id": "reaches", "say": "web reaches db", "kind": "behavioral", "probe": "reach(WEB1 -> DB1) == ok"},
        {"id": "shielded", "say": "db shielded", "kind": "behavioral", "probe": "reach(NET -> DB1) == fail"},
    ],
    "complete_when": "all",
}


def _behavioral():
    return L.from_dict(_BEHAVIORAL)


def _named_vpc():
    t = Topology(); v = t.add_device("vpc", "VPC1")
    t.add_device("web_app", "WEB1", parent_id=v.id)
    t.add_device("database", "DB1", parent_id=v.id)
    return t


def test_parse_probe_forms():
    assert P.parse("reach(A -> B) == ok") == P.Probe("reach", "A", "B", None, True)
    assert P.parse("reach(A -> B) == fail").expect_ok is False
    assert P.parse("http(A -> B:5432) == ok") == P.Probe("http", "A", "B", 5432, True)
    assert P.parse("balances(LB1, >= 3)") == P.Probe("balances", "LB1", n=3)
    assert P.parse("ping(A -> B) == ok").kind == "ping"
    assert not P.probe_ok("garbage(")


def test_evaluate_against_runner():
    r = P.FakeRunner({("reach", "WEB1", "DB1", None): True,
                      ("reach", "NET", "DB1", None): False,
                      ("http", "A", "B", 80): True,
                      ("backends", "LB1"): 3})
    assert P.evaluate("reach(WEB1 -> DB1) == ok", r)
    assert P.evaluate("reach(NET -> DB1) == fail", r)      # expect fail, got fail → met
    assert not P.evaluate("reach(NET -> DB1) == ok", r)
    assert P.evaluate("http(A -> B:80) == ok", r)
    assert P.evaluate("balances(LB1, >= 2)", r)
    assert not P.evaluate("balances(LB1, >= 4)", r)


def test_behavioral_pending_without_runner_then_resolves():
    les = _behavioral()
    w = O.TopologyWorld(_named_vpc())

    # no runner → behavioral objectives pending, structural met
    res = {r.id: r.status for r in O.evaluate_all(les.objectives, w)}
    assert res["in-boundary"] == O.MET
    assert res["reaches"] == O.PENDING and res["shielded"] == O.PENDING

    # a runner where web reaches db and the internet is blocked → all met
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True,
                           ("reach", "NET", "DB1", None): False})
    res2 = {r.id: r.status for r in O.evaluate_all(les.objectives, w, runner)}
    assert res2 == {"in-boundary": O.MET, "reaches": O.MET, "shielded": O.MET}


def test_unavailable_runner_keeps_pending():
    down = P.FakeRunner({}, available=False)
    res = {r.id: r.status for r in O.evaluate_all(_behavioral().objectives, O.TopologyWorld(Topology()), down)}
    assert res["reaches"] == O.PENDING           # runtime down → not wrongly failed


def test_mission_check_runs_behavioral_to_gold():
    from gini.agent.mission import Mission
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    m = Mission(_behavioral(), now=lambda: 0.0); m.start()
    sc = m.check(O.TopologyWorld(_named_vpc()), runner)
    assert sc.band == "gold" and m.complete

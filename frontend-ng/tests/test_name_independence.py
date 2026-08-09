"""Name independence — THE correctness property of authoring.

A teacher records a fragment with elements named R1/S1/H1. A student plays it with the router named
'Gateway-42' and the switch 'CoreSwitch'. The rule must still match. GINI guarantees this because
every derived predicate is TYPE-based (exists(router), link(host, switch), reach(web_app ->
database)) — never the instance name. These tests prove it end-to-end and guard against a name ever
leaking into a derived check.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.agent.mission import Mission
from gini.domain import assembly as A
from gini.domain import authoring as AU
from gini.domain import fragments as F
from gini.domain import objectives as O
from gini.domain import probes as P
from gini.domain.topology import Topology


def test_derived_checks_carry_TYPES_not_NAMES():
    """Whatever the teacher names things, the derived check text must not contain the instance name."""
    t = Topology(); r = AU.Recorder()
    s1 = t.add_device("switch", "S1"); r.capture(t)
    h1 = t.add_device("host", "H1"); r.capture(t)
    t.add_link(h1.id, s1.id); r.capture(t)
    r1 = t.add_device("router", "R1"); r.capture(t)
    t.add_link(s1.id, r1.id); r.capture(t)

    checks = [s["check"] for s in r.result()]
    assert checks == ["exists(switch)", "exists(host)", "link(host, switch)",
                      "exists(router)", "link(router, switch)"]
    blob = " ".join(checks)
    for name in ("S1", "H1", "R1"):
        assert name not in blob                         # not a single instance name leaked


def test_a_fragment_authored_with_ONE_naming_grades_against_ANOTHER():
    t = Topology(); r = AU.Recorder()
    s1 = t.add_device("switch", "S1"); r.capture(t)
    h1 = t.add_device("host", "H1"); r.capture(t)
    t.add_link(h1.id, s1.id); r.capture(t)
    r1 = t.add_device("router", "R1"); r.capture(t)
    t.add_link(s1.id, r1.id); r.capture(t)

    d = AU.build_fragment_dict(frag_id="ni-lan", teaches="networking-basics", summary="x", spirit="",
                               objectives=r.result(), author="prof")
    AU.save_fragment(d); F.reload()
    les = A.assemble(["ni-lan"], lesson_id="ni", fill=False)

    # a student builds the SAME topology with COMPLETELY different names
    st = Topology()
    sw = st.add_device("switch", "CoreSwitch"); a = st.add_device("host", "alpha")
    st.add_link(a.id, sw.id)
    rt = st.add_device("router", "Gateway-42"); st.add_link(sw.id, rt.id)

    m = Mission(les, now=lambda: 0.0); m.start()
    m.evaluate(O.TopologyWorld(st))
    assert m.complete and m.score().band == "gold"      # graded fine, despite the different names


def test_a_recorded_count_matches_regardless_of_names():
    """count(host) >= 2 must fire for any two hosts, whatever they're called."""
    t = Topology(); r = AU.Recorder()
    t.add_device("host", "H1"); r.capture(t)
    t.add_device("host", "H2"); r.capture(t)
    step = next(s for s in r.result() if s["key"] == "place:host")
    assert step["check"] == "count(host) >= 2"

    st = Topology()
    st.add_device("host", "penguin"); st.add_device("host", "walrus")
    res = O.evaluate_all([O.Objective(id="c", say="two hosts", check="count(host) >= 2")],
                         O.TopologyWorld(st))
    assert res[0].met                                   # matched on TYPE, names irrelevant


def test_a_live_check_is_also_name_independent():
    """The probe resolves types to whatever the student named things (TypeRunner) — a reach() rule
    authored on web_app/database fires against 'shop-frontend'/'orders-db'."""
    check = AU.live_check("web_app", "database", True)
    assert check["probe"] == "reach(web_app -> database) == ok"

    st = Topology()
    web = st.add_device("web_app", "shop-frontend")
    db = st.add_device("database", "orders-db")
    # a fake runner that only knows the student's ACTUAL names — the type layer must bridge to them
    runner = P.TypeRunner(P.FakeRunner({("reach", "shop-frontend", "orders-db", None): True}),
                          lambda: st)
    assert P.evaluate(check["probe"], runner) is True   # type→name resolution, not literal names

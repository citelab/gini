"""Fragment certification — the hard compiler gate + the soft composability review with a dry-run
of the composition engine.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import authoring as AU
from gini.domain import certify as C
from gini.domain import fragment_yaml as FY


def _frag_from(**kw):
    return FY.fragment_from_dict(AU.build_fragment_dict(**kw))


def _good_dict():
    return AU.build_fragment_dict(
        frag_id="lan", teaches="switching", summary="a switched LAN", spirit="hosts talk via a switch",
        objectives=[{"id": "h", "say": "place 2 hosts", "check": "count(host) >= 2", "level": 1},
                    {"id": "l", "say": "wire them", "check": "link(host, switch)", "level": 2},
                    AU.output_check("packet_view", "packets", ">=", 1)],
        forks=[{"id": "hard", "label": "add a router", "difficulty": 2, "kind": "converge",
                "objectives": [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}]}])


# -- the compiler: correctness is a HARD gate -------------------------------- #
def test_grading_a_fragment_uses_instantiated_objectives_not_templates():
    # the runtime grade must instantiate() (Objective has is_behavioral); the stored templates don't
    from gini.domain import fragment_yaml as FY
    from gini.domain import objectives as O
    from gini.domain import probes as P
    from gini.domain.topology import Topology
    d = AU.build_fragment_dict(frag_id="x", teaches="n", summary="s", spirit="sp",
                               objectives=[{"id": "h", "say": "h", "check": "exists(host)",
                                            "level": 1},
                                           AU.output_check("packet_view", "packets", ">=", 1)])
    frag = FY.fragment_from_dict(d)
    assert not hasattr(frag.objectives[0], "is_behavioral")     # a template — would crash evaluate
    t = Topology(); t.add_device("host", "H1")
    runner = P.FakeRunner({("measure", "packet_view", "packets"): 5})
    results = O.evaluate_all(frag.instantiate(), O.TopologyWorld(t), runner)   # instantiate = the fix
    assert {r.status for r in results} == {O.MET}


def test_a_broken_fragment_is_blocked_not_certified():
    d = AU.build_fragment_dict(frag_id="bad", teaches="x", summary="s", spirit="sp",
                               objectives=[{"id": "o", "say": "bad", "kind": "behavioral",
                                            "probe": "reach(host ->", "level": 4}])
    rep = C.certify(d, library=[])
    assert rep.blocked and not rep.certified
    assert any(i.code == "invalid" for i in rep.of(C.BLOCK))


def test_a_behavioral_fragment_needs_a_clean_runtime_grade():
    d = _good_dict()                                       # has a measure output check → behavioral
    # HARD gate: without a live grade, a behavioral fragment is NOT certified
    blocked = C.certify(d, library=[])
    assert blocked.blocked and any(i.code == "runtime-required" for i in blocked.of(C.BLOCK))
    # a clean live grade certifies it
    ok = C.certify(d, library=[], runtime=C.RuntimeGrade(available=True))
    assert ok.certified and any(i.code == "runtime-ok" for i in ok.of(C.INFO))
    codes = {i.code for i in ok.issues}
    assert "no-spirit" not in codes and "no-output" not in codes and "no-difficulty" not in codes


def test_runtime_failures_block_with_the_reason():
    d = _good_dict()
    unmet = C.certify(d, library=[],
                      runtime=C.RuntimeGrade(available=True, unmet=["out-packet_view-packets"]))
    assert unmet.blocked and any(i.code == "not-winnable" for i in unmet.of(C.BLOCK))
    pend = C.certify(d, library=[], runtime=C.RuntimeGrade(available=True, pending=["x"]))
    assert pend.blocked and any(i.code == "runtime-incomplete" for i in pend.of(C.BLOCK))


def test_pure_structural_fragment_needs_no_runtime():
    d = AU.build_fragment_dict(frag_id="struct", teaches="x", summary="s", spirit="sp",
                               objectives=[{"id": "h", "say": "host", "check": "exists(host)",
                                            "level": 1}],
                               forks=[{"id": "f", "label": "l", "difficulty": 2, "kind": "converge",
                                       "objectives": [{"id": "r", "say": "r",
                                                       "check": "exists(router)", "level": 1}]}])
    rep = C.certify(d, library=[])                         # no runtime, but no behavioral either
    assert rep.certified and any(i.code == "structural-only" for i in rep.of(C.INFO))


def test_runtime_from_results_summarizes_statuses_and_the_stamp_travels():
    from gini.domain import fragment_yaml as FY
    from gini.domain import objectives as O
    res = [O.ObjectiveResult("a", "", "structural", O.MET),
           O.ObjectiveResult("b", "", "behavioral", O.UNMET),
           O.ObjectiveResult("c", "", "behavioral", O.PENDING)]
    g = C.runtime_from_results(res)
    assert g.available and g.unmet == ["b"] and g.pending == ["c"] and not g.all_met

    d = AU.build_fragment_dict(frag_id="cert-me", teaches="x", summary="s", spirit="sp",
                               objectives=[{"id": "h", "say": "h", "check": "exists(host)",
                                            "level": 1}], certified=True)
    frag = FY.fragment_from_dict(d)
    assert frag.certified                                  # the stamp survives the round-trip
    assert "certified: true" in FY.to_yaml(frag).lower()


# -- the AI certifier: composability warnings are SOFT ----------------------- #
def test_soft_warnings_do_not_block_but_are_reported():
    d = AU.build_fragment_dict(
        frag_id="thin", teaches="x", summary="s", spirit="",       # no spirit
        objectives=[{"id": "h", "say": "host", "check": "exists(host)", "level": 1}])  # no output, no fork
    rep = C.certify(d, library=[])
    assert rep.certified                                   # soft: still certifiable
    codes = {i.code for i in rep.of(C.WARN)}
    assert {"no-spirit", "no-output", "no-difficulty"} <= codes


def test_multiplied_probed_type_is_an_info_hint_not_a_block():
    # type-based grading reads this as "some web_app" — a hint about the quantifier, never a block
    d = AU.build_fragment_dict(
        frag_id="amb", teaches="x", summary="s", spirit="sp",
        objectives=[{"id": "w", "say": "2 web apps", "check": "count(web_app) >= 2", "level": 1},
                    {"id": "r", "say": "reaches db", "kind": "behavioral",
                     "probe": "reach(web_app -> database) == ok", "level": 4}])
    rep = C.certify(d, library=[], runtime=C.RuntimeGrade(available=True))   # behavioral → grade it
    assert rep.certified                                         # the quantifier hint never blocks
    assert any(i.code == "quantifier" for i in rep.of(C.INFO))   # just a hint


# -- dry-run the composition engine ------------------------------------------ #
def test_dry_run_reports_orphan_requires_and_forward_composition():
    # library: a fragment that PROVIDES 'l2-fabric', and one that REQUIRES it
    lib = [
        _frag_from(frag_id="lan", teaches="x", summary="s", spirit="sp",
                   objectives=[{"id": "h", "say": "h", "check": "exists(host)", "level": 1}],
                   provides=["l2-fabric"]),
        _frag_from(frag_id="web", teaches="x", summary="s", spirit="sp",
                   objectives=[{"id": "w", "say": "w", "check": "exists(web_app)", "level": 1}],
                   requires=["l2-fabric"]),
    ]
    # our fragment provides 'l2-fabric' (so 'web' can consume it) and requires 'l3-gateway' (orphan)
    d = AU.build_fragment_dict(frag_id="mylan", teaches="x", summary="s", spirit="sp",
                               objectives=[{"id": "h", "say": "h", "check": "exists(host)", "level": 1}],
                               provides=["l2-fabric"], requires=["l3-gateway"])
    rep = C.certify(d, library=lib)
    assert rep.certified                                   # unknown-role would have blocked; these are real
    assert "l3-gateway" in rep.unmet_requires
    assert any(i.code == "orphan-requires" for i in rep.of(C.WARN))
    assert "web" in rep.composes_into                      # forward: 'web' requires what we provide
    assert any(i.code == "composes-into" for i in rep.of(C.INFO))

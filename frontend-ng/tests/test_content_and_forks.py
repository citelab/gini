"""Authoring foundation: content layering (system + user), version stamping, and difficulty forks.

The three foundational pieces the authoring system stands on:
  * fragments load from a SYSTEM layer (bundled) + a USER layer (~/.gini/content) — so gBuilder can be
    packaged AND have somewhere for authored/OTA fragments to land;
  * a broken user fragment is DECLINED, never fatal (refuse what you can't grade);
  * a FORK is the difficulty knob: the easy path earns PASS, taking a harder fork earns GOLD.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# each test module gets its own GINI home so the user content layer is isolated
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.agent.mission import Mission
from gini.domain import assembly as A
from gini.domain import content as C
from gini.domain import fragment_yaml as FY
from gini.domain import fragments as F
from gini.domain import objectives as O
from gini.domain.topology import Topology


def _author(frag, name):
    """Drop an authored fragment into the USER content layer and reload."""
    open(C.ensure_user_content_dir() / f"{name}.yaml", "w").write(FY.to_yaml(frag))
    F.reload()


def _forked_lan():
    return F.Fragment(
        id="t-lan-fork", layer="core", teaches="networking-basics", summary="switched LAN + fork",
        provides=("switched-segment",),
        objectives=(F.ObjectiveTemplate(id="c-switch", say="Place a switch", check="exists(switch)"),),
        forks=(F.Fork(id="route", label="Also route off-subnet", difficulty=2, kind="converge",
                      objectives=(F.ObjectiveTemplate(id="f-router", say="Add a router",
                                                      check="exists(router)"),)),),
        engine_version=C.ENGINE_VERSION, author="prof")


# -- content layering -------------------------------------------------------- #
def test_the_system_layer_ships_the_builtins():
    assert C.system_content_dir().exists()
    assert len(F.FRAGMENTS) >= 15               # built-ins present without any user content


def test_an_authored_fragment_in_the_user_layer_is_picked_up():
    _author(_forked_lan(), "t-lan-fork")
    f = F.get("t-lan-fork")
    assert f is not None
    assert f.engine_version == C.ENGINE_VERSION and f.author == "prof"


def test_a_fragment_round_trips_through_yaml_with_its_forks_and_stamp():
    y = FY.to_yaml(_forked_lan())
    back = FY.from_yaml(y)
    assert len(back.forks) == 1 and back.forks[0].kind == "converge"
    assert back.forks[0].difficulty == 2
    assert back.engine_version == C.ENGINE_VERSION
    assert FY.validate(back) == []              # authored fragments must validate like built-ins


def test_a_broken_user_fragment_is_declined_not_fatal():
    """A bad OTA/authored pack must never brick the client — it's refused, the rest still load."""
    _author(_forked_lan(), "t-good")            # a good one
    open(C.ensure_user_content_dir() / "t-broken.yaml", "w").write(
        "id: t-broken\nlayer: core\nobjectives:\n- {id: x, check: 'nonsense(('}\n")
    F.reload()
    assert "t-lan-fork" in F.FRAGMENTS           # the good one still loaded
    assert "t-broken" not in F.FRAGMENTS         # the broken one was declined
    assert any("t-broken" in w for w in F.LOAD_WARNINGS)


def test_the_system_layer_is_strict_a_broken_builtin_would_raise():
    """System packs are authoritative; a broken built-in is a bug, not a soft decline."""
    import pytest
    d = tempfile.mkdtemp()
    open(os.path.join(d, "bad.yaml"), "w").write("id: bad\nlayer: nonsense\n")
    with pytest.raises(ValueError):
        FY.load_dir(d, strict=True)
    frags, warnings = FY.load_dir(d, strict=False)   # …but the user layer just warns
    assert not frags and warnings


# -- forks as the difficulty knob -------------------------------------------- #
def _play(les, t):
    m = Mission(les, now=lambda: 0.0)
    m.start()
    m.evaluate(O.TopologyWorld(t))
    return m


def test_the_easy_path_earns_pass_the_harder_fork_earns_gold():
    _author(_forked_lan(), "t-lan-fork")
    les = A.assemble(["t-lan-fork"], lesson_id="lf", fill=False)
    assert [f["id"] for f in les.forks] == ["route"]

    t = Topology()
    assert _play(les, t).score().band == "incomplete"

    t.add_device("switch", "S1")                # golden path complete
    m = _play(les, t)
    assert m.complete and m.score().band == "pass"       # gold is RESERVED for the harder fork
    assert m.score().forks_done == 0

    t.add_device("router", "R1")                # take the harder fork
    m = _play(les, t)
    assert m.complete and m.score().band == "gold"
    assert m.score().forks_done == 1 and m.score().forks_total == 1


def test_forks_never_block_completion():
    """A fork is optional difficulty — never a gate. The mission completes on the core alone."""
    _author(_forked_lan(), "t-lan-fork")
    les = A.assemble(["t-lan-fork"], lesson_id="lf", fill=False)
    t = Topology(); t.add_device("switch", "S1")
    assert _play(les, t).complete is True       # complete without touching the fork


def test_a_forkless_mission_still_golds_the_old_way():
    """Nothing regresses: a plain mission with no forks still reaches gold on complete+on-time."""
    from gini.domain import scoring as S
    from gini.domain.objectives import ObjectiveResult, MET
    res = [ObjectiveResult("a", "do a", "structural", MET)]
    sc = S.score(res, complete_when="all", on_time=True, forks_total=0)
    assert sc.band == "gold"                    # legacy semantics preserved exactly

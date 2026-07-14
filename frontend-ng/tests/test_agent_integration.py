"""P-Integration: the live mission loop driven by the multi-agent brain (AgentGameMaster) — the
controller's mechanics (panel, completion, scoring) unchanged, the reasoning routed through the
blackboard + personas."""
from gini.agent.agent_gamemaster import AgentGameMaster
from gini.agent.mission import Mission
from gini.agent.mission_controller import MissionController
from gini.domain import assembly as A, objectives as _obj
from gini.domain.topology import Topology


def _lan(hosts=2, extra=None):
    t = Topology()
    sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
    for i in range(hosts):
        h = t.add_device("host", f"H{i}"); t.add_link(h.id, sw.id)
    t.add_link(sw.id, r.id)
    if extra:
        t.add_device(extra, extra.upper())
    return t


def _lesson():
    # experience = core only (no auto-filled observe layer), so a plain LAN can complete it
    return A.assemble(["basic-lan"], genre="experience", lesson_id="t")


def test_agent_gamemaster_grounds_its_reaction_in_live_facts():
    seen = []
    gm = AgentGameMaster(_lesson(), llm=lambda p: (seen.append(p), "A grounded line.")[1])
    m = Mission(_lesson()); m.start()
    gm.decide(m, m.evaluate(_obj.TopologyWorld(_lan(hosts=1))))     # first call → sets baseline
    gm.decide(m, m.evaluate(_obj.TopologyWorld(_lan(hosts=2))))     # progress → a reaction fires
    assert any("Objectives met" in p or "FACTS" in p for p in seen)  # the reasoning was grounded


def test_controller_runs_end_to_end_with_the_agent_brain():
    posts = []
    topo = _lan(hosts=1)                                   # start incomplete
    ctrl = MissionController(
        llm=lambda p: "line",
        post=lambda role, tx: posts.append(tx),
        get_topology=lambda: topo,
        gm_factory=AgentGameMaster)
    assert ctrl.start(_lesson())
    assert ctrl.active
    topo = _lan(hosts=2)                                   # complete the LAN
    ctrl.get_topology = lambda: topo
    ctrl.on_canvas_changed()
    assert ctrl.mission.complete                           # scoring/completion still work
    assert posts                                           # the agent brain produced lines


def test_agent_gamemaster_is_inert_without_a_model():
    gm = AgentGameMaster(_lesson(), llm=None)
    m = Mission(_lesson()); m.start()
    move = gm.decide(m, m.evaluate(_obj.TopologyWorld(_lan())))
    assert move.kind == "quiet"                            # LLM-gated, like GameMaster
    assert gm.brief_line() == _lesson().brief             # degrades to authored text

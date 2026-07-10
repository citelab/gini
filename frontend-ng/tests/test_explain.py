"""Grounded diagnosis: the predicate explainer turns a red objective into a board-specific reason,
and the Reasoning persona receives it (so 'why is this red?' answers are about the actual board)."""
from gini.agent.agent_gamemaster import AgentGameMaster
from gini.agent.mission import Mission
from gini.domain import explain, lesson as L, objectives as O
from gini.domain.topology import Topology


def _world(t):
    return O.TopologyWorld(t)


def test_explains_missing_and_unwired_elements():
    t = Topology(); t.add_device("switch", "S")           # a lone switch, nothing else
    w = _world(t)
    assert "no router" in explain.why_unmet("exists(router)", w)
    # uses the student-facing label ('host' shows as 'Machine' on the palette)
    assert "wired to a switch" in explain.why_unmet("link(host, switch)", w)


def test_explains_reachability_and_isolation():
    t = Topology()
    c = t.add_device("cloud", "NET"); d = t.add_device("database", "DB"); t.add_link(c.id, d.id)
    w = _world(t)
    # web can't reach db (no web app / no path)
    assert "can't reach" in explain.why_unmet("path(web_app, database)", w)
    # shield violated: a cloud CAN still reach the database
    assert "still reach" in explain.why_unmet("not path(cloud, database)", w)


def test_reasoning_situation_includes_the_why():
    les = L.from_archetype("basic-lan", {}, id="t")
    gm = AgentGameMaster(les, llm=lambda p: p)             # echo the prompt so we can inspect grounding
    gm.bind_world(lambda: _world(Topology()))              # empty board → everything red
    m = Mission(les); m.start()
    gm.decide(m, m.evaluate(_world(Topology())), utterance="why isn't anything passing?")
    # the reasoning prompt carried board-grounded reasons, not just objective names
    assert "no switch" in gm.runner.last_prompt or "no router" in gm.runner.last_prompt

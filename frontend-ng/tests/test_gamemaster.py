"""The game master reasoning loop: it interprets student intent via the LLM (never keyword),
respects the lesson's help level, gives warmer/colder grounded in objective deltas, celebrates
completion, and is inert without a model (Missions are LLM-gated)."""
from gini.agent import gamemaster as G
from gini.agent.mission import Mission
from gini.domain import lesson as L, objectives as O, probes as P
from gini.domain.topology import Topology


class ScriptedLLM:
    """Records prompts; returns scripted JSON for the interpret call, prose otherwise."""
    def __init__(self, interpret_json='{"progress":"attempting","objective_ref":"","is_question":false}'):
        self.prompts = []
        self.interpret_json = interpret_json

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.interpret_json if "ONLY as JSON" in prompt else "a phrased line."


def _lesson(**over):
    d = {"id": "lab03", "intent": {"concept": "vpc-networking", "spirit": "reachability"},
         "objectives": [
             {"id": "in-boundary", "say": "DB inside the VPC", "kind": "structural",
              "check": "contains(VPC1, DB1)"},
             {"id": "reaches", "say": "web reaches db", "kind": "behavioral",
              "probe": "reach(WEB1 -> DB1) == ok"},
             {"id": "shielded", "say": "db shielded", "kind": "behavioral",
              "probe": "reach(NET -> DB1) == fail"}],
         "complete_when": "all"}
    d.update({k: v for k, v in over.items() if k in ("help", "persona", "time_limit", "attempts")})
    return L.from_dict(d)


def _partial_world():
    t = Topology(); v = t.add_device("vpc", "VPC1")
    t.add_device("web_app", "WEB1", parent_id=v.id)
    t.add_device("database", "DB1", parent_id=v.id)
    return O.TopologyWorld(t)


def _complete(mission):
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    mission.check(_partial_world(), runner)


def test_no_llm_is_inert():
    gm = G.GameMaster(_lesson(), llm=None)
    assert gm.decide(Mission(_lesson()), []).kind == G.QUIET


def test_completion_celebrates():
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    _complete(m)
    mv = G.GameMaster(_lesson(), llm=ScriptedLLM()).decide(m, m.last_results)
    assert mv.kind == G.CELEBRATE and mv.text


def test_question_respects_help_level():
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    res = m.evaluate(_partial_world())
    q = "how do I make the db reachable?"

    ask = '{"progress":"asking_hint","objective_ref":"reaches","is_question":true}'
    full = G.GameMaster(_lesson(help="full_tutor_logged"), llm=ScriptedLLM(ask)).decide(m, res, utterance=q)
    assert full.kind == G.ANSWER and full.logged

    hint = G.GameMaster(_lesson(help="warmer_colder"), llm=ScriptedLLM(ask)).decide(m, res, utterance=q)
    assert hint.kind == G.HINT

    none = G.GameMaster(_lesson(help="none"), llm=ScriptedLLM(ask)).decide(m, res, utterance=q)
    assert none.kind == G.QUIET                       # proctored: no help


def test_warmer_when_more_objectives_met():
    m = Mission(_lesson(help="warmer_colder"), now=lambda: 0.0); m.start()
    gm = G.GameMaster(_lesson(help="warmer_colder"), llm=ScriptedLLM())
    gm.decide(m, O.evaluate_all(_lesson().objectives, O.TopologyWorld(Topology())))  # baseline (0 met)
    mv = gm.decide(m, m.evaluate(_partial_world()))   # in-boundary now met → delta > 0
    assert mv.kind == G.NUDGE and mv.text


def test_stuck_gets_a_hint_when_help_allows():
    m = Mission(_lesson(help="warmer_colder"), now=lambda: 0.0); m.start()
    res = m.evaluate(_partial_world())
    stuck = '{"progress":"stuck","objective_ref":"shielded","is_question":false}'
    mv = G.GameMaster(_lesson(help="warmer_colder"), llm=ScriptedLLM(stuck)).decide(m, res, utterance="ugh")
    assert mv.kind == G.HINT and mv.objective_ref == "shielded"


def test_persona_shapes_the_prompt():
    llm = ScriptedLLM()
    G.GameMaster(_lesson(persona="challenger"), llm=llm).interpret("what now?", [])
    assert any("challenger" in p for p in llm.prompts)


def test_interpretation_is_llm_not_keyword():
    # a message with no '?' that the model reads as a question still routes as one
    m = Mission(_lesson(help="full_tutor_logged"), now=lambda: 0.0); m.start()
    res = m.evaluate(_partial_world())
    reads_as_q = '{"progress":"asking_hint","objective_ref":"","is_question":true}'
    mv = G.GameMaster(_lesson(help="full_tutor_logged"),
                      llm=ScriptedLLM(reads_as_q)).decide(m, res, utterance="I have no idea what to do")
    assert mv.kind == G.ANSWER                         # the LLM's reading decided, not a keyword

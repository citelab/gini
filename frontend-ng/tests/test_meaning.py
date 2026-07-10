"""P4: Understanding & Critic personas, the always-on fast-path classifier, and the MissionAgent
orchestrating one full turn on a single model."""
from gini.agent.blackboard import Blackboard
from gini.agent.contracts import Claim, Intent, Move, Notification
from gini.agent.meaning import Classifier, CriticAgent, MissionAgent, Route, UnderstandingAgent
from gini.agent.personas import PersonaRunner
from gini.domain import assembly as A
from gini.domain.topology import Topology


def _lan(hosts=2):
    t = Topology()
    sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
    for i in range(hosts):
        h = t.add_device("host", f"H{i}"); t.add_link(h.id, sw.id)
    t.add_link(sw.id, r.id)
    return t


def _les():
    return A.assemble(["basic-lan"], genre="experience", lesson_id="t")


def _bb(topology):
    bb = Blackboard(); bb.load_lesson(_les()); bb.update(topology)
    return bb


def test_understanding_parses_a_structured_intent():
    r = PersonaRunner(lambda p: '{"kind":"objective","objective_ref":"two-hosts","refs":["H0"]}')
    ua = UnderstandingAgent(r, _les())
    intent = ua.parse("how many machines do I need?")
    assert intent.kind == "objective" and intent.objective_ref == "two-hosts" and intent.refs == ("H0",)


def test_classifier_is_always_on_and_routes():
    r = PersonaRunner(lambda p: '{"reason":true,"understand":true,"critic":true}')
    route = Classifier(r).route(change="", utterance="why is this red?")
    assert route.reason and route.understand and route.critic


def test_classifier_offline_fallback():
    route = Classifier(PersonaRunner(None)).route(change="off_task_added", utterance="")
    assert route.reason and not route.understand and not route.critic


def test_verify_claims_catches_a_contradiction():
    bb = _bb(_lan(hosts=1))                              # 'two-hosts' is UNMET
    move = Move(text="You've got two hosts wired up!", claims=(Claim("two-hosts", expected=True),))
    bad = CriticAgent.verify_claims(move, bb)
    assert "two-hosts" in bad                            # the oracle catches the false claim


def test_mission_agent_runs_a_full_question_turn():
    calls = {"reason": 0}

    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":true,"critic":true}'
        if prompt.startswith("You classify"):
            return '{"kind":"objective","objective_ref":"","refs":[]}'
        if prompt.startswith("You audit"):
            return '{"ok":true,"missing":[],"unsupported":[]}'
        calls["reason"] += 1                             # the Reasoning persona
        return "Here's what's still open on your board."
    bb = _bb(_lan(hosts=1))
    agent = MissionAgent(PersonaRunner(llm), bb, _les())
    move = agent.turn(utterance="what's left?")
    assert move.text and calls["reason"] >= 1
    assert move.kind == "answer"


def test_mission_agent_revises_when_critic_flags():
    reason_calls = []

    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":true}'
        if prompt.startswith("You audit"):
            return '{"ok":false,"missing":[],"unsupported":["claimed the LAN is done"]}'
        reason_calls.append(prompt)
        return "revised" if "reviewer noted" in prompt else "draft"
    bb = _bb(_lan(hosts=1))
    agent = MissionAgent(PersonaRunner(llm), bb, _les())
    move = agent.turn(Notification("objective_unmet", subjects=("two-hosts",), salience=0.4))
    assert move.text == "revised"                        # the critic forced one revision pass
    assert len(reason_calls) == 2

"""P3: the persona runner (clean isolation) and the Reasoning persona reasoning off the blackboard —
grounded in the live verdicts, with a graceful offline fallback."""
from gini.agent.blackboard import Blackboard
from gini.agent.contracts import Intent, Notification
from gini.agent.personas import Persona, PersonaRunner
from gini.agent.reasoning import ReasoningAgent
from gini.domain import assembly as A
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


def _agent(topology):
    les = A.assemble(["basic-lan"], genre="experience", lesson_id="t")
    bb = Blackboard(); bb.load_lesson(les); bb.update(topology)
    captured = {}

    def llm(prompt):
        captured["prompt"] = prompt
        return "Model line."
    agent = ReasoningAgent(PersonaRunner(llm), bb, les)
    agent._captured = captured
    return agent


def test_persona_runner_isolates_prompt_to_system_context_task():
    seen = {}
    r = PersonaRunner(lambda p: (seen.__setitem__("p", p), "ok")[1])
    p = Persona("X", system="SYSTEM-ONLY-LINE")
    out = r.call(p, context="CTX", task="TASK")
    assert out == "ok"
    assert r.last_prompt.startswith("SYSTEM-ONLY-LINE")
    assert "CTX" in r.last_prompt and "TASK" in r.last_prompt


def test_reasoning_is_grounded_in_live_facts():
    agent = _agent(_lan(hosts=1))                      # one host → 'two hosts' objective is open
    move = agent.react(Intent(text="what's left to do?"))
    assert move.kind == "answer"
    # the prompt the model saw carried the actual objective facts (grounding, not canned)
    assert "Still open" in agent._captured["prompt"]
    assert "Objectives met" in agent._captured["prompt"]


def test_completion_produces_an_advance_move():
    agent = _agent(_lan(hosts=2))
    move = agent.react(Notification("mission_complete", salience=1.0))
    assert move.kind == "advance" and move.text


def test_off_task_produces_a_flag_move_naming_the_element():
    agent = _agent(_lan(extra="k8s_cluster"))
    note = Notification("off_task_added", subjects=("K8S",), salience=0.9)
    move = agent.react(note)
    assert move.kind == "flag"
    assert "K8S" in agent._captured["prompt"]           # the model was told which element to flag


def test_offline_fallback_when_no_model():
    les = A.assemble(["basic-lan"], genre="experience", lesson_id="t")
    bb = Blackboard(); bb.load_lesson(les); bb.update(_lan())
    agent = ReasoningAgent(PersonaRunner(None), bb, les)    # no model
    move = agent.react(Notification("mission_complete", salience=1.0))
    assert move.text                                    # still says something sensible

"""Reasoning 2.0 phase A — the deterministic Reasoning Twin: concern enumeration, the exact
coverage diff, one objection round, and visible flags. All golden-fixture, no live model."""
import re

from gini.agent.blackboard import Blackboard
from gini.agent.contracts import Fact, Move, Notification, Verdict
from gini.agent.meaning import MissionAgent
from gini.agent.personas import Persona, PersonaRunner
from gini.agent.twin import (
    MAX_CONCERNS, Concern, Coverage, Twin, cap, mission_concerns, parse_coverage,
)
from gini.domain import assembly as A
from gini.domain.topology import Topology


def _lan(hosts=1):
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


# ---- contracts ------------------------------------------------------------- #
def test_parse_coverage_tolerates_shapes():
    c = parse_coverage({"addressed": ["a", "b"], "omitted": [{"id": "c", "why": "off-topic"}]})
    assert c.addressed == {"a", "b"} and c.omitted == {"c": "off-topic"}
    assert parse_coverage({"addressed": [], "omitted": ["d"]}).omitted == {"d": ""}
    assert parse_coverage(None) is None                     # absent -> coverage-silent
    assert parse_coverage({"nope": 1}) is None              # malformed -> coverage-silent
    assert parse_coverage("prose") is None


def test_salience_cap_keeps_the_urgent():
    concerns = [Concern(f"c{i}", "objective", f"s{i}", "e", salience=2) for i in range(6)]
    concerns.append(Concern("urgent", "legality", "s", "e", salience=3))
    kept = cap(concerns)
    assert len(kept) == MAX_CONCERNS
    assert kept[0].id == "urgent"                           # highest salience survives the cap


# ---- the mission enumerator ------------------------------------------------ #
def test_mission_concerns_project_the_blackboard():
    bb = _bb(_lan(hosts=1))                                 # some objectives unmet
    concerns = mission_concerns(bb, _les())
    assert concerns, "an unfinished board must yield concerns"
    assert all(c.evidence for c in concerns)                # no concern without ground evidence
    assert all(c.id.startswith("objective:") for c in concerns)
    assert all(c.salience == 2 for c in concerns)


def test_mission_concerns_include_legality_at_salience_3():
    bb = _bb(_lan(hosts=1))
    bb._verdicts["off_task"] = Verdict("legality:off_task", subject="off_task", value=False,
                                       evidence=Fact("off_task", ["DB1"]), deps=("topology",))
    concerns = mission_concerns(bb, _les())
    off = [c for c in concerns if c.id == "legality:off_task"]
    assert off and off[0].salience == 3 and "DB1" in off[0].statement


def test_all_green_board_yields_no_concerns():
    bb = _bb(_lan(hosts=2))                                 # basic-lan complete
    if bb.unmet_objectives():                               # guard: only meaningful when green
        return
    assert mission_concerns(bb, _les()) == []


# ---- the exact diff -------------------------------------------------------- #
def _c(cid, sal=2, kind="objective"):
    return Concern(cid, kind, f"'{cid}' is unmet", "why", salience=sal)


def test_diff_is_exact_set_arithmetic():
    twin = Twin()
    concerns = [_c("a"), _c("b"), _c("low", sal=1)]
    cov = Coverage(addressed=frozenset({"a"}), omitted={"b": "would give it away"})
    assert twin.diff(concerns, cov) == []                   # every must-address accounted for
    cov2 = Coverage(addressed=frozenset({"a"}), omitted={})
    missed = twin.diff(concerns, cov2)
    assert [o.concern.id for o in missed] == ["b"]          # b silently dropped -> objection
    assert "why not" in missed[0].question.lower() or "leave that out" in missed[0].question.lower()
    # low-salience concerns never draw objections
    assert all(o.concern.id != "low" for o in missed)


def test_coverage_silence_objects_only_about_urgent():
    twin = Twin()
    concerns = [_c("a"), _c("urgent", sal=3, kind="legality")]
    missed = twin.diff(concerns, None)                      # no coverage report at all
    assert [o.concern.id for o in missed] == ["urgent"]


def test_omissions_split_by_adjudication():
    from gini.agent.twin import TwinContext
    twin = Twin()
    concerns = [_c("a"), _c("b")]
    cov = Coverage(addressed=frozenset({"a"}), omitted={"b": "socratic", "ghost": "n/a"})
    ctx = TwinContext(move_kind="hint")                     # withholding legit on a hint
    assert twin.audit(concerns, cov, ctx) == []             # justification validated -> defeated
    acc, rej = twin.split_omissions(concerns, cov)
    assert acc == {"b": "socratic"} and rej == {}           # unknown ids dropped
    ctx2 = TwinContext(move_kind="answer")                  # answering a question: not legit
    objs = twin.audit(concerns, cov, ctx2)
    assert [o.concern.id for o in objs] == ["b"]
    _, rej2 = twin.split_omissions(concerns, cov)
    assert "b" in rej2                                      # rejected, with the checked reason


def test_flag_appends_visibly_and_preserves_the_move():
    twin = Twin()
    move = Move(kind="hint", text="Try the router.", refs=("R",))
    out = twin.flag(move, twin.diff([_c("a")], Coverage(frozenset(), {})))
    assert out.kind == "hint" and out.refs == ("R",)
    assert out.text.startswith("Try the router.")
    assert "Also worth a look" in out.text and "'a' is unmet" in out.text


# ---- the full turn (golden, scripted model) -------------------------------- #
def _ids_in(prompt):
    return re.findall(r"^  (\S+): ", prompt, re.M)          # the concern checklist lines


def _mk_agent(llm, twin=True):
    bb = _bb(_lan(hosts=1))
    return MissionAgent(PersonaRunner(llm), bb, _les(), twin=Twin() if twin else None)


def test_turn_objection_then_full_coverage_no_flags():
    seen = {"reason": 0}

    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        seen["reason"] += 1
        ids = _ids_in(prompt)
        if "You did not address" in prompt:                 # the objection round -> cover all
            addressed = ",".join(f'"{i}"' for i in ids)
            return '{"text":"revised line","coverage":{"addressed":[%s],"omitted":[]}}' % addressed
        return '{"text":"draft line","coverage":{"addressed":[],"omitted":[]}}'

    agent = _mk_agent(llm)
    move = agent.turn(Notification("objective_unmet", salience=0.4))
    assert move.text == "revised line"                      # objection forced one revision
    assert "Also worth a look" not in move.text             # full coverage -> no flags
    assert seen["reason"] == 2                              # draft + one objection round only
    res = agent.last_twin_result
    assert res and res.objections and not res.surviving and not res.coverage_silent


def test_turn_surviving_objection_becomes_a_flag():
    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        return '{"text":"stubborn line","coverage":{"addressed":[],"omitted":[]}}'

    agent = _mk_agent(llm)
    move = agent.turn(Notification("objective_unmet", salience=0.4))
    assert move.text.startswith("stubborn line")
    assert "Also worth a look" in move.text                 # never a silent ship
    assert agent.last_twin_result.surviving


def test_justified_omission_defeats_the_objection():
    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        ids = _ids_in(prompt)
        omitted = ",".join('{"id":"%s","why":"would give the answer away"}' % i for i in ids)
        return '{"text":"hint only","coverage":{"addressed":[],"omitted":[%s]}}' % omitted

    agent = _mk_agent(llm)
    move = agent.turn(Notification("objective_unmet", salience=0.4))
    assert move.text == "hint only" and "Also worth a look" not in move.text
    assert agent.last_twin_result.accepted_omissions       # phase A: logged, phase B: validated


def test_twin_disabled_is_byte_identical_behavior():
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        return "plain line"

    agent = _mk_agent(llm, twin=False)
    move = agent.turn(Notification("objective_unmet", salience=0.4))
    assert move.text == "plain line"
    assert agent.last_twin_result is None
    assert all("coverage" not in p for p in prompts)        # no twin artifacts in any prompt


def test_offline_fallback_stays_usable_with_twin():
    agent = MissionAgent(PersonaRunner(None), _bb(_lan(hosts=1)), _les(), twin=Twin())
    move = agent.turn(Notification("objective_unmet", salience=0.4))
    assert move.text                                        # authored fallback text still ships


# ---- phase B: justification adjudication ----------------------------------- #
def test_scope_justification_checked_against_the_question():
    from gini.agent.twin import TwinContext, adjudicate
    c = Concern("legality:off_task", "legality", "off-task element(s): DB1", "ev", salience=3)
    # the question never mentions the concern -> genuinely off-topic -> valid
    ok = adjudicate(c, "that's not what they asked", TwinContext(utterance="what is a router?"))
    assert ok.valid and ok.kind == "scope"
    # the question IS about it -> invalid
    bad = adjudicate(c, "off topic", TwinContext(utterance="why is DB1 flagged as off-task?"))
    assert not bad.valid
    # nothing was asked (a notification turn) -> nothing to be off-topic from -> invalid
    assert not adjudicate(c, "not what they asked", TwinContext(utterance="")).valid


def test_already_addressed_checked_against_history():
    from gini.agent.twin import TwinContext, adjudicate
    c = _c("objective:x")
    assert not adjudicate(c, "already covered earlier", TwinContext()).valid   # never covered
    ctx = TwinContext(history={"objective:x"})
    assert adjudicate(c, "covered in the previous hint", ctx).valid


def test_state_claim_translated_then_checked_by_the_oracle():
    from gini.agent.twin import TwinContext, adjudicate, state_holds
    world = _lan(hosts=1)                                   # has a switch, one host
    c = _c("objective:y")
    ctx_true = TwinContext(world=world, translate=lambda why: "exists(switch)")
    assert adjudicate(c, "the board has a switch so this is moot", ctx_true).valid
    ctx_false = TwinContext(world=world, translate=lambda why: "count(host) >= 2")
    a = adjudicate(c, "there are already two hosts", ctx_false)
    assert not a.valid and "FALSE" in a.reason              # the oracle contradicted the excuse
    ctx_none = TwinContext(world=world, translate=lambda why: None)
    assert not adjudicate(c, "some untranslatable claim", ctx_none).valid
    assert state_holds("exists(switch)", world) is True     # bare Topology gets wrapped
    assert state_holds("not a predicate((", world) is None  # unparsable -> can't validate


def test_unknown_justification_never_defeats():
    from gini.agent.twin import TwinContext, adjudicate
    assert not adjudicate(_c("z"), "", TwinContext(move_kind="hint")).valid
    assert not adjudicate(_c("z"), "just because", TwinContext(move_kind="hint")).valid


def test_bounded_rounds_then_flag_and_metrics():
    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        return '{"text":"stubborn","coverage":{"addressed":[],"omitted":[]}}'

    agent = _mk_agent(llm)
    move = agent.turn(Notification("objective_unmet", salience=0.4))
    assert "Also worth a look" in move.text
    res = agent.last_twin_result
    assert res.rounds == 2                                  # max_rounds=2, then flag — bounded
    m = agent.twin.metrics
    assert m["turns"] == 1 and m["revisions"] == 2 and m["flags"] == 1
    assert m["objections"] >= 1


def test_history_accumulates_and_defeats_already_claims():
    turn_no = {"n": 0}

    def llm(prompt):
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        ids = _ids_in(prompt)
        if turn_no["n"] == 0:                               # turn 1: address everything
            addressed = ",".join(f'"{i}"' for i in ids)
            return '{"text":"t1","coverage":{"addressed":[%s],"omitted":[]}}' % addressed
        omitted = ",".join('{"id":"%s","why":"already covered earlier"}' % i for i in ids)
        return '{"text":"t2","coverage":{"addressed":[],"omitted":[%s]}}' % omitted

    agent = _mk_agent(llm)
    agent.turn(Notification("objective_unmet", salience=0.4))          # turn 1 covers all
    assert agent.twin.history                                          # ...into the history
    turn_no["n"] = 1
    move = agent.turn(Notification("objective_unmet", salience=0.4))   # turn 2 omits: "already"
    assert move.text == "t2" and "Also worth a look" not in move.text  # defeated via history
    assert agent.last_twin_result.rounds == 0


def test_concern_context_injected_up_front():
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        if "Route this turn" in prompt:
            return '{"reason":true,"understand":false,"critic":false}'
        ids = _ids_in(prompt)
        addressed = ",".join(f'"{i}"' for i in ids)
        return '{"text":"ok","coverage":{"addressed":[%s],"omitted":[]}}' % addressed

    agent = _mk_agent(llm)
    agent.turn(Notification("objective_unmet", salience=0.4))
    covered = [p for p in prompts if "coverage" in p]
    assert covered and "Things that matter right now (ground truth):" in covered[0]
    # the grounding block precedes the task/checklist — it shapes the DRAFT, not just the report
    assert covered[0].index("Things that matter") < covered[0].index("Respond as ONE JSON")


# ---- structured outputs (the R2.0-A prerequisite) -------------------------- #
def test_ollama_backend_passes_schema_as_format():
    from gini.agent.llm.backend import Message
    from gini.agent.llm.ollama import OllamaBackend
    sent = []

    def transport(path, payload):
        sent.append((path, payload))
        return {"message": {"content": '{"ok":true}'}}

    be = OllamaBackend(transport=transport)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    out = list(be.chat([Message("user", "hi")], schema=schema))
    assert sent[0][1]["format"] == schema                   # constrained decode requested
    assert out and out[0].text == '{"ok":true}'
    sent.clear()
    list(be.chat([Message("user", "hi")]))                  # no schema -> no format key
    assert "format" not in sent[0][1]


def test_persona_runner_plumbs_schema_only_when_accepted():
    got = {}

    def with_schema(prompt, schema=None):
        got["schema"] = schema
        return "ok"

    p = Persona("X", system="sys", schema={"type": "object"})
    assert PersonaRunner(with_schema).call(p, task="t") == "ok"
    assert got["schema"] == {"type": "object"}
    # a plain lambda (no schema kwarg) keeps working — nothing is passed
    assert PersonaRunner(lambda pr: "plain").call(p, task="t") == "plain"

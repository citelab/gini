"""Reasoning 2.0 phases C/D/E — the coach + authoring surfaces, the learner seam, the harness."""
from types import SimpleNamespace as NS

from gini.agent.contracts import Notification
from gini.agent.meaning import MissionAgent
from gini.agent.personas import PersonaRunner
from gini.agent.twin import (
    GoldenTurn, Twin, authoring_concerns, coach_concerns, fallback_text, focus_line,
    learner_concerns, replay,
)
from gini.agent.twin.harness import HarnessReport
from gini.domain import assembly as A
from gini.domain.topology import Topology


# ---- C: the OS-coach enumerator -------------------------------------------- #
class _MS:
    """A machine-state stub: scheduling flags + a shadow manifest."""
    def __init__(self, flags=None, shadows=None):
        self._flags, self._shadows = flags or {}, shadows or {}
    def scheduling_flags(self):
        return self._flags
    def shadows(self):
        return self._shadows


def _ev(kind, detail, pid=None):
    return NS(kind=kind, detail=detail, pid=pid)


def test_coach_concerns_rank_a_faulted_shadow_first():
    events = [_ev("starvation", "pid 4 has stayed RUNNABLE for 9 slices", pid=4)]
    shadows = {"prio_sched": NS(faults=2, is_student=True, active=False)}
    concerns = coach_concerns(events, _MS(shadows=shadows))
    assert concerns[0].id == "shadow:prio_sched:faulted"    # salience 3 outranks the event
    assert "crashed 2x" in concerns[0].statement
    assert any(c.id == "watcher:starvation:4" for c in concerns)
    assert all(c.evidence for c in concerns)


def test_coach_active_flags_fill_in_without_duplicating_events():
    events = [_ev("starvation", "pid 4 starving", pid=4)]
    ms = _MS(flags={"starvation": {4}, "cpu_monopoly": {3}})
    ids = [c.id for c in coach_concerns(events, ms)]
    assert ids.count("watcher:starvation:4") == 1           # drained event wins, no duplicate
    assert "watcher:cpu_monopoly:3" in ids                  # active-but-undrained still surfaces


def test_coach_focus_and_fallback_render_the_concerns():
    concerns = coach_concerns([_ev("cpu_monopoly", "pid 3 hogs the CPU", pid=3)], _MS())
    assert "pid 3 hogs the CPU" in focus_line(concerns)
    assert "Worth looking at right now" in fallback_text(concerns)
    assert "steady" in fallback_text([])                    # empty set -> the calm default


def test_coach_prompt_carries_the_focus():
    from gini.agent.wizard import os_coach_prompt
    p = os_coach_prompt([], "CARD", 3, focus="The most salient issue RIGHT NOW: X")
    assert "The most salient issue RIGHT NOW: X" in p
    assert os_coach_prompt([], "CARD", 3).count("salient issue") == 0   # optional, absent by default


# ---- C: the authoring surface ----------------------------------------------- #
def test_authoring_flags_lexical_disagreement_and_infeasibility():
    prop = NS(archetype_id="load-balanced-web", lesson=None,
              infeasible="wants a firewall AND excludes firewalls", suppressed="")
    concerns = authoring_concerns("a mission about a basic lan with two hosts", prop)
    ids = [c.id for c in concerns]
    assert "authoring:infeasible" in ids                    # resolver findings surface as concerns
    dis = [c for c in concerns if c.id.startswith("authoring:lexical-disagreement")]
    assert dis and "why not" in dis[0].statement            # the ratify-time question


def test_authoring_quiet_when_the_pick_matches_the_words():
    from gini.agent.lesson_resolver import lexical_scores
    scores = lexical_scores("a mission about a basic lan with two hosts")
    top_id = scores[0][0]
    prop = NS(archetype_id=top_id, lesson=None, infeasible="", suppressed="")
    assert authoring_concerns("a mission about a basic lan with two hosts", prop) == []


# ---- D: the learner seam (stub-tested; real model = parallel track) ---------- #
def _learner(misconceptions=(), bands=None):
    return NS(misconceptions=list(misconceptions),
              band=lambda c: (bands or {}).get(c, "unknown"))


def test_learner_misconception_salience_follows_relevance():
    m = NS(id="priority-bigger-wins", concept="os-scheduling",
           statement="believes a bigger priority number wins", evidence=["e1", "e2"])
    on_topic = learner_concerns(_learner([m]), concepts=("os-scheduling",))
    assert on_topic[0].salience == 3 and "likely believes" in on_topic[0].statement
    off_topic = learner_concerns(_learner([m]), concepts=("networking-basics",))
    assert off_topic[0].salience == 2                       # active but not touching current work


def test_learner_cold_start_and_no_evidence_yield_nothing():
    ghost = NS(id="g", concept="os-scheduling", statement="s", evidence=[])
    assert learner_concerns(_learner([ghost]), concepts=("os-scheduling",)) == []
    # unknown band = cold start = NO concern (unknown != weak)
    assert learner_concerns(_learner(), concepts=("os-scheduling",)) == []
    weak = learner_concerns(_learner(bands={"os-scheduling": "weak"}),
                            concepts=("os-scheduling",))
    assert weak and weak[0].id == "learner:weak:os-scheduling"
    assert learner_concerns(None) == []


# ---- E: the harness ---------------------------------------------------------- #
def _mk_agent_factory():
    def make(llm):
        t = Topology()
        sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
        h = t.add_device("host", "H0"); t.add_link(h.id, sw.id); t.add_link(sw.id, r.id)
        from gini.agent.blackboard import Blackboard
        lesson = A.assemble(["basic-lan"], genre="experience", lesson_id="t")
        bb = Blackboard(); bb.load_lesson(lesson); bb.update(t)
        return MissionAgent(PersonaRunner(llm), bb, lesson, twin=Twin())
    return make


def _covering_llm(prompt):
    import re
    if "Route this turn" in prompt:
        return '{"reason":true,"understand":false,"critic":false}'
    ids = re.findall(r"^  (\S+): ", prompt, re.M)
    addressed = ",".join(f'"{i}"' for i in ids)
    return '{"text":"good line","coverage":{"addressed":[%s],"omitted":[]}}' % addressed


def _stubborn_llm(prompt):
    if "Route this turn" in prompt:
        return '{"reason":true,"understand":false,"critic":false}'
    return '{"text":"stubborn","coverage":{"addressed":[],"omitted":[]}}'


def test_harness_replays_a_golden_bank():
    make = _mk_agent_factory()
    bank = [
        GoldenTurn("covers-everything", make, _covering_llm,
                   trigger=Notification("objective_unmet", salience=0.4),
                   expect_flags=False, expect_objections=False, expect_clean=True),
        GoldenTurn("stubborn-gets-flagged", make, _stubborn_llm,
                   trigger=Notification("objective_unmet", salience=0.4),
                   expect_flags=True, expect_objections=True),
    ]
    report = replay(bank)
    assert report.passed, [o for o in report.outcomes if not o.ok]
    m = report.metrics()
    assert m["turns"] == 2 and m["pass_rate"] == 1.0
    assert m["false_objection_rate"] == 0.0                 # no nagging on the clean turn
    assert m["flag_rate"] == 0.5 and m["addressed_rate"] == 0.5


def test_harness_catches_a_false_objection_regression():
    # golden says clean, but the turn draws objections -> the report FAILS (the regression gate)
    make = _mk_agent_factory()
    bank = [GoldenTurn("should-be-clean", make, _stubborn_llm,
                       trigger=Notification("objective_unmet", salience=0.4),
                       expect_flags=False, expect_objections=False, expect_clean=True)]
    report = replay(bank)
    assert not report.passed
    assert report.metrics()["false_objection_rate"] == 1.0


def test_harness_survives_a_broken_turn():
    report = replay([GoldenTurn("boom", lambda llm: (_ for _ in ()).throw(RuntimeError("x")),
                                None)])
    assert not report.passed and "error" in report.outcomes[0].notes
    assert isinstance(report, HarnessReport)

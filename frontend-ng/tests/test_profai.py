"""Phases D–E: ProfAI and hosted StudentAI.

An AI answering as a professor is a claim of authority. If a student acts on a wrong ProfAI answer,
the cost lands on the student — so the guardrails are not decoration, they're the product:

  1. refuse-and-escalate on deadlines / exams / grades / policy — as a DETERMINISTIC pre-filter, not
     a polite request in a prompt;
  2. never assert a grade;
  3. every answer labelled, versioned, logged, reviewable.

Plus the rule that makes the whole thing trustworthy: **a present human is never pre-empted by their
own proxy.**
"""
import json
import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
sys.path.insert(0, str(_TC))

import ai as AI                                          # noqa: E402
import social as S                                       # noqa: E402
import teacher as T                                      # noqa: E402


class FakeLLM:
    """A model that will say ANYTHING — the point being that the guardrails must not depend on the
    model's good behaviour."""
    def __init__(self, reply="Sure! The exam is on Tuesday and you got an A."):
        self.reply, self.calls = reply, []

    def available(self):
        return True

    def chat(self, system, user):
        self.calls.append((system, user))
        return self.reply


@pytest.fixture()
def world(tmp_path):
    c = T.Course(tmp_path, "c1")
    c.enrol("ana", name="Ana", group="g1")
    c.enrol("ben", name="Ben", group="g1", ai_hosted=True)
    soc = S.Social(tmp_path, c)
    llm = FakeLLM()
    prof = AI.ProfAI(tmp_path, c, soc, llm=llm)
    sai = AI.StudentAI(tmp_path, c, soc, llm=llm)
    return prof, sai, soc, c, llm


# -- guardrail 1: the deterministic refusal ---------------------------------- #
@pytest.mark.parametrize("q", [
    "when is the lab due?",
    "can I get an extension on assignment 2?",
    "what will be on the final exam?",
    "will subnetting come up on the midterm?",
    "what's my grade in this course?",
    "I want to appeal my mark, it's unfair",
    "what's the policy on academic integrity here?",
])
def test_dangerous_questions_never_reach_the_model(world, q):
    """A regex is a mechanism; asking a model to please not answer is a hope. The model here would
    happily invent a deadline and a grade — it never gets the chance."""
    prof, _, _, _, llm = world
    r = prof.answer("ana", q)
    assert r["kind"] == "refusal"
    assert r["escalate"] is True
    assert llm.calls == []                               # the model was NOT consulted
    assert "flagged" in r["body"]                        # …and the student is told what happens next


def test_an_ordinary_question_does_reach_the_model(world):
    prof, _, _, _, llm = world
    llm.reply = "A switch forwards within one subnet; a router forwards between subnets."
    r = prof.answer("ana", "what's the difference between a switch and a router?")
    assert r["kind"] == "ai" and "router" in r["body"]
    assert llm.calls                                     # this one is safe to answer


def test_the_system_prompt_still_carries_the_hard_rules(world):
    """Belt and braces: the pre-filter is the mechanism, but the prompt says it too — a model that
    volunteers a grade unprompted should also have been told not to."""
    prof, _, _, _, llm = world
    prof.answer("ana", "explain ARP")
    system = llm.calls[0][0]
    assert "Never state or imply a student's grade" in system
    assert "deadline" in system and "labelled as an AI" in system


# -- the reply ladder --------------------------------------------------------- #
def test_a_present_teacher_is_never_pre_empted_by_their_own_proxy(world):
    """If students can't tell which one they're talking to, they stop trusting both."""
    prof, _, soc, _, _ = world
    soc.heartbeat("teacher")                             # the professor is at their desk
    assert prof.should_answer("teacher") is False

    import time as _t
    soc.store.put_presence("teacher", _t.time() - 10_000, None)   # …and now they've gone home
    assert prof.should_answer("teacher") is True


def test_the_teacher_can_switch_the_proxy_off_entirely(world):
    prof, _, _, _, _ = world
    prof.persona.save({"auto_answer": False})
    assert prof.should_answer("teacher") is False        # away, but silent — messages just queue


# -- guardrail 3: labelled, versioned, logged, reviewable ---------------------- #
def test_every_answer_is_logged_for_review_with_its_persona_version(world):
    prof, _, _, _, _ = world
    r = prof.answer("ana", "explain subnet masks")
    prof.log_answer("ana", "explain subnet masks", r, "msg-1")
    q = prof.review_queue()
    assert len(q) == 1 and q[0]["student"] == "ana"
    assert q[0]["persona_version"] == r["persona_version"]
    assert q[0]["reviewed"] is False


def test_escalations_sort_to_the_top_of_the_review_queue(world):
    prof, _, _, _, _ = world
    ok = prof.answer("ana", "explain ARP")
    prof.log_answer("ana", "explain ARP", ok, "m1")
    bad = prof.answer("ben", "when is it due?")          # a refusal → escalated
    prof.log_answer("ben", "when is it due?", bad, "m2")
    q = prof.review_queue()
    assert q[0]["message_id"] == "m2"                    # the thing needing a HUMAN comes first


def test_a_correction_becomes_a_standing_answer(world):
    """The loop that makes the persona improve from experience instead of from prompt engineering."""
    prof, _, _, _, llm = world
    prof.persona.add_standing_answer("what is a VPC", "A VPC is your own private slice of the cloud "
                                                      "network — I explain it in week 6.")
    r = prof.answer("ana", "what is a VPC exactly?")
    assert r["kind"] == "standing"
    assert "week 6" in r["body"]
    assert llm.calls == []                               # the teacher's own words, not a guess


def test_a_dead_model_says_nothing_rather_than_guessing(world):
    prof, _, _, _, _ = world

    class Dead:
        def chat(self, *a):
            raise OSError("connection refused")
    prof.llm = Dead()
    r = prof.answer("ana", "explain ARP")
    assert r["kind"] == "unavailable" and r["escalate"] is True
    assert "rather say nothing than guess" in r["body"]


# -- capacity ------------------------------------------------------------------ #
def test_a_student_spamming_is_rate_limited_not_served(world):
    prof, _, _, _, _ = world
    assert prof.answer("ana", "explain ARP")["kind"] == "ai"
    assert prof.answer("ana", "explain ARP again")["kind"] == "busy"   # honest, not a queue-forever


# -- the digest ---------------------------------------------------------------- #
def test_the_digest_works_even_with_no_model_because_the_FACTS_are_deterministic(world):
    """A professor with no time needs the situation, not the transcript. The LLM only phrases facts
    we computed ourselves — so if it's down, you still get the part that matters."""
    prof, _, soc, _, _ = world

    class Dead:
        def chat(self, *a):
            raise OSError("nope")
    prof.llm = Dead()

    soc.heartbeat("ana", {"lesson_id": "lab01", "title": "Build a LAN", "level": 1, "met": 1,
                          "total": 7})
    soc.heartbeat("ben", {"lesson_id": "lab01", "title": "Build a LAN", "level": 1, "met": 1,
                          "total": 7})
    soc.send("ana", "teacher", "I really don't get subnetting")

    d = prof.digest()
    assert d["summary"] == ""                            # no model → no prose…
    assert d["facts"]["stuck"][0]["group"] == "g1"       # …but the facts are all there
    assert d["facts"]["stuck"][0]["level"] == 1
    assert d["facts"]["unanswered"][0]["student"] == "ana"


# -- Phase E: hosted StudentAI -------------------------------------------------- #
def test_a_student_proxy_needs_BOTH_the_teachers_grant_and_the_students_consent(world):
    """Either one alone is not consent. The teacher grants capacity; the student decides to speak
    through a proxy at all."""
    _, sai, _, _, _ = world
    assert sai.granted("ben") and not sai.granted("ana")   # teacher granted Ben only

    assert not sai.enabled("ben")                          # granted, but Ben hasn't opted in
    sai.set_pref("ben", True)
    assert sai.enabled("ben")

    sai.set_pref("ana", True)                              # opted in, but never granted
    assert not sai.enabled("ana")


def test_a_student_proxy_refuses_the_same_dangerous_topics(world):
    _, sai, _, _, llm = world
    sai.set_pref("ben", True)
    r = sai.answer("ben", "ana", "hey when's the assignment due?")
    assert r["kind"] == "refusal" and llm.calls == []


def test_a_student_proxy_never_speaks_for_a_PRESENT_student(world):
    _, sai, soc, _, _ = world
    sai.set_pref("ben", True)
    soc.heartbeat("ben")                                  # Ben is right there — let Ben answer
    assert sai.should_answer("ben") is False

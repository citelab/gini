"""The lab's questions on the gBuilder side: what arrives, what is answered, what is submitted.

The design in one line: the Teaching Center hands the prompts over when a code is armed, the
student answers in a tab beside Terminal, the answers go into the proof chain, and a person marks
them.

Everything here is a rule that depends on, written as the thing that must not happen:

  * an answer that never reaches the chain, or reaches it after the work was handed in;
  * a student shown the questions of a lab they are no longer recording;
  * a box being rebuilt underneath someone who is typing in it;
  * gBuilder scoring an answer;
  * a student armed offline hearing nothing about questions they were never sent.

That last one is what the bit in the assignment code is for, and it is the only part of this that
could not have been done with a network call.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain import lab_questions as lq          # noqa: E402
from gini.domain import proof as P                   # noqa: E402
from gini.domain import proof_events as ev           # noqa: E402
from gini.domain import ticket as T                  # noqa: E402

REPLY = {"ok": True, "activity": "comp535/lab1", "title": "Multi-LAN",
         "questions": [{"id": "q1", "prompt": "What IP did you give M1?"},
                       {"id": "q2", "prompt": "Which command showed the route?"}]}


# ---- what arrives from the server ----------------------------------------------- #
def test_the_prompts_come_out_of_an_arm_reply():
    qs = lq.questions_from(REPLY)
    assert [q.id for q in qs] == ["q1", "q2"]
    assert qs[0].prompt == "What IP did you give M1?"


def test_a_question_has_nowhere_to_put_an_answer_key():
    """The key never crosses the wire — the Teaching Center strips it — and a field for it here
    would be an invitation to start carrying one."""
    assert not hasattr(lq.Question("q1", "?"), "answer")


def test_an_older_course_server_is_not_a_traceback():
    """This parses a reply from a server that may be a version behind. A student mid-lab must not
    meet an exception because their course has not been updated."""
    for junk in ({}, {"ok": True}, {"questions": None}, {"questions": "two"},
                 {"questions": [1, "x", {}, {"id": "", "prompt": "no id"}]}):
        assert lq.questions_from(junk) == []
    assert lq.questions_from(None) == []


# ---- the chain is the state ------------------------------------------------------ #
def _chain(*answers):
    c = P.Chain.start("A" * 12, assignment="comp535/lab1", gini_version="t", t=1000.0)
    for i, (qid, text) in enumerate(answers, 1):
        c.append(*ev.answer(qid, "prompt for " + qid, text), t=1000.0 + i)
    return c


def test_answers_are_read_back_out_of_the_chain():
    """No separate record of what has been answered. The chain is what gets submitted and marked,
    so anything held beside it is a second copy that can disagree with the one that counts."""
    assert lq.answers_in(_chain(("q1", "10.0.0.2")).entries) == {"q1": "10.0.0.2"}


def test_thinking_again_gives_the_last_answer():
    got = lq.answers_in(_chain(("q1", "first"), ("q1", "second")).entries)
    assert got == {"q1": "second"}


def test_answers_survive_a_round_trip_through_disk():
    """A resumed code, or a restarted gBuilder, reads its chain back as plain dicts."""
    proof = P.build_proof(_chain(("q1", "10.0.0.2")))
    assert lq.answers_in(proof["entries"]) == {"q1": "10.0.0.2"}


def test_other_entries_are_not_mistaken_for_answers():
    c = _chain(("q1", "yes"))
    c.append(*ev.command("M1", "ip route", ["default via 10.0.0.1"]), t=1100.0)
    assert lq.answers_in(c.entries) == {"q1": "yes"}


def test_a_deliberate_blank_counts_as_answered():
    """A student who submitted a blank has said something — that they had nothing. Nagging them at
    hand-in would treat their decision as an oversight."""
    qs = lq.questions_from(REPLY)
    assert lq.unanswered(qs, {"q1": "", "q2": ""}) == []
    assert lq.nudge(qs, {"q1": "", "q2": ""}) == ""


def test_the_nudge_names_the_questions_left():
    qs = lq.questions_from(REPLY)
    said = lq.nudge(qs, {"q1": "10.0.0.2"})
    assert "Which command showed the route?" in said
    assert "What IP did you give M1?" not in said


def test_the_summary_counts_what_is_done():
    qs = lq.questions_from(REPLY)
    assert lq.summary(qs, {}) == "0 of 2"
    assert lq.summary(qs, {"q1": "x"}) == "1 of 2"
    assert lq.summary(qs, {"q1": "x", "q2": "y"}) == "all answered"
    assert lq.summary([], {}) == ""


# ---- the bit in the code --------------------------------------------------------- #
def test_the_code_alone_says_the_lab_has_questions():
    """The reason this is in the ticket and not fetched: gBuilder arms offline, and by the time you
    could fetch this you would not need it."""
    assert lq.missing_because_offline(T.mint(questions=True).questions, []) is True
    assert lq.missing_because_offline(T.mint().questions, []) is False


def test_having_the_questions_is_not_a_problem():
    assert lq.missing_because_offline(True, lq.questions_from(REPLY)) is False


# ---- the recorder ---------------------------------------------------------------- #
class _Bus:
    def __getattr__(self, _n):
        return type("S", (), {"connect": lambda *a: None, "emit": lambda *a: None})()


def _recorder(tmp_path):
    """A real recorder over a real chain store. The chain IS the state under test, so a mock
    would be testing the mock."""
    import pathlib as _pl

    from gini.domain.topology import Topology
    from gini.services.proof_recorder import ProofRecorder

    class Ctx:
        bus = _Bus()
        settings = type("S", (), {"tc_url": "https://tc.example", "tc_course": "comp535"})()
        topology = Topology("lab1")

    return ProofRecorder(Ctx(), store=P.ChainStore(_pl.Path(str(tmp_path))))


@pytest.fixture
def rec(tmp_path):
    r = _recorder(tmp_path)
    ok, _ = r.arm(T.mint(questions=True).pretty)
    assert ok
    r.note_questions(lq.questions_from(REPLY))
    return r


def test_an_answer_lands_in_the_chain(rec):
    assert rec.note_answer("q1", "What IP did you give M1?", "10.0.0.2") is True
    assert rec.answers() == {"q1": "10.0.0.2"}


def test_the_prompt_travels_with_the_answer(rec):
    """A teacher may edit or retire a question between the lab and the marking, and an answer
    whose question changed underneath it is worse than no answer at all."""
    rec.note_answer("q1", "What IP did you give M1?", "10.0.0.2")
    entry = [e for e in rec._chain.entries if e.kind == ev.ANSWER][-1]
    assert entry.data["prompt"] == "What IP did you give M1?"


def test_nothing_is_recorded_when_nothing_is_being_recorded(tmp_path):
    assert _recorder(tmp_path).note_answer("q1", "?", "an answer") is False


def test_an_answer_after_handing_in_is_refused(rec):
    """It would land past the `submit` entry — not in the proof that was sent — so accepting it
    would let a student type into a box no marker will ever read."""
    rec.note_answer("q1", "?", "before")
    assert rec.generate_proof()["ok"]
    assert rec.note_answer("q2", "?", "after") is False
    assert "q2" not in rec.answers()


def test_cancelling_takes_the_questions_with_it(rec):
    """Cancel is the whole departure. A student who cancels and arms a DIFFERENT code must not be
    shown the last lab's questions over a fresh chain."""
    rec.cancel()
    assert rec.questions == []


def test_the_answers_are_in_the_proof_that_is_submitted(rec):
    """The point of the whole design: the answers are part of the submission, not beside it."""
    rec.note_answer("q1", "What IP did you give M1?", "10.0.0.2")
    proof = rec.generate_proof()["proof"]
    assert lq.answers_in(proof["entries"]) == {"q1": "10.0.0.2"}
    assert P.verify_proof(proof).ok, "an answer must not break the chain"


def test_a_key_that_leaked_from_the_server_would_still_not_reach_the_student():
    """Defence in depth. The Teaching Center strips `answer` in the handler and has its own test
    saying so — but if a future version ever leaked one, gBuilder must not carry it into a widget
    where a student could read it out of the page. `Question` has nowhere to put it."""
    leaky = {"questions": [{"id": "q1", "prompt": "What IP?", "answer": "10.0.0.2"}]}
    q, = lq.questions_from(leaky)
    assert "10.0.0.2" not in repr(q)
    assert set(vars(q)) == {"id", "prompt"}


def test_nothing_here_judges_an_answer(rec):
    """No verdict comes back from recording one, and the chain entry holds only what was said.
    There is no key on this side of the wire and nothing here should ever start looking for one."""
    rec.note_answer("q1", "What IP did you give M1?", "utter nonsense")
    entry = [e for e in rec._chain.entries if e.kind == ev.ANSWER][-1]
    assert set(entry.data) == {"id", "prompt", "text"}

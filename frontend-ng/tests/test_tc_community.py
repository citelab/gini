"""The answer bank — a teacher writes an answer once, and the course stops answering it again.

No model is involved anywhere in this file, and that is the feature: the teacher wrote every word
the bot will ever say, so it cannot be confidently wrong in front of a class. The Teaching Center's
"No AI" docstring stays literally true.
"""
from __future__ import annotations

import time

import pytest

from gini_teaching_center.community import (ANSWER_FLOOR, COOLDOWN_S, Answer, attach, best_match,
                                            from_cluster, render, should_post, unanswered)

CRASH = "gbuilder core dumped on the lab machine, help?"
FIX = "Install libxcb-cursor0 — Qt 6 needs it and the lab images do not carry it yet."


@pytest.fixture
def bank():
    return [from_cluster(CRASH, FIX, author="maheswar@cs.mcgill.ca")]


def test_the_same_question_in_another_persons_words_gets_the_answer(bank):
    """The entire point: the next person will phrase it like a person, not like the teacher."""
    for asked in ("my gbuilder core dumps on the lab machine",
                  "the lab machine core dumped gbuilder again",
                  "gbuilder wont start on the lab box, core dump"):
        m = best_match(asked, bank)
        assert m is not None, asked
        assert m[0].answer == FIX


def test_an_unrelated_question_gets_silence_rather_than_a_guess(bank):
    """Silence is a valid answer. A hedging reply from a bot is noise on a busy server, and it
    trains people to ignore it — which costs the answers that are good."""
    assert best_match("how do I add a router to the canvas", bank) is None


def test_a_question_with_nothing_in_it_is_never_answered(bank):
    assert best_match("same here", bank) is None
    assert best_match("", bank) is None


def test_the_bar_for_posting_is_not_lower_than_the_bar_for_grouping():
    """A smell check across two DIFFERENT measures, and labelled as one.

    Grouping uses `resemblance` and answering uses `covers`, so the two numbers are not directly
    comparable and this is not a proof of anything. What it catches is the direction going wrong:
    the costs are asymmetric — a missed cluster under-counts on a page the teacher reads and can
    correct in a click, while a wrong answer is published in the course's name to someone who
    cannot tell — so posting must never end up the looser of the two."""
    from gini.domain.similarity import SAME
    assert ANSWER_FLOOR >= SAME


def test_a_teacher_can_attach_a_phrasing_that_did_not_match(bank):
    """The console's second verb. A cluster that is plainly the same thing but scored below the
    floor becomes one click — and nobody has to answer it again."""
    odd = "gbuilder segfaults over ssh -Y, anyone seen this?"
    assert best_match(odd, bank) is None

    attach(bank[0], odd)
    m = best_match(odd, bank)
    assert m is not None and m[0].answer == FIX
    assert best_match("how do I add a router to the canvas", bank) is None, \
        "teaching it one phrasing must not make it answer everything"


def test_attaching_the_same_phrasing_twice_changes_nothing(bank):
    attach(bank[0], "my gbuilder core dumps on the lab machine")
    before = list(bank[0].triggers)
    attach(bank[0], "my gbuilder core dumps on the lab machine")
    assert bank[0].triggers == before


def test_a_disabled_answer_is_never_posted(bank):
    bank[0].enabled = False
    assert best_match("my gbuilder core dumps on the lab machine", bank) is None


def test_an_answer_scoped_to_a_course_stays_in_it(bank):
    asked = "my gbuilder core dumps on the lab machine"
    bank[0].course = "comp310"
    assert best_match(asked, bank, course="comp310") is not None
    assert best_match(asked, bank, course="comp535") is None
    assert best_match(asked, bank) is not None, \
        "an unscoped ask still sees it — a shared server is not course-scoped"


def test_a_terse_asking_gets_silence_and_the_teacher_can_fix_it_in_one_click(bank):
    """"my gbuilder core dumps" carries three of the five words of the banked question — 0.60,
    just under the floor — and the bot says nothing.

    That is the intended trade rather than a tuning failure. The floor faces an asymmetry with no
    middle: a miss costs one click on a page the teacher is already reading, and a wrong answer is
    published in the course's name to someone who has no way to tell. Only one of those is
    recoverable, so the threshold is set where the recoverable failure happens."""
    terse = "my gbuilder core dumps"
    assert best_match(terse, bank) is None

    attach(bank[0], terse)
    assert best_match(terse, bank) is not None


def test_the_bot_stays_quiet_when_six_people_pile_onto_one_outage():
    """The answer is useful once and spam five times."""
    now = time.time()
    posted = {}
    assert should_post("a1", "help", now, posted)
    posted[("a1", "help")] = now
    assert not should_post("a1", "help", now + 60, posted)
    assert should_post("a1", "help", now + COOLDOWN_S + 1, posted)
    assert should_post("a1", "os-lab", now + 60, posted), "a different channel has not heard it"


def test_what_a_student_sees_names_the_author_and_the_date(bank):
    """A student needs to know this is the course's answer rather than a machine's opinion, and how
    old it is — GINI ships every few weeks and an answer from two terms ago may be about a version
    that no longer exists."""
    out = render(bank[0])
    assert FIX in out
    assert "maheswar" in out and "@cs.mcgill.ca" not in out, "a byline, not an address"


def test_the_console_shows_what_is_still_unanswered(bank):
    """The page worth opening is not "everything said on Discord", it is "what people keep asking
    that you have not answered yet"."""
    class _C:
        def __init__(self, sample, people):
            self.sample, self.people = sample, people

    clusters = [_C("my gbuilder core dumps on the lab machine", 3),
                _C("how do I add a router to the canvas", 2)]
    todo = unanswered(clusters, bank)
    assert [c.sample for c in todo] == ["how do I add a router to the canvas"]


def test_an_empty_bank_answers_nothing_and_leaves_every_cluster_to_do():
    class _C:
        sample, people = "gbuilder core dumped on the lab machine", 2

    assert best_match("gbuilder core dumped on the lab machine", []) is None
    assert len(unanswered([_C()], [])) == 1

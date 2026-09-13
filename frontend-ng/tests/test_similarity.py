"""Two jobs that look like one, and the measure each of them needs.

Grouping two reports of the same problem, and matching a message against a question the course has
already answered, are not the same question — and using one measure for both got the second one
wrong in a way that cost a real answer its posting.
"""
from __future__ import annotations

from gini.domain.similarity import MIN_TERMS, SAME, covers, overlap, terms


def test_two_people_describing_one_failure_look_the_same():
    """The measured pair that killed an earlier design, kept so it cannot come back: a hash of the
    term set filed these as two incidents because they differ by the word "help"."""
    a = terms("gbuilder core dumped on the lab machine, help?")
    b = terms("the lab machine core dumped gbuilder")
    assert overlap(a, b) >= SAME


def test_two_different_complaints_sharing_a_word_do_not():
    a = terms("gbuilder core dumped on the lab machine")
    b = terms("gbuilder wont open the router lab window")
    assert overlap(a, b) < SAME


def test_a_message_with_too_little_in_it_matches_nothing():
    """"same here" must not join every open incident. Empty joins nothing rather than everything."""
    assert terms("same here") == ""
    assert overlap("", "core dump gbuild lab") == 0.0
    assert covers("core dump gbuild lab", "") == 0.0


def test_terms_needs_real_content_words():
    assert terms("is it?") == ""
    assert len(terms("gbuilder core dumped on the lab machine").split()) >= MIN_TERMS


def test_extra_words_in_a_students_message_are_not_evidence_against_a_match():
    """The asymmetry that matters. A student saying MORE than the banked question is still asking
    it, and `overlap` punishes them for the extra words — 0.50 here, below any sane posting floor —
    while `covers` sees that every content word of the known question is present."""
    known = terms("gbuilder core dumped on the lab machine")
    said = terms("gbuilder wont start on the lab box, core dump")
    assert overlap(known, said) < 0.62, "if this rises, the test below stops proving anything"
    assert covers(known, said) >= 0.75


def test_covers_is_asymmetric_and_overlap_is_not():
    known = terms("gbuilder core dumped on the lab machine")
    said = terms("gbuilder core dumped on the lab machine in the trottier lab over ssh yesterday")
    assert covers(known, said) == 1.0, "the known question is entirely present"
    assert covers(said, known) < 1.0, "the longer message is not entirely present in the shorter"
    assert overlap(known, said) == overlap(said, known)


def test_an_unrelated_message_covers_nothing():
    known = terms("gbuilder core dumped on the lab machine")
    assert covers(known, terms("how do I add a router to the canvas")) < 0.3


def test_a_terse_question_and_a_wordy_one_about_the_same_thing_are_alike():
    """Measured on a real term of traffic: Jaccard at 0.5 found three recurring things in 130
    questions. That was not a quiet server — it was a measure reporting on message length."""
    from gini.domain.similarity import resemblance
    a = terms("Where do we send the receipt code?")
    b = terms("hi, quick question — where are we supposed to send the receipt code once we finish?")
    assert overlap(a, b) < 0.5, "if this rises, the assertion below stops proving anything"
    assert resemblance(a, b) >= 0.9


def test_resemblance_still_keeps_unrelated_questions_apart():
    from gini.domain.similarity import resemblance
    a = terms("Where do we send the receipt code?")
    b = terms("how do I add a router to the canvas")
    assert resemblance(a, b) == 0.0


def test_the_guard_against_a_scrap_matching_everything_is_MIN_TERMS_not_MIN_SHARED():
    """Dividing by the smaller set makes a short message cheap to match, and the floor that stops
    that is `terms` refusing to produce anything at all below MIN_TERMS content words.

    The earlier version of this test hand-wrote a two-word terms string and asserted it matched
    nothing — proving something about a value `terms` can never return, while the real floor
    (MIN_SHARED) was set high enough to file "where do we send the receipt code" and "where does
    the receipt code go" as two separate questions."""
    from gini.domain.similarity import MIN_TERMS, resemblance
    assert terms("router canvas") == "", "a scrap is not matchable in the first place"
    assert resemblance("", "router canva topolog link switch") == 0.0
    assert len(terms("how do I add a router to the canvas").split()) >= MIN_TERMS


def test_one_incidental_word_in_common_is_still_not_a_match():
    """What MIN_SHARED does rule out, now that MIN_TERMS carries the weight."""
    from gini.domain.similarity import resemblance
    a = terms("where do we send the receipt code")
    b = terms("the code in trap.c is where the timer lands")
    assert len(set(a.split()) & set(b.split())) == 1
    assert resemblance(a, b) == 0.0

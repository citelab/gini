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

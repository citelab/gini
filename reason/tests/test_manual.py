"""Reading the OS manual — the corpus that was written to be read by this and never was.

`docs/manual/README.md` claims every page carries frontmatter and the same heading skeleton so that
"a retrieval system can answer 'what is this number?', 'where does it come from?', and 'what are its
limits?' from a single page". These tests hold the manual to that claim as much as they test the
reader: if a page loses its frontmatter or its Limits section, this is where it shows.
"""
from __future__ import annotations

from gini_reason.manual import Page, find, limits, manual_root, pages


def test_every_page_in_the_manual_is_indexable():
    ps = pages()
    assert len(ps) >= 15, f"only {len(ps)} pages indexed from {manual_root()}"
    assert all(isinstance(p, Page) and p.id and p.title for p in ps)


def test_a_file_without_frontmatter_is_not_a_page():
    """The README is prose about the manual, not a page of it."""
    assert "README.md" not in {p.name for p in pages()}


def test_the_frontmatter_lists_are_read_as_lists():
    traps = next(p for p in pages() if p.id == "os-traps")
    assert "scause" in traps.keywords
    assert "/traps" in traps.endpoints


def test_a_two_word_question_still_finds_its_page():
    """The regression that made this worth testing: scoring ran through `terms()`, which refuses
    anything under three content words, so "lock contention" normalised to nothing and the manual
    returned nothing at all for a subject it has a whole page on."""
    hits = find("what is a lock contention")
    assert hits, "a two-word question found nothing"
    assert hits[0][0].id == "os-locks"


def test_the_page_that_answers_wins_rather_than_the_alphabet():
    hits = find("why is my process stuck in the scheduler")
    assert hits and hits[0][0].id == "os-scheduler"


def test_a_question_the_manual_has_nothing_on_returns_nothing():
    """Silence is a result. A retriever that always returns its three least-bad pages hands the
    layer above three irrelevant citations and no way to tell."""
    assert find("how do I make a topology in the canvas") == []
    assert find("") == []


def test_a_page_states_what_must_not_be_claimed():
    """`limits()` is the Reasoning Twin's evidence — an answer asserting something a page lists as
    a limit is a silent miss that can be proved rather than suspected."""
    traps = next(p for p in pages() if p.id == "os-traps")
    ls = limits(traps)
    assert ls, "os-traps has no Limits and honesty bullets"
    joined = " ".join(ls).lower()
    assert "observer appears in the data" in joined
    assert "scause 14 does not exist" in joined


def test_almost_every_page_carries_its_limits():
    """The manual's own rule. A page without them is not a broken test — it is a page that has not
    said what it cannot tell you, which is the heading most worth keeping."""
    missing = [p.id for p in pages() if not limits(p)]
    assert len(missing) <= 2, f"pages with no Limits and honesty section: {missing}"


def test_a_section_is_found_even_when_its_heading_was_extended():
    """Two pages write "Wire format — record reference" where the rest write "Wire format", and a
    caller should not have to know which."""
    with_wire = [p for p in pages() if p.section("Wire format")]
    assert len(with_wire) >= 14

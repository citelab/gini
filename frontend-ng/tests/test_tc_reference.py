"""A reference work the course can be answered OUT of, not merely pointed at.

Until now the Teaching Center stored a filename and a title, and `tc_ask.as_context` said so in as
many words: "a material is named and linked, never quoted — the server stores a filename, not the
text inside it". That is the boundary this moves. A reference holds the prose, so a question can be
matched against the words a student actually types rather than against a heading.

Modelled apart from materials on purpose. A material belongs to one course and a teacher owns it; a
reference is the same text for everybody, so holding it per course would mean N copies of one book
and one teacher's re-index landing in another's course.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini_teaching_center.store import Store                  # noqa: E402

XV6 = {"id": "xv6-riscv-book", "title": "xv6: a simple, Unix-like teaching operating system",
       "source_url": "https://xv6-guide.github.io/xv6-riscv-book/",
       "licence": "MIT", "attribution": "Copyright (c) 2006-2024 Russ Cox, Frans Kaashoek, "
                                        "Robert Morris"}

SECTIONS = [
    {"id": "xv6/7.5", "ref": "xv6-riscv-book", "number": "7.5", "title": "Sleep and wakeup",
     "url": "https://x/Ch7.S5.html", "ord": 1,
     "body": "A process that must wait yields the CPU by sleeping on a channel until another "
             "process wakes it. Sleep and wakeup are the primitives that let threads interact."},
    {"id": "xv6/3.1", "ref": "xv6-riscv-book", "number": "3.1", "title": "Paging hardware",
     "url": "https://x/Ch3.S1.html", "ord": 2,
     "body": "The RISC-V page table maps virtual addresses to physical addresses through three "
             "levels of page directory."},
    {"id": "xv6/6.1", "ref": "xv6-riscv-book", "number": "6.1", "title": "Race conditions",
     "url": "https://x/Ch6.S1.html", "ord": 3,
     "body": "Two CPUs updating the same list without a lock produces a race, and the process "
             "may lose an update."},
]


@pytest.fixture
def store(tmp_path):
    Store._instances.clear()
    st = Store(str(tmp_path))
    st.reference_put(XV6)
    st.sections_put("xv6-riscv-book", SECTIONS)
    st.course_ref_set("comp310", "xv6-riscv-book", True)
    return st


def _titles(hits):
    return [h["title"] for h in hits]


# ---- retrieval ----------------------------------------------------------------- #
def test_a_question_finds_the_section_that_answers_it(store):
    """The whole point, and the thing the old ranker could not do: not one word of this question
    appears in the heading "Sleep and wakeup"."""
    hits = store.search_sections("why is my process stuck waiting", ["xv6-riscv-book"])
    assert _titles(hits)[0] == "Sleep and wakeup"


def test_ranking_puts_the_better_match_first(store):
    hits = store.search_sections("page table virtual address", ["xv6-riscv-book"])
    assert _titles(hits)[0] == "Paging hardware"


def test_a_coincidence_is_not_a_match(store):
    """BM25 returns EVERY row sharing a single term, so a floor is what stops a question about
    processes dragging in every section that happens to use the word once."""
    hits = store.search_sections("why is my process stuck waiting", ["xv6-riscv-book"])
    assert "Race conditions" not in _titles(hits)


def test_the_cutoff_survives_a_corpus_of_one(store):
    """BM25 is RELATIVE to the corpus: a term's weight is how rare it is, so in a one-section index
    every term is in every document and every score collapses towards zero. An absolute floor was
    tried and rejected a freshly re-indexed book outright."""
    store.sections_put("xv6-riscv-book", [SECTIONS[1]])
    assert _titles(store.search_sections("page table virtual", ["xv6-riscv-book"]))


def test_a_question_about_nothing_in_the_book_finds_nothing(store):
    assert store.search_sections("kubernetes ingress controller", ["xv6-riscv-book"]) == []


def test_higher_is_better_like_every_other_hit(store):
    """`bm25()` returns a NEGATIVE number, most negative being best. Left raw it would have sorted
    backwards against the activity and material scores every caller already compares."""
    hits = store.search_sections("page table", ["xv6-riscv-book"])
    assert hits and all(h["score"] > 0 for h in hits)


def test_a_section_carries_where_to_read_the_whole_thing(store):
    """A quote without a citation is worse than no quote: the student cannot check it, and the
    licence requires the source travel with the text."""
    hit = store.search_sections("sleeping on a channel", ["xv6-riscv-book"])[0]
    assert hit["url"] and hit["number"] and hit["title"]


# ---- a course opts in ---------------------------------------------------------- #
def test_a_course_that_has_not_attached_it_never_sees_it(store):
    """A networking course must not be answered out of an operating-systems book that happens to
    share the word 'block'."""
    assert store.course_refs("comp535") == []
    assert store.search_sections("sleeping", store.course_refs("comp535")) == []


def test_attaching_and_detaching(store):
    store.course_ref_set("comp535", "xv6-riscv-book", True)
    assert store.course_refs("comp535") == ["xv6-riscv-book"]
    store.course_ref_set("comp535", "xv6-riscv-book", False)
    assert store.course_refs("comp535") == []


def test_attaching_twice_is_not_an_error(store):
    store.course_ref_set("comp310", "xv6-riscv-book", True)
    assert store.course_refs("comp310") == ["xv6-riscv-book"]


# ---- a student's question is not a query language ------------------------------ #
@pytest.mark.parametrize("hostile", [
    'what is a "pipe',                       # unbalanced quote
    "process AND",                           # a dangling FTS5 operator
    "NEAR(", "*", "^", "process OR OR page",
    "'; DROP TABLE reference_section; --",
])
def test_a_hostile_question_finds_nothing_rather_than_erroring(store, hostile):
    """A tutor that returns a database error because a student typed an apostrophe is worse than
    one that finds nothing. Every term is quoted before it reaches FTS5."""
    assert isinstance(store.search_sections(hostile, ["xv6-riscv-book"]), list)
    assert store.search_sections("page table", ["xv6-riscv-book"]), "the index survived"


# ---- re-indexing ---------------------------------------------------------------- #
def test_reindexing_replaces_rather_than_merges(store):
    """A section the source dropped must stop answering questions out of a book that no longer
    contains it — which means the full-text index has to lose it too, not just the table."""
    store.sections_put("xv6-riscv-book", [SECTIONS[1]])
    assert store.search_sections("sleeping on a channel", ["xv6-riscv-book"]) == []
    assert store.search_sections("page table", ["xv6-riscv-book"])
    assert store.reference("xv6-riscv-book")["sections"] == 1


def test_the_full_text_index_follows_an_edit(store):
    """External-content FTS5 keeps only the terms; without the triggers a re-index leaves the old
    terms pointing at rows that no longer say that."""
    edited = dict(SECTIONS[0], body="Now it is entirely about semaphores and nothing else.")
    store.sections_put("xv6-riscv-book", [edited])
    assert store.search_sections("semaphores", ["xv6-riscv-book"])
    # A term from the OLD body only. Querying "sleeping" would still match, correctly — the title
    # is unchanged and FTS5's porter stemmer reaches "Sleep and wakeup" from it, which was this
    # test asserting its own mistake rather than a fault in the index.
    assert store.search_sections("channel", ["xv6-riscv-book"]) == []


def test_deleting_a_reference_takes_its_sections_and_attachments(store):
    store.reference_delete("xv6-riscv-book")
    assert store.references() == []
    assert store.course_refs("comp310") == []
    assert store.search_sections("page table", ["xv6-riscv-book"]) == []


# ---- the licence travels with it ------------------------------------------------ #
def test_a_reference_records_its_licence_and_attribution(store):
    """Columns rather than a comment, because carrying the notice is a CONDITION of use: the xv6
    book is MIT-licensed "provided they include the original copyright notice and license terms"."""
    ref = store.reference("xv6-riscv-book")
    assert ref["licence"] and "Cox" in ref["attribution"]
    assert ref["source_url"].startswith("https://")


def test_an_older_database_gains_the_reference_tables(tmp_path):
    """The migration rule the store already lives by: additive, never destructive."""
    import sqlite3
    (tmp_path / "data").mkdir()
    db = sqlite3.connect(tmp_path / "data" / "gini.db")
    db.execute("CREATE TABLE course (id TEXT PRIMARY KEY)")
    db.execute("INSERT INTO course VALUES ('comp310')")
    db.commit()
    db.close()
    Store._instances.clear()
    st = Store(str(tmp_path))
    assert [c["id"] for c in st.courses()] == ["comp310"], "an existing course was lost"
    st.reference_put(XV6)
    st.sections_put("xv6-riscv-book", SECTIONS)
    assert st.search_sections("page table", ["xv6-riscv-book"])

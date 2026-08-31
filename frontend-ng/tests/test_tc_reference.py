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


# ---- a question that is not about this book ------------------------------------- #
# Found against the real 81-section xv6 index: "kubernetes ingress controller" returned three
# sections of an operating-systems book, every one of them on the strength of "controller" alone.
# The relative cutoff could not see it — all three were equally bad, so a fraction of the best
# kept them all. Coverage is the rule that can: how much of the question did this actually answer?
@pytest.mark.parametrize("nonsense", [
    "kubernetes ingress controller",
    "how do I bake a cake",
    "postgres replication lag",
])
def test_a_question_about_something_else_finds_nothing(store, nonsense):
    assert store.search_sections(nonsense, ["xv6-riscv-book"]) == []


def test_coverage_is_counted_on_a_prefix_because_the_index_stems(store):
    """FTS5 stems and Python does not. An exact substring test says "waiting" is absent from "wait
    yields", under-counts the best hit there is, and throws it away — which is exactly what an
    earlier version of this rule did."""
    hits = store.search_sections("a process waiting on a sleeping channel", ["xv6-riscv-book"])
    assert _titles(hits) == ["Sleep and wakeup"]


def test_a_short_question_is_not_held_to_the_coverage_rule(store):
    """Two words are all-or-nothing anyway; requiring two of two would only ever restate what FTS
    already decided."""
    assert _titles(store.search_sections("page table", ["xv6-riscv-book"])) == ["Paging hardware"]


def test_at_most_the_limit_comes_back(store):
    """Filtering happens after the query, so the query asks for more than it needs — without the
    final truncation a permissive question would return everything that survived."""
    assert len(store.search_sections("process page lock", ["xv6-riscv-book"], limit=1)) <= 1


# ---- the Library: a global shelf, linked per course ---------------------------- #
# One copy of a book serves every course that links it — that is what stops five operating-systems
# courses holding five copies of one index. The link is a TEACHER's decision, and it is not only
# about topic collisions: it is about which vocabulary a class is taught in. A course that says
# "thread" where a book says "process" does not want the two mixed in front of students.
def test_the_shelf_is_global_and_the_link_is_not(store):
    """The book exists once. Two courses linking it share the same rows and the same index."""
    store.course_ref_set("comp535", "xv6-riscv-book", True)
    assert len(store.references()) == 1
    for course in ("comp310", "comp535"):
        assert store.course_refs(course) == ["xv6-riscv-book"]
    assert _titles(store.search_sections("page table", store.course_refs("comp535")))


def test_a_reindex_reaches_every_course_at_once(store):
    """The point of one copy. Per course, you would have to remember which courses had it."""
    store.course_ref_set("comp535", "xv6-riscv-book", True)
    store.sections_put("xv6-riscv-book", [dict(SECTIONS[0], body="A brand new edition says this.")])
    for course in ("comp310", "comp535"):
        assert _titles(store.search_sections("brand new edition", store.course_refs(course)))


def test_unlinking_leaves_the_book_on_the_shelf(store):
    """Unlinking is a course saying "not in my class", not a librarian throwing a book out."""
    store.course_ref_set("comp310", "xv6-riscv-book", False)
    assert store.course_refs("comp310") == []
    assert len(store.references()) == 1
    assert store.reference("xv6-riscv-book")["sections"] == len(SECTIONS)


# ---- worth having, not worth quoting -------------------------------------------- #
def test_an_aside_ranks_last_but_is_never_dropped(tmp_path):
    """BM25 normalises by length, so a textbook's short "Exercises" sections outrank the chapter
    that explains the thing: two of the three passages sent to answer "why is my process stuck
    waiting" were lists of homework, which is more questions handed to a model asked a question.

    Ranked last rather than removed, so a student who asks about the exercises still finds them.
    """
    Store._instances.clear()
    st = Store(str(tmp_path))
    st.reference_put({**XV6, "aside_titles": "Exercises"})
    st.sections_put("xv6-riscv-book", SECTIONS + [
        {"id": "xv6/7.11", "ref": "xv6-riscv-book", "number": "7.11", "title": "Exercises",
         "url": "https://x/Ch7.S11.html", "ord": 4,
         "body": "Sleep and wakeup: what happens if a process wakes another process that is not "
                 "sleeping? Modify the sleep channel."}])
    got = [h["title"] for h in st.search_sections("sleeping process wakeup channel",
                                                  ["xv6-riscv-book"], limit=5)]
    assert "Exercises" in got, "an aside must still be findable"
    assert got.index("Sleep and wakeup") < got.index("Exercises")


def test_a_book_that_marks_no_asides_is_unaffected(store):
    """It is a property of a BOOK, held as data — two general rules were measured against the real
    index first and neither worked. Interrogative density does not separate exercises from prose
    (xv6's are imperative, and their median count of question marks is zero while an ordinary
    section has the highest in the book), and neither does length."""
    assert store.reference("xv6-riscv-book")["aside_titles"] in ("", None)
    assert _titles(store.search_sections("page table", ["xv6-riscv-book"])) == ["Paging hardware"]



def test_a_sqlite_without_fts5_still_runs_the_server(tmp_path, monkeypatch):
    """FTS5 is compiled into almost every sqlite3 the standard library ships, and "almost" is not
    a thing to bet a department's server on. Created in one executescript with the ordinary
    indexes, a build without it would take the whole Teaching Center down at startup, on a machine
    nobody can attach a debugger to. Losing library search is a missing feature; losing the server
    is a lost afternoon for a class."""
    import gini_teaching_center.store as S
    # The script is pointed at a module that certainly does not exist, which produces exactly the
    # error a build without FTS5 raises — `sqlite3.Connection` is an immutable type, so its methods
    # cannot be patched, and faking it at that level would test the mock rather than the fallback.
    monkeypatch.setattr(S, "_FTS", "CREATE VIRTUAL TABLE nope USING fts_not_compiled_in(x);")
    S.Store._instances.clear()
    st = S.Store(str(tmp_path))                     # must not raise
    assert st.has_fts is False
    st.put_course({"id": "comp310", "title": "OS"})  # the rest of the server is unaffected
    assert [c["id"] for c in st.courses()] == ["comp310"]
    assert st.search_sections("anything", ["xv6-riscv-book"]) == []

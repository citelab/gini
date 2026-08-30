"""The course as something the tutor is ANSWERABLE for, not merely told.

gBuilder already asked the Teaching Center what a course holds on a question and pasted the answer
into the prompt. Nothing then checked whether the model used any of it — a student could ask about
connecting two LANs, have a released activity called "Multi-LAN routing" that is the very lab they
are recording, and get an answer out of general knowledge with no trace anywhere that the course
was ignored. The material was delivered and nobody was answerable for it.

These are the rules that decide when that becomes an obligation. Rules only, never a model — the
salience philosophy is that a noisy Twin is tuned HERE (twin/salience.py).
"""
from __future__ import annotations

import pytest

from gini.agent.twin import course as C
from gini.agent.twin.salience import MUST_ADDRESS


def hit(**kw):
    base = {"kind": "activity", "id": "comp535/lab1", "title": "Multi-LAN routing",
            "brief": "Join two LANs with a router.", "score": 6.0}
    base.update(kw)
    return base


# ---- when the course becomes an obligation ------------------------------------ #
def test_the_lab_you_are_being_marked_on_must_be_addressed():
    """The one case worth an objection. Everything else in a course is context."""
    c, = C.course_concerns([hit()], current_lab="comp535/lab1")
    assert c.salience >= MUST_ADDRESS
    assert "the lab you are recording" in c.statement


def test_everything_else_in_the_course_is_tracked_but_never_objected_about():
    """A tutor that had to name every handout sharing a word with the question would be a nag.
    Salience 1 is precisely the 'track it, never object' tier."""
    for h in (hit(id="comp535/lab7", title="Firewalls"),
              hit(kind="material", id="m1", title="Subnetting handout")):
        c, = C.course_concerns([h], current_lab="comp535/lab1")
        assert c.salience < MUST_ADDRESS


def test_a_student_who_is_not_recording_owes_nothing():
    """No armed code means no lab, and nothing here is what they are being marked on. Browsing
    the tutor between labs must not produce obligations."""
    assert all(c.salience < MUST_ADDRESS for c in C.course_concerns([hit()]))


def test_a_hit_on_another_course_lab_is_not_your_lab():
    c, = C.course_concerns([hit(id="comp535/lab9", title="Multicast")],
                           current_lab="comp535/lab1")
    assert c.salience < MUST_ADDRESS


def test_a_material_is_never_must_address_even_in_your_own_lab():
    """A material is a title and a filename — the server stores no text from inside it, so
    'you did not mention the PDF' is not something anyone can act on."""
    c, = C.course_concerns([hit(kind="material", id="comp535/lab1", title="Lab 1 handout")],
                           current_lab="comp535/lab1")
    assert c.salience < MUST_ADDRESS


# ---- no evidence, no concern --------------------------------------------------- #
def test_a_weak_match_is_not_evidence_of_anything():
    """`search.rank` scores 3.0 for a query word in a title and 1.0 for one in a brief, so a lone
    brief word is a coincidence, not a relevant lab."""
    assert C.course_concerns([hit(score=1.0)], current_lab="comp535/lab1") == []
    assert C.course_concerns([hit(score=C.MIN_SCORE)], current_lab="comp535/lab1")


def test_a_hit_with_no_title_is_dropped():
    assert C.course_concerns([hit(title="")], current_lab="comp535/lab1") == []


def test_every_concern_carries_its_evidence():
    """The Twin can only cite what GINI can prove — the rule every source obeys."""
    for c in C.course_concerns([hit(), hit(kind="material", id="m1", title="Handout", score=3.0)],
                               current_lab="comp535/lab1"):
        assert c.evidence and "score" in c.evidence


def test_an_unreachable_course_accuses_nobody():
    """A network failure must never become 'the model ignored the course'. `tc_ask.ask` reports
    every failure as an empty answer, so this is the shape that reaches us."""
    assert C.course_concerns([]) == []
    assert C.course_concerns(None) == []


def test_junk_from_the_server_is_ignored_rather_than_raised():
    """It is a network response. It is not allowed to break a turn."""
    assert C.course_concerns(["nonsense", None, {}, {"score": "x"}]) == []


def test_the_concern_set_is_capped():
    """MAX_CONCERNS — the Twin whispers, it does not checklist."""
    many = [hit(id=f"comp535/lab{i}", title=f"Lab {i}") for i in range(20)]
    assert len(C.course_concerns(many, current_lab="comp535/lab1")) <= 5


def test_ids_are_stable_and_namespaced():
    """Concern ids are the index an exact set diff runs against, and they share a namespace with
    every other source — a bare lab id could collide with an objective's."""
    c, = C.course_concerns([hit()], current_lab="comp535/lab1")
    assert c.id == "course:comp535/lab1" and c.kind == "course"


# ---- where the lab comes from -------------------------------------------------- #
def test_the_current_lab_is_read_from_the_recorder():
    """Settings holds the COURSE; this needs the LAB, and gBuilder only learns that from the
    Teaching Center when a code is armed."""
    class Rec:
        def status(self):
            return {"activity": "comp535/lab1"}
    assert C.current_lab_of(Rec()) == "comp535/lab1"


def test_no_recorder_and_a_broken_recorder_both_mean_no_lab():
    class Broken:
        def status(self):
            raise RuntimeError("nope")
    assert C.current_lab_of(None) == ""
    assert C.current_lab_of(Broken()) == ""


def test_the_recorder_keeps_the_lab_the_server_named(tmp_path):
    """It always received `activity` at arm time and only ever printed it."""
    from gini.domain import proof as P
    from gini.services.proof_recorder import ProofRecorder
    rec = ProofRecorder(None, store=P.ChainStore(tmp_path))
    assert rec.status()["activity"] == ""
    rec.note_activity("comp535/lab1", "Multi-LAN routing")
    assert C.current_lab_of(rec) == "comp535/lab1"
    assert rec.status()["activity_title"] == "Multi-LAN routing"


def test_cancelling_forgets_which_lab_it_was(tmp_path):
    """Leaving recording mode leaves it entirely — the tutor must not go on treating a lab as the
    one being marked after the student has stopped recording it."""
    from gini.domain import proof as P
    from gini.domain.ticket import mint
    from gini.services.proof_recorder import ProofRecorder

    class Ctx:
        topology = None
        bus = None
    rec = ProofRecorder(Ctx(), store=P.ChainStore(tmp_path))
    rec.arm(mint(lambda n: bytes(range(n))).pretty)
    rec.note_activity("comp535/lab1", "Multi-LAN routing")
    rec.cancel()
    assert C.current_lab_of(rec) == ""


# ---- a book is context, never an obligation ------------------------------------ #
def test_a_library_hit_is_never_must_address():
    """A book is not what the student is being marked on, and an answer is not wrong for having
    explained something in its own words rather than the book's. The Twin tracks it; it does not
    object about it, however well the passage scored."""
    c, = C.course_concerns([hit(kind="reference", id="xv6/7.6", title="7.6 Sleep and wakeup",
                                score=9.0)],
                           current_lab="comp535/lab1")
    assert c.salience < MUST_ADDRESS
    assert "library" in c.statement


def test_a_library_hit_reads_as_a_book_not_as_a_handout():
    """Wording matters here: "this course posts …" would describe a teacher's upload, and a book
    on a shared shelf is not that."""
    c, = C.course_concerns([hit(kind="reference", id="x", title="7.6 Sleep and wakeup",
                                score=6.0)])
    assert "posts" not in c.statement

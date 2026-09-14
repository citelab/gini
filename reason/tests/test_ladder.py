"""The ladder — what GINI AI is willing to say, and how far down it drops when it cannot.

Every test here runs with **no model**, which is the point: the lower rungs are the outage plan, so
they have to be the tested path rather than the untested one.
"""
from __future__ import annotations

from gini_reason.audit import concerns, flags, review
from gini_reason.ladder import Draft, answer, context, retrieve


class _Scripted:
    """A model that says exactly what a test needs it to say."""

    def __init__(self, text):
        self.text, self.seen = text, []

    def chat(self, messages, tools=None, stream=False):
        self.seen.append(messages)

        class _C:
            def __init__(self, t):
                self.text = t
        return iter([_C(self.text)])

    def available(self):
        return True


# -- L1: retrieval, and the confidence that gates everything above it --------------- #

def test_retrieval_finds_the_page_and_carries_what_it_forbids():
    g = retrieve("why is my process stuck in the scheduler")
    assert [p.id for p, _ in g.pages] == ["os-scheduler"]
    assert g.limits(), "a page's stated limits must travel with the grounding"
    assert all(name.endswith(".md") for name, _line in g.limits())


def test_a_question_nothing_covers_grounds_empty():
    assert retrieve("what is the airspeed of a swallow").strength == "empty"


def test_the_students_own_course_material_counts_as_strong():
    """`twin/course.py`'s argument: the lab they are being marked on outranks GINI's general
    knowledge, because it is the thing that decides their grade."""
    g = retrieve("how do I connect two LANs",
                 course_hits=[{"title": "Multi-LAN routing", "brief": "Join two LANs."}])
    assert g.strength == "strong"


# -- the rungs ---------------------------------------------------------------------- #

def test_with_no_model_the_answer_drops_to_pointing_at_the_material():
    d = answer("why is my process stuck in the scheduler")
    assert d.rung == "L1" and not d.used_model
    assert "os-02-scheduler.md" in d.text


def test_with_nothing_retrieved_it_refuses_and_names_a_human():
    d = answer("what is the airspeed of a swallow")
    assert d.rung == "L3"
    assert "teaching team" in d.text
    assert "not sure" not in d.text.lower(), "a hedge teaches people to ignore the bot"


def test_the_model_only_runs_on_strong_grounding():
    llm = _Scripted("composed answer")
    d = answer("why is my process stuck in the scheduler", llm=llm)
    assert d.rung == "L1" and not d.used_model, "thin grounding must not reach the model"
    assert llm.seen == [], "and must not even call it"


def test_on_strong_grounding_the_model_composes_and_is_told_only_what_was_retrieved():
    llm = _Scripted("Use the Multi-LAN routing lab.")
    d = answer("how do I connect two LANs", llm=llm,
               course_hits=[{"title": "Multi-LAN routing", "brief": "Join two LANs."}])
    assert d.rung == "L2" and d.used_model
    prompt = "".join(m.content for m in llm.seen[0])
    assert "Multi-LAN routing" in prompt
    assert "ONLY from the material" in prompt


def test_a_model_that_fails_drops_a_rung_rather_than_the_answer():
    class _Broken:
        def chat(self, *a, **k):
            raise OSError("the tunnel is down")

        def available(self):
            return True

    d = answer("how do I connect two LANs", llm=_Broken(),
               course_hits=[{"title": "Multi-LAN routing", "brief": "Join two LANs."}])
    assert d.rung == "L1" and d.text, "a dead GPU must not become a dead answer"


def test_the_prompt_carries_what_the_page_forbids():
    g = retrieve("why is my process stuck in the scheduler")
    assert "Limits this page states" in context(g)


# -- the audit ---------------------------------------------------------------------- #

def test_an_answer_that_cites_nothing_it_was_given_is_objected_to():
    """`twin/course.py`'s gap, one layer up: the material was delivered and nobody was answerable
    for it."""
    g = retrieve("why is my process stuck in the scheduler")
    objs = review("why is my process stuck", g, "Your process is probably blocked on I/O.")
    assert objs and any("names none of the pages" in o.concern.statement for o in objs)


def test_an_answer_that_names_its_page_draws_no_objection():
    g = retrieve("why is my process stuck in the scheduler")
    assert review("why is my process stuck", g,
                  "See os-02-scheduler.md — it shows gini_pick and the quantum.") == []


def test_ignoring_the_students_own_lab_is_an_objection():
    g = retrieve("how do I connect two LANs",
                 course_hits=[{"title": "Multi-LAN routing", "brief": "Join two LANs."}])
    objs = review("how do I connect two LANs", g, "Put a router between them and set gateways.")
    assert any(o.concern.kind == "course" for o in objs)

    ok = review("how do I connect two LANs", g,
                "Your course's Multi-LAN routing lab covers exactly this.")
    assert not any(o.concern.kind == "course" for o in ok)


def test_every_concern_carries_deterministic_evidence():
    """The Twin's rule, and the reason an objection is worth reading: it may only cite what GINI
    can prove."""
    g = retrieve("why is my process stuck in the scheduler")
    assert all(c.evidence for c in concerns(g))


def test_flags_read_as_sentences_for_a_teacher():
    g = retrieve("why is my process stuck in the scheduler")
    out = flags(review("q", g, "unrelated prose about nothing in particular"))
    assert out and all(" — " in f for f in out)


def test_the_draft_is_a_draft():
    """Nothing in this module posts anything. It returns text and flags; who may see it is the
    Teaching Center's decision and depends on which door asked."""
    d = answer("why is my process stuck in the scheduler")
    assert isinstance(d, Draft)
    assert not hasattr(d, "post") and not hasattr(d, "send")

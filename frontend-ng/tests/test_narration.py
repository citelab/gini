"""The model writes the mission's story; GINI checks the story against what it actually grades.

The bug this guards: a mission titled "Decoupling Web and Database with a Queue" sitting on top of
VPC-isolation objectives. Note that the title and brief AGREED WITH EACH OTHER perfectly — they were
written in one breath. Prose-vs-prose agreement is worthless. The only agreement that means anything
is prose ⟷ objectives, and that is checkable without asking the model to police itself.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.agent import lesson_resolver as R, narration as N
from gini.domain import lesson as L


def _rb():
    return L.from_archetype("reachability-boundary", {}, id="rb")


def test_graded_types_are_the_licensed_vocabulary():
    got = N.graded_types(_rb())
    assert {"vpc", "web_app", "database"} <= got     # what the objectives really check
    assert "queue" not in got and "cache" not in got


def test_false_claims_catches_prose_about_things_nothing_grades():
    allowed = N.graded_types(_rb())
    assert N.false_claims("Keep the database private inside its VPC.", allowed) == []
    assert "queue" in N.false_claims("Decouple the web tier with a message queue.", allowed)
    assert "cache" in N.false_claims("Put a cache in front of the database.", allowed)


def _llm(title, description):
    return lambda prompt: json.dumps({"title": title, "description": description})


def test_a_truthful_narration_is_accepted():
    t, d = N.narrate(_rb(), "private database, public web", _llm(
        "Private Database, Public Web",
        "You'll put a web app and a managed database inside one VPC. The web app must reach the "
        "database, while the database stays unreachable from the internet. Done means the boundary "
        "holds under a live check."))
    assert t == "Private Database, Public Web"
    assert "database" in d.lower() and len(d.split(".")) >= 3       # a real description, not a stub


def test_a_LYING_narration_is_refused_even_though_it_reads_beautifully():
    t, d = N.narrate(_rb(), "private database, public web", _llm(
        "Decoupling Web and Database with a Queue",
        "You'll place a message queue between the web tier and the database so neither blocks the "
        "other. The queue absorbs bursts. Done means messages flow end to end."), retries=0)
    assert (t, d) == ("", "")            # rejected: no objective grades a queue → caller falls back


def test_a_rambling_title_is_refused():
    t, _ = N.narrate(_rb(), "x", _llm(
        "A Long Winding Title About Keeping Your Database Private Inside A VPC Boundary",
        "You'll put a web app and a database in a VPC. The database stays private. Done when it "
        "holds."), retries=0)
    assert t == ""                       # "short title" is part of the contract


def test_compose_narrates_from_the_ASSEMBLED_mission_not_the_guess():
    """End-to-end: the composer must describe the mission it BUILT. The model here tries to sell a
    queue story (the original bug); the objectives are about a VPC, so the lie is dropped and the
    fragment's own — always-true — summary survives."""
    def liar(prompt):
        if "\"primary\"" in prompt:      # the archetype-selection call
            return json.dumps({"primary": "decouple-with-queue", "secondary": "",
                               "title": "Queues Everywhere", "brief": "A queue story."})
        return json.dumps({"title": "Queues Everywhere",       # the narration call
                           "description": "You'll wire a message queue between the tiers."})

    p = R.compose("private database, public web", llm=liar, lesson_id="c1")
    les = p.lesson
    assert "vpc" in N.graded_types(les)                       # we assembled the VPC mission…
    assert not N.false_claims(les.title, N.graded_types(les))  # …and the title cannot mention a queue
    assert not N.false_claims(les.brief, N.graded_types(les))
    assert "queue" not in (les.title + les.brief).lower()

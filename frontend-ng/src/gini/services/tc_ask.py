"""Asking the course what it says about something.

The tutor's link to the Teaching Center. A student types a question in Ask GINI; this carries it to
the course server, which searches that course's released activities and materials and hands back
what it found. gBuilder does no searching of its own — the course's material lives on the server
and stays there.

**Scoped by the Course in Settings, and nothing else.** That field is what stops one class's
handouts answering another class's question, and it is why an unknown course is worth reporting
plainly rather than silently returning nothing: the fix is in the student's own Settings.

**No account, by design.** A student never signs in — a code is a scope, not an identity — so this
is unauthenticated, like the `/m/<id>` materials it points at.

Failure is never an exception a caller has to catch to keep working. The tutor is useful offline;
losing the course server should make its answers thinner, not stop it answering.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .tc_submit import Insecure, Unreachable, _require_tls

TIMEOUT = 8.0        # a person is waiting on this one, unlike a hand-in


class Answer:
    """What the course had to say. Falsey when it had nothing, so callers can just test it."""

    __slots__ = ("course", "hits", "error", "reason")

    def __init__(self, course: str = "", hits=None, error: str = "", reason: str = "") -> None:
        self.course, self.hits = course, list(hits or [])
        self.error, self.reason = error, reason

    def __bool__(self) -> bool:
        return bool(self.hits)

    def __repr__(self) -> str:
        return f"Answer(course={self.course!r}, hits={len(self.hits)}, reason={self.reason!r})"


def ask(url: str, course: str, question: str, *, timeout: float = TIMEOUT) -> Answer:
    """Ask one course's material a question. Never raises.

    An empty `Answer` carrying a `reason` is how everything that went wrong is reported — no
    server configured, no course of that name, the network down. The tutor keeps working either
    way; it simply has less to go on.
    """
    if not (url or "").strip():
        return Answer(reason="no_server")
    if not (course or "").strip():
        return Answer(reason="no_course",
                      error="No course is set — Settings → Teaching Center.")
    if not (question or "").strip():
        return Answer(course=course)
    try:
        _require_tls(url)
    except Insecure as e:
        return Answer(course=course, reason="insecure", error=str(e))

    q = urllib.parse.urlencode({"course": course, "q": question})
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/ask?" + q, timeout=timeout) as r:
            obj = json.loads(r.read() or b"null") or {}
    except urllib.error.HTTPError as e:
        try:
            obj = json.loads(e.read() or b"{}")
        except Exception:                                        # noqa: BLE001
            obj = {}
        return Answer(course=course, reason=obj.get("reason", "refused"),
                      error=obj.get("error", f"The course server replied with {e.code}."))
    except Exception as e:                                       # noqa: BLE001
        # Including a timeout. A tutor that stalls on bad wifi is worse than one that answers from
        # what it already knows.
        return Answer(course=course, reason="unreachable", error=str(e))
    if not obj.get("ok"):
        return Answer(course=course, reason=obj.get("reason", "refused"),
                      error=obj.get("error", ""))
    return Answer(course=obj.get("course", course), hits=obj.get("hits"))


def as_context(answer: Answer, *, limit: int = 4) -> str:
    """The hits as plain text a model can be given, or "" when there is nothing.

    Titles and briefs only. A material is named and linked, never quoted — the server stores a
    filename, not the text inside it, and inventing a summary of a PDF nobody read is exactly the
    kind of confident wrongness a tutor must not produce.
    """
    lines = []
    for h in (answer.hits or [])[:limit]:
        if h.get("kind") == "activity":
            brief = (h.get("brief") or "").strip()
            lines.append(f"- Activity “{h.get('title', '')}”"
                         + (f": {brief}" if brief else ""))
        else:
            lines.append(f"- Course material “{h.get('title', '')}” ({h.get('url', '')})")
    return ("From this course's material:\n" + "\n".join(lines)) if lines else ""


__all__ = ["Answer", "ask", "as_context", "Insecure", "Unreachable"]

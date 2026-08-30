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


# ---- checking the link, from a terminal --------------------------------------- #
# `python3 -m gini.services.tc_ask "how do I connect two LANs"`
#
# The tutor asks silently and shows nothing — working offline is normal, so a warning on every
# question would be worse than the problem. That silence makes "is GINI actually reading my
# course?" unanswerable from inside the app, which is what this is for. It reads gBuilder's OWN
# settings rather than taking a URL, because a check against hand-typed values proves the server
# works and not that the app is pointed at it.
def _cli(argv=None) -> int:                                  # pragma: no cover - a terminal tool
    import argparse
    from ..app import paths

    ap = argparse.ArgumentParser(
        prog="python3 -m gini.services.tc_ask",
        description="Show what the Teaching Center gives GINI for a question.")
    ap.add_argument("question", nargs="*", help="what a student would type into Ask GINI")
    ap.add_argument("--url", default="", help="override the course server in Settings")
    ap.add_argument("--course", default="", help="override the Course in Settings")
    ap.add_argument("--json", action="store_true", help="print the raw hits instead")
    a = ap.parse_args(argv)

    cfg = paths.load_config()
    url = a.url or cfg.get("tc_url", "") or ""
    course = a.course or cfg.get("tc_course", "") or ""
    question = " ".join(a.question).strip()

    # --json prints JSON and nothing else, so it can be piped. A header on stdout would make
    # every caller strip it first, and one of them would forget.
    if not a.json:
        print(f"  server    {url or '(not set)'}")
        print(f"  course    {course or '(not set)'}")
        print(f"  question  {question or '(none)'}\n")
    if not question:
        print("  Give a question to ask. Nothing is sent without one.")
        return 2

    answer = ask(url, course, question)
    if a.json:
        print(json.dumps({"course": answer.course, "reason": answer.reason,
                          "error": answer.error, "hits": answer.hits}, indent=2))
        return 0 if answer.hits else 1

    if answer.reason:
        # Every one of these is a different fix, so none of them is "it didn't work".
        print(f"  ✗ {answer.reason}: {answer.error or 'no detail given'}")
        print({
            "no_server": "  Set the course server in Settings → Teaching Center.",
            "no_course": "  Set the Course in Settings → Teaching Center.",
            "no_such_course": "  The server is up and does not have that course. Check "
                              "the spelling against the Teaching Center console.",
            "insecure": "  GINI speaks HTTPS only. The URL must start with https://.",
            "unreachable": "  The server could not be reached — VPN, or the wrong port.",
        }.get(answer.reason, "  The server refused the question."))
        return 1

    if not answer.hits:
        # The honest list of reasons, because "0 hits" on its own sends people to the network.
        print("  ✓ reached the course, and it matched nothing.\n")
        print("  The search reads TITLES and BRIEFS of released activities, and the titles,")
        print("  filenames and links of materials. It does NOT read inside a PDF. So:")
        print("    · an activity still in draft is never returned — release it;")
        print("    · a question whose words appear only inside a handout cannot match;")
        print("    · try words from the lab's title.")
        return 1

    print(f"  ✓ {len(answer.hits)} hit(s) from {answer.course}\n")
    for h in answer.hits:
        where = h.get("url", "") if h.get("kind") == "material" else h.get("lab", "")
        print(f"    {h.get('kind', ''):9} {h.get('title', ''):32} "
              f"score {h.get('score', 0):<4} {where}")

    context = as_context(answer)
    print("\n  What GINI is given, verbatim:\n")
    print("  " + "─" * 68)
    for line in context.splitlines():
        print("  " + line)
    print("  " + "─" * 68)
    print("\n  Titles and briefs only — a material is named and linked, never quoted.")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    sys.exit(_cli())

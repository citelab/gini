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
        # A certificate failure is NOT an outage, and saying "unreachable" sent someone to check a
        # VPN and a port number while the server sat there answering. `tc_submit` has told the two
        # apart since it was written; this asked the same server over the same TLS and did not.
        import ssl as _ssl
        cause = getattr(e, "reason", e)
        if isinstance(cause, _ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            return Answer(course=course, reason="untrusted", error=str(e))
        # Including a timeout. A tutor that stalls on bad wifi is worse than one that answers from
        # what it already knows.
        return Answer(course=course, reason="unreachable", error=str(e))
    if not obj.get("ok"):
        return Answer(course=course, reason=obj.get("reason", "refused"),
                      error=obj.get("error", ""))
    return Answer(course=obj.get("course", course), hits=obj.get("hits"))


#: A picture that will not fit in the answer panel is not an illustration.
MAX_FIGURE_BYTES = 2_000_000


def figures(answer: Answer, url: str, *, limit: int = 3) -> list[tuple]:
    """Download the pictures attached to a course's passages: [(caption, bytes), …].

    Never raises, like everything else here. A tutor whose answer fails because a diagram did not
    arrive is worse than one that answers without the diagram — and the words are the answer.
    """
    out: list[tuple] = []
    for h in (answer.hits or []):
        for f in (h.get("figures") or []):
            if len(out) >= limit:
                return out
            try:
                with urllib.request.urlopen(
                        url.rstrip("/") + f["url"], timeout=TIMEOUT) as r:
                    blob = r.read(MAX_FIGURE_BYTES + 1)
                if blob and len(blob) <= MAX_FIGURE_BYTES:
                    out.append((f.get("caption", ""), blob))
            except Exception:                                    # noqa: BLE001
                continue
    return out


def as_context(answer: Answer, *, limit: int = 4) -> str:
    """The hits as plain text a model can be given, or "" when there is nothing.

    An ACTIVITY or a MATERIAL is named and linked, never quoted. That was once the rule for
    everything here, and its reason was exact: the server stored a filename, not the text inside
    it, so any summary would have been invented.

    A LIBRARY hit is different, and only because the reason changed. The server now holds the
    book's own words, so it sends them — verbatim, with the section, the link and the copyright
    line. Quoting real text is the opposite of the failure that rule guarded against; paraphrasing
    it would BE that failure.
    """
    named, quoted = [], []
    for h in (answer.hits or [])[:limit]:
        kind = h.get("kind")
        if kind == "activity":
            brief = (h.get("brief") or "").strip()
            named.append(f"- Activity “{h.get('title', '')}”" + (f": {brief}" if brief else ""))
        elif kind == "reference":
            quoted.append(_cite(h))
        else:
            named.append(f"- Course material “{h.get('title', '')}” ({h.get('url', '')})")
    blocks = []
    if named:
        blocks.append("From this course's material:\n" + "\n".join(named))
    if quoted:
        blocks.append(
            "From the books this course has linked. These are VERBATIM passages — use them, and "
            "say which section you took a point from so the student can go and read it. Do not "
            "present them as your own words, and do not stretch them past what they say:\n\n"
            + "\n\n".join(quoted))
    return "\n\n".join(blocks)


def _cite(hit: dict) -> str:
    """One passage, with everything needed to check it and everything the licence requires.

    The attribution is part of the block rather than gathered up at the end, because a model given
    four passages and one trailing notice will attach the notice to the wrong one — or to none.
    """
    book = (hit.get("book") or "").strip()
    where = " ".join(x for x in (hit.get("title", ""),) if x).strip()
    head = f"{book} — {where}" if book else where
    parts = [f"[{head}]", f"  {hit.get('passage', '').strip()}"]
    # A caption, never the picture. A text model cannot see a diagram, and telling it one exists
    # lets it point the student at "Figure 8.1" without ever implying it looked at it.
    for f in (hit.get("figures") or []):
        if f.get("caption"):
            parts.append(f"  (a diagram accompanies this passage: {f['caption']})")
    if hit.get("url"):
        parts.append(f"  Read it: {hit['url']}")
    if hit.get("attribution"):
        parts.append(f"  {hit['attribution']}")
    return "\n".join(parts)


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
            "untrusted": ("  The server IS up — its certificate is not one this machine trusts,\n"
                          "  which in practice means a self-signed one. Point at it:\n"
                          "      SSL_CERT_FILE=<the server's cert.pem> gbuilder\n"
                          "  Retrying, the VPN and the port number are all beside the point."),
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
    books = sum(1 for h in answer.hits if h.get("kind") == "reference")
    print(f"\n  {books} passage(s) quoted from the library; activities and materials are named "
          f"and linked, never quoted." if books else
          "\n  Titles and briefs only — a material is named and linked, never quoted.")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    sys.exit(_cli())

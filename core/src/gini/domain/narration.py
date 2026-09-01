"""Chain → a transcript an instructor can skim.

Plain rendering, no model. The chain is the evidence and the narration is only its reading; if a
sentence here cannot be pointed at a specific entry, it does not belong. That is the same
invariant as the mission oracle and the Reasoning Twin: GINI states the facts, a person judges
them. (Phase 2's LLM layer reads this same data — it never replaces it, and it never grades.)

Written for the person marking thirty of these on a Sunday: a header that says whose work this is
and whether it hangs together, a timeline in the student's own vocabulary, and a short verdict
paragraph at the end that names anything the chain cannot account for.
"""
from __future__ import annotations

import time

from . import proof_events as ev
from .proof import GENESIS, PREEXISTING, SUBMIT, Entry, account_for_artifact

_MARK = {"met": "✓", "ok": "✓", "unmet": "✗", "fail": "✗", "pending": "…"}


def fmt_clock(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def fmt_stamp(t: float) -> str:
    return time.strftime("%d %b %Y %H:%M", time.localtime(t))


def fmt_span(seconds: float) -> str:
    m = int(max(0.0, seconds) // 60)
    h, m = divmod(m, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m" if m else "under a minute"


def describe(entry: Entry) -> str:
    """One entry as one sentence.

    Everything is past tense and names the student's own elements, because the instructor is
    reading a story about a person, not a log. Anything the chain does not actually know (why a
    console was opened) is simply absent rather than guessed at.
    """
    d = entry.data
    k = entry.kind
    if k == GENESIS:
        which = f' for "{d.get("assignment")}"' if d.get("assignment") else ""
        return f"Started recording{which}."
    if k == PREEXISTING:
        n, l = d.get("devices", 0), d.get("links", 0)
        if not n:
            return "Started from an empty canvas."
        return (f"The canvas already held {_count(n, 'element')} and {_count(l, 'link')} when "
                f"recording started — that work is not in this chain.")
    if k == ev.STOPPED:
        return "Stopped recording here."
    if k == ev.RESUMED:
        away = f" after {fmt_span(d.get('away', 0))}" if d.get("away") else ""
        n = d.get("devices", 0)
        held = (f", with {_count(n, 'element')} on the canvas" if n else ", on an empty canvas")
        return (f"Resumed recording{away}{held}. Anything that appeared while recording was off "
                f"is not accounted for by this chain.")
    if k == ev.LOAD:
        where = d.get("source") or "a file"
        if not d.get("devices"):
            # Switching to a new/empty experiment goes down the same path as an import. Calling
            # that "imported 0 elements" would be alarming nonsense, so say what happened.
            return f'Switched the canvas to "{where}" — an empty board.'
        return (f'IMPORTED a topology from "{where}" — '
                f'{_count(d.get("devices", 0), "element")} and '
                f'{_count(d.get("links", 0), "link")} appeared at once, not built here.')
    if k == ev.PLACE:
        return f"Placed a {d.get('type', 'element')} ({d.get('name', '?')})."
    if k == ev.REMOVE:
        return f"Removed {d.get('name', '?')}."
    if k == ev.CONNECT:
        verb = "Attached" if d.get("edge") == "attach" else "Connected"
        # The reason gets its own sentence rather than a dash: the grammar's reasons are already
        # written as sentences and often contain a dash of their own.
        why = f" {_sentence(d['why'])}" if d.get("why") else ""
        return f"{verb} {d.get('a', '?')} to {d.get('b', '?')}.{why}"
    if k == ev.DISCONNECT:
        return f"Disconnected {d.get('a', '?')} from {d.get('b', '?')}."
    if k == ev.CONFIGURE:
        bits = ", ".join(f"{key} = {val}" for key, val in sorted(d.get("changes", {}).items()))
        return f"Configured {d.get('name', '?')}: {bits}."
    if k == ev.RUN:
        if not d.get("ok", True):
            return f"Tried to start the lab and it failed{_tail(d.get('msg'))}"
        return "Started the lab."
    if k == ev.STOP:
        return "Stopped the lab."
    if k == ev.OPEN_CONSOLE:
        return f"Opened a console on {d.get('name', '?')}."
    if k == ev.COMMAND:
        out = d.get("out") or []
        # The command is the fact; the output is the evidence for it. Indented beneath, so a marker
        # skimming the left column still reads a clean list of what was run.
        head = f"On {d.get('on', '?')}: {d.get('cmd', '')}"
        if not out:
            return head + "   (no output)"
        return head + "".join(f"\n            {line}" for line in out)
    if k == ev.MEASURE:
        got = d.get("summary") or ", ".join(f"{a}={b}" for a, b in
                                            sorted(d.get("measurement", {}).items()))
        return f"Ran {d.get('name', '?')} → {got or 'no reading'}."
    if k == ev.INVOKE:
        return f"Invoked a function{_tail(d.get('result'))}"
    if k == ev.WITNESS:
        mark = _MARK.get(d.get("verdict", ""), "?")
        return f"GINI checked {d.get('probe', '?')} on the running network — {mark} " \
               f"{d.get('verdict', '?')}."
    if k == ev.OBJECTIVE:
        return (f"Objective \"{d.get('say') or d.get('id')}\" went "
                f"{d.get('from', '?')} → {d.get('to', '?')}.")
    if k == ev.ANSWER:
        # The question is quoted with it. A marker reading the transcript top to bottom should not
        # have to hold three prompts in their head to know which one this replies to.
        return f"Q: {d.get('prompt', '?')}\n            A: {d.get('text', '') or '(left blank)'}"
    if k == SUBMIT:
        art = d.get("artifact", {}) or {}
        met = sum(1 for r in d.get("objectives", []) if r.get("status") == "met")
        total = len(d.get("objectives", []) or [])
        scored = f", {met}/{total} objectives met" if total else ""
        return (f"Generated a proof. Handed in {_count(art.get('devices', 0), 'element')} and "
                f"{_count(art.get('links', 0), 'link')}{scored}.")
    return f"{k}."


def _tail(msg) -> str:
    msg = str(msg or "").strip()
    return f": {msg}" if msg else "."


def _sentence(text: str) -> str:
    text = str(text).strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _count(n, noun: str) -> str:
    n = int(n or 0)
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def summarize(entries) -> dict:
    """Counts the closing paragraph is built from. Separated out so a UI can show the numbers
    without re-parsing the prose."""
    entries = list(entries)
    kinds: dict[str, int] = {}
    for e in entries:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    witnesses = [e for e in entries if e.kind == ev.WITNESS]
    return {
        "entries": len(entries),
        "kinds": kinds,
        "construction": sum(kinds.get(k, 0) for k in ev.CONSTRUCTION),
        "operation": sum(kinds.get(k, 0) for k in ev.OPERATION),
        "witnessed": sum(kinds.get(k, 0) for k in ev.WITNESSED),
        "witness_passed": sum(1 for e in witnesses if e.data.get("verdict") == "ok"),
        "witness_total": len(witnesses),
        "imported": kinds.get(ev.LOAD, 0),
        "first_t": entries[0].t if entries else 0.0,
        "last_t": entries[-1].t if entries else 0.0,
    }


def headline(entries) -> str:
    """The one line to read if you read nothing else."""
    s = summarize(entries)
    acc = account_for_artifact(entries)
    if not any(e.kind == SUBMIT for e in entries):
        return "No proof was generated from this chain — the work was never submitted."
    if acc.total and acc.ok and s["witness_total"]:
        return (f"Built here, action by action, and proved live: "
                f"{s['witness_passed']}/{s['witness_total']} checks passed.")
    if acc.total and acc.ok:
        return "Built here, action by action — but nothing was ever checked on a running network."
    if acc.imported:
        return (f"{len(acc.imported)} of the {acc.total} elements handed in came from an "
                f"imported file, not from work recorded here.")
    if acc.suspect:
        return (f"{len(acc.suspect)} of the {acc.total} elements handed in are not accounted "
                f"for by anything in this chain.")
    return "Nothing was handed in."


def narrate(entries, verdict=None) -> str:
    """The full transcript. `verdict` is an optional `proof.Verdict` to print at the top — the
    integrity result belongs above the story, because a broken chain changes how every line under
    it should be read."""
    entries = list(entries)
    if not entries:
        return "This proof is empty — there is nothing to read."

    g = entries[0].data if entries[0].kind == GENESIS else {}
    s = summarize(entries)
    out: list[str] = []

    code = str(g.get("ticket", ""))
    pretty = "-".join(code[i:i + 4] for i in range(0, len(code), 4)) if code else "(none)"
    out.append(f"PROOF OF ACTIVITY — code {pretty}")
    if g.get("assignment"):
        out.append(f"Assignment: {g['assignment']}")
    if g.get("gini_version"):
        out.append(f"Recorded by gBuilder {g['gini_version']}")
    if verdict is not None:
        out.append(f"Integrity: {verdict.label}"
                   + (f" — {verdict.reason}" if verdict.reason else ""))
        for w in getattr(verdict, "warnings", ()) or ():
            out.append(f"  note: {w}")
    # One sitting reads better as "24 Aug 2026 14:02 → 15:11" than as the date twice.
    same_day = time.strftime("%x", time.localtime(s["first_t"])) == \
        time.strftime("%x", time.localtime(s["last_t"]))
    until = fmt_clock(s["last_t"]) if same_day else fmt_stamp(s["last_t"])
    out.append(f"{fmt_stamp(s['first_t'])} → {until} "
               f"({fmt_span(s['last_t'] - s['first_t'])}), {s['entries']} events.")
    out.append("")
    out.append(headline(entries))
    out.append("")

    out.append("WHAT THEY DID")
    for e in entries:
        out.append(f"  {fmt_clock(e.t)}  {describe(e)}")
    out.append("")

    out.append("WHAT THE CHAIN SHOWS")
    k = s["kinds"]
    out.append(f"  Construction — {k.get(ev.PLACE, 0)} placed, {k.get(ev.CONNECT, 0)} connected, "
               f"{k.get(ev.CONFIGURE, 0)} configured, {k.get(ev.REMOVE, 0)} removed.")
    out.append(f"  Operation — {k.get(ev.RUN, 0)} run(s), "
               f"{k.get(ev.OPEN_CONSOLE, 0)} console(s) opened, "
               f"{k.get(ev.COMMAND, 0)} command(s) run, "
               f"{k.get(ev.MEASURE, 0)} measurement(s).")
    if s["witness_total"]:
        out.append(f"  Witnessed by GINI on the running network — "
                   f"{s['witness_passed']}/{s['witness_total']} live checks passed.")
    else:
        out.append("  Witnessed by GINI on the running network — none. Nothing in this chain was "
                   "measured on a live lab.")

    acc = account_for_artifact(entries)
    if acc.total:
        out.append("")
        out.append("WHERE THE SUBMITTED TOPOLOGY CAME FROM")
        out.append(f"  Built under this code: {len(acc.built)} of {acc.total}"
                   + (f" ({', '.join(acc.built)})" if acc.built else ""))
        if acc.imported:
            out.append(f"  Arrived in an import: {len(acc.imported)} "
                       f"({', '.join(acc.imported)}) — these were not built here.")
        if acc.preexisting:
            out.append(f"  Already on the canvas before recording started: "
                       f"{len(acc.preexisting)} ({', '.join(acc.preexisting)}).")
        if acc.unexplained:
            out.append(f"  Unaccounted for: {len(acc.unexplained)} "
                       f"({', '.join(acc.unexplained)}) — the chain never saw these appear.")

    objectives = next((e.data.get("objectives", []) for e in reversed(entries)
                       if e.kind == SUBMIT), [])
    if objectives:
        out.append("")
        out.append("OBJECTIVES AS GINI SCORED THEM")
        for r in objectives:
            mark = _MARK.get(r.get("status", ""), "?")
            out.append(f"  {mark} {r.get('say') or r.get('id')}  [{r.get('status')}]")
    return "\n".join(out)

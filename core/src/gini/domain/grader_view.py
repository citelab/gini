"""Chain → the COOKED view: what a grader needs first, read out of the raw log.

The raw log (`narration.narrate`) is evidence. It is shown in full and never edited: removing or
rewording an entry would make the report disagree with the chain it claims to render, and the whole
point of a proof of activity is that nobody — student or tool — reshapes the record after the fact.
But in full it is busy. In the busiest real chain on the maintainer's machine, 48 of 67 lines were
kernel observations: honest context for an exploration lab, noise for someone marking thirty
submissions. A TA had to read all of it to find the five facts that decide a grade.

So this is a READING of the chain, shown ABOVE the raw log, never instead of it:

  * at a glance — how long, in how many sittings, what GINI measured, how far the wiring got, how
    the builds went, what the prescribed test printed;
  * "look at" — the handful of things in this submission a grader should not miss;
  * the student's answers, beside the key (they stay in the raw log too; no harm).

The invariant is the narration's: every line here can be pointed at specific entries. Nothing is
scored and nothing is inferred beyond what the chain records — a failed metric is "failed", not
"wrong", and whether it costs marks is the grader's call.

Pure: no Qt, no Docker, no server. The Teaching Center calls it per report view, so an old
submission gets the cooked view too — it is computed from the chain, not stored.
"""
from __future__ import annotations

import re

from . import proof_events as ev
from .proof import GENESIS, SUBMIT

#: A gap longer than this between two recorded actions starts a new sitting, when the chain has
#: no explicit stopped/resumed marks to say so. Thirty minutes of nothing recorded is a break,
#: not a student thinking hard at a Load.
SITTING_GAP_S = 30 * 60

_WIRING = re.compile(r"wiring (\d+)/(\d+)")
_PART = re.compile(r"\b([A-Z]) (\d+)/(\d+)")
_PUT_BACK = re.compile(r"^(\S+) put back")


def _d(e) -> dict:
    return (e.get("data") if isinstance(e, dict) else getattr(e, "data", None)) or {}


def _k(e) -> str:
    return (e.get("kind") if isinstance(e, dict) else getattr(e, "kind", "")) or ""


def _t(e) -> float:
    return float((e.get("t") if isinstance(e, dict) else getattr(e, "t", 0.0)) or 0.0)


#: How a staged check's measurement is named (`services.xv6_lab.record_grades`): "<title> · stage A1".
#: A stage that was not run is recorded as pending, with the reason as its summary -- "not run —
#: needs A2" -- and must not be described as a reading that had nothing to compare.
STAGE_PREFIX = "stage "


def _metric_name(name: str) -> str:
    """`"A-Lab 01 — sysinfo · freemem_tracks"` → `"freemem_tracks"`: the lab title is on the page."""
    return name.rsplit(" · ", 1)[-1] if " · " in name else name


def sittings(entries) -> int:
    """How many separate sittings the work took. Explicit stopped→resumed marks win; otherwise a
    long silence between two recorded actions counts as a break."""
    es = list(entries)
    if not es:
        return 0
    if any(_k(e) == ev.RESUMED for e in es):
        return 1 + sum(1 for e in es if _k(e) == ev.RESUMED)
    n = 1
    for a, b in zip(es, es[1:]):
        if _t(b) - _t(a) > SITTING_GAP_S:
            n += 1
    return n


def paused_seconds(entries) -> float:
    """How long recording was paused, in total: the `away` each resume recorded.

    Measured from the chain's own marks, so it is the same number on the server as in gBuilder.
    A chain with no resumes was never paused and reads 0.
    """
    return sum(float(_d(e).get("away") or 0.0) for e in entries if _k(e) == ev.RESUMED)


def _wiring(log: list) -> dict | None:
    """The wiring progress a Load recorded as the first line of its log. Newer gBuilders add the
    per-part counts (`wiring 12/16 · A 6/6 · B 6/6 · C 0/4`); older ones only the total."""
    for line in log or []:
        m = _WIRING.search(str(line))
        if m:
            parts = {p: [int(a), int(b)] for p, a, b in _PART.findall(str(line))}
            return {"passed": int(m.group(1)), "total": int(m.group(2)), "parts": parts}
    return None


def _checklist(entry) -> list | None:
    """The structured checklist a Load recorded — `[{id, part, ok}]` — or None for an entry made
    before builds carried one. See `proof_events.build`."""
    rows = _d(entry).get("checks")
    if not isinstance(rows, list):
        return None
    return [{"id": str(r.get("id", "")), "part": str(r.get("part", "")),
             "passed": bool(r.get("ok"))}
            for r in rows if isinstance(r, dict)]


def _wiring_at(entry) -> dict | None:
    """Wiring progress at one Load: from the structured checklist when the entry has one, else
    from the prose first line of its log. Preferring the data is the point — a reworded log line
    then changes nothing for any submission that carries `checks`."""
    rows = _checklist(entry)
    if rows is None:
        return _wiring(_d(entry).get("log"))
    parts: dict[str, list[int]] = {}
    for r in rows:
        if r["part"]:
            slot = parts.setdefault(r["part"], [0, 0])
            slot[1] += 1
            slot[0] += 1 if r["passed"] else 0
    return {"passed": sum(1 for r in rows if r["passed"]), "total": len(rows), "parts": parts,
            "checks": rows}


#: How many console commands the cooked view lists. The raw log has every one of them.
RECENT_COMMANDS = 8


def cook(entries, title: str = "", answers: list | None = None,
         measurements: list | None = None, sources: list | None = None) -> dict:
    """The cooked view of one submission, as plain data the console renders.

    `answers`, `measurements` and `sources` are the Teaching Center's own rows for the same
    submission (`activities.answered`, `.measurements`, `.check_sources`), passed in rather than
    recomputed, so the cooked view and the sections beside it can never disagree.
    """
    es = list(entries or [])
    genesis = next((_d(e) for e in es if _k(e) == GENESIS), {})
    submitted = next((_t(e) for e in reversed(es) if _k(e) == SUBMIT), 0.0)
    first, last = (_t(es[0]), _t(es[-1])) if es else (0.0, 0.0)
    paused = paused_seconds(es)
    out: dict = {
        "title": title or str(genesis.get("assignment", "")),
        "first_t": first, "last_t": last, "submitted_t": submitted,
        # Time spent RECORDING. A student who paused overnight did not work for fourteen hours,
        # and a grader comparing minutes across a class needs the number that means the same
        # thing for everyone. The pauses are beside it, not hidden.
        "minutes": round(max(0.0, last - first - paused) / 60.0, 1),
        "paused_minutes": round(paused / 60.0, 1),
        "sittings": sittings(es),
        "events": len(es),
        "look_at": [],
    }
    look = out["look_at"]

    # -- what GINI measured: the LAST reading of each metric, i.e. the one at submit --------------
    rows = [r for r in (measurements or []) if r.get("kind") == "measurement"]
    if rows:
        latest: dict[str, dict] = {}
        seen: dict[str, int] = {}
        for r in rows:                         # chain order, so the last write wins
            name = _metric_name(r.get("name", ""))
            latest[name] = r
            seen[name] = seen.get(name, 0) + 1
        passed = [n for n, r in latest.items() if r.get("ok") and not r.get("pending")]
        failed = [{"name": n, "summary": r.get("summary", "")} for n, r in latest.items()
                  if not r.get("ok") and not r.get("pending")]
        pending = [n for n, r in latest.items() if r.get("pending")]
        out["grading"] = {"passed": len(passed), "total": len(latest), "failed": failed,
                          "pending": pending,
                          # How many times the student measured before the final reading. A
                          # student who checked their work is a fact worth seeing, not a score.
                          "rounds": max(seen.values())}
        for f in failed:
            look.append(f"Grading: {f['name']} failed" + (f" — {f['summary']}" if f["summary"]
                                                          else ""))
        for n in pending:
            if n.startswith(STAGE_PREFIX):
                look.append(f"Grading: {n} {latest[n].get('summary') or 'not run'}")
                continue
            look.append(f"Grading: {n} could not be measured (nothing to compare — "
                        f"not a failure)")
        # The staged checks, in the order they ran, as their final verdicts. GINI computes no mark
        # from them: which stages make a pass is the TA's.
        stages = [{"id": n[len(STAGE_PREFIX):],
                   "verdict": ("not run" if r.get("pending") else "ok" if r.get("ok") else "FAIL"),
                   "summary": str(r.get("summary", ""))}
                  for n, r in latest.items() if n.startswith(STAGE_PREFIX)]
        if stages:
            out["stages"] = stages

    # -- builds (OS labs): Loads, failures, the first working kernel, reverts -------------------
    builds = [e for e in es if _k(e) == ev.BUILD]
    loads = [e for e in builds if _d(e).get("action") != "revert"]
    reverts = [e for e in builds if _d(e).get("action") == "revert"]
    if loads:
        ok = [e for e in loads if _d(e).get("ok")]
        last_load = loads[-1]
        first_ok = ok[0] if ok else None
        out["builds"] = {
            "loads": len(loads), "built": len(ok), "failed": len(loads) - len(ok),
            "last_ok": bool(_d(last_load).get("ok")), "last_t": _t(last_load),
            "first_ok_t": _t(first_ok) if first_ok else 0.0,
            "first_ok_minutes_in": (round((_t(first_ok) - first) / 60.0, 1)
                                    if first_ok else None),
        }
        if not ok:
            look.append(f"No Load ever built — {len(loads)} attempt(s), all failed. There is no "
                        f"working kernel in this submission.")
        elif not _d(last_load).get("ok"):
            look.append("The LAST Load failed. The machine kept the previous kernel, so what was "
                        "graded is an earlier build than the code sent.")
        # Wiring at the last Load that recorded it.
        w = next((_wiring_at(e) for e in reversed(loads) if _wiring_at(e)), None)
        if w:
            out["wiring"] = w
            short = [f"Part {p} {a}/{b}" for p, (a, b) in sorted(w["parts"].items()) if a < b]
            if short:
                look.append("Wiring incomplete at the last Load: " + ", ".join(short) + ".")
            elif not w["parts"] and w["passed"] < w["total"]:
                look.append(f"Wiring {w['passed']}/{w['total']} at the last Load.")
        # Which files changed between the first and the last Load. The chain does not carry the
        # image's originals, so this is "changed DURING the work", said as exactly that.
        a, b = _d(loads[0]).get("sources") or {}, _d(loads[-1]).get("sources") or {}
        out["changed_between_loads"] = sorted(
            n for n in set(a) | set(b)
            if (a.get(n) or {}).get("sha256") != (b.get(n) or {}).get("sha256"))
    if reverts:
        rv = []
        for e in reverts:
            name = ""
            for line in _d(e).get("log") or []:
                m = _PUT_BACK.search(str(line))
                if m:
                    name = m.group(1)
            rv.append({"file": name, "t": _t(e)})
        out["reverts"] = rv

    # -- the prescribed test, and what it PRINTED -------------------------------------------------
    tests = [e for e in es if _k(e) == ev.SPAWN and _d(e).get("test")]
    if tests:
        lt = tests[-1]
        printed = list(_d(lt).get("out") or [])
        out["tests"] = {"runs": len(tests), "prog": str(_d(lt).get("what", "")),
                        "last_t": _t(lt), "last_out": printed}
        if not printed:
            look.append(f"The last run of {_d(lt).get('what', 'the test')} printed nothing.")
    elif loads:
        typed = sum(1 for e in es if _k(e) == ev.COMMAND)
        look.append("The prescribed test was never run from the assignment panel"
                    + (f" — {typed} command(s) were typed at the console instead; see them below."
                       if typed else "."))

    # -- the workloads a grading run performed, and what each one PRINTED --------------------------
    # The transcript behind the metrics above. The LAST run of each name, matching the metrics,
    # which are also the last reading of each.
    graded: dict[str, dict] = {}
    for e in es:
        d = _d(e)
        if _k(e) == ev.SPAWN and d.get("run"):
            graded[str(d["run"])] = {"run": str(d["run"]), "what": str(d.get("what", "")),
                                     "out": list(d.get("out") or []), "t": _t(e)}
    if graded:
        out["graded_runs"] = list(graded.values())

    # -- what was typed at a console, and what it printed -----------------------------------------
    # A-Lab 02 and 03 are driven from the xv6 console, so for them this is where the student's own
    # measurements are. The most recent few, newest last; the raw log has every one.
    cmds = [e for e in es if _k(e) == ev.COMMAND]
    if cmds:
        out["commands"] = {
            "count": len(cmds),
            "recent": [{"on": str(_d(e).get("on", "")), "cmd": str(_d(e).get("cmd", "")),
                        "out": list(_d(e).get("out") or []), "t": _t(e)}
                       for e in cmds[-RECENT_COMMANDS:]]}

    # -- network labs: the construction and the live checks ---------------------------------------
    kinds: dict[str, int] = {}
    for e in es:
        kinds[_k(e)] = kinds.get(_k(e), 0) + 1
    if any(kinds.get(k) for k in ev.CONSTRUCTION) and not loads:
        witnesses = [e for e in es if _k(e) == ev.WITNESS]
        out["network"] = {
            "placed": kinds.get(ev.PLACE, 0), "connected": kinds.get(ev.CONNECT, 0),
            "runs": kinds.get(ev.RUN, 0), "commands": kinds.get(ev.COMMAND, 0),
            "checks_passed": sum(1 for e in witnesses if _d(e).get("verdict") == "ok"),
            "checks": len(witnesses)}

    # -- the code as sent, against the code as last built ------------------------------------------
    for s in sources or []:
        name = str(s.get("path", "")).rsplit("/", 1)[-1]
        if s.get("never_built"):
            look.append(f"{name} was never part of a successful Load.")
        elif not s.get("matches"):
            look.append(f"{name} changed after the last successful Load — the file sent is not "
                        f"the one that was built.")

    # -- answers: in the cooked view, AND left in the raw log --------------------------------------
    if answers:
        out["answers"] = answers
        blank = [i + 1 for i, a in enumerate(answers) if not a.get("answered")]
        if blank:
            look.append("Unanswered: question " + ", ".join(map(str, blank)) + ".")

    out["table"] = table(out, measurements)
    return out


def preview(entries, questions: list | None = None, title: str = "") -> dict:
    """The cooked view of a chain that has NOT been handed in yet — what the student sees before
    they confirm, so "I did the work, honest" becomes something they can check and fix first.

    `cook()` itself, with its inputs read from the chain the way the Teaching Center reads them:
    the measurement rows from `measure` entries (`activities.measurements`, the kinds `cook` reads)
    and the answers from `answer` entries paired with the questions the code was given
    (`activities.answered`, without the key — a student's machine never has it). Those two are a
    few lines each and are restated rather than moved here, so the server keeps owning its report;
    a test pins that both sides produce the same counts.
    """
    es = list(entries or [])
    measured = []
    for e in es:
        if _k(e) == ev.MEASURE:
            d = _d(e)
            measured.append({"kind": "measurement", "name": str(d.get("name", "")),
                             "ok": bool(d.get("ok")), "pending": bool(d.get("pending")),
                             "summary": str(d.get("summary", ""))})
    said: dict[str, list[dict]] = {}
    for e in es:
        if _k(e) == ev.ANSWER:
            said.setdefault(str(_d(e).get("id", "")), []).append(_d(e))
    answers = []
    for q in questions or []:
        qid = str(q.get("id", "") if isinstance(q, dict) else getattr(q, "id", ""))
        turns = said.get(qid, [])
        answers.append({"id": qid, "answered": bool(turns),
                        "given": (turns[-1].get("text", "") if turns else "")})
    return cook(es, title, answers or None, measured)


def table(cooked: dict, measurements: list | None = None) -> dict:
    """The same facts as ROWS, for marking a class rather than reading one submission.

    One dict per check (`id`, `part`, `passed`), per metric (`id`, `ok`, `pending`, `summary`) and
    per question (`id`, `answered`, `chars`). The class view and the CSV export both read this, so
    there is one implementation of "what this submission shows" and not three.

    Nothing is scored. A row is what the chain records; a column of them is still not a mark.
    """
    w = cooked.get("wiring") or {}
    checks = list(w.get("checks") or [])
    metrics: dict[str, dict] = {}
    for r in measurements or []:
        if r.get("kind") != "measurement":
            continue
        name = _metric_name(r.get("name", ""))
        metrics[name] = {"id": name, "ok": bool(r.get("ok")) and not r.get("pending"),
                         "pending": bool(r.get("pending")), "summary": str(r.get("summary", ""))}
    questions = [{"id": str(a.get("id", "")), "answered": bool(a.get("answered")),
                  "chars": len(str(a.get("given") or ""))}
                 for a in cooked.get("answers") or []]
    return {"checks": checks,
            # Per part as well, for a submission whose Loads predate the structured checklist:
            # the counts are all an older chain can say.
            "parts": {p: list(v) for p, v in sorted((w.get("parts") or {}).items())},
            "metrics": list(metrics.values()), "questions": questions}

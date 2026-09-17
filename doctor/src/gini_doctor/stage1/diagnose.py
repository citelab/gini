"""Local diagnosis: match one report against the remedy table and say what to do.

The table is ``remedies.json``, ``docs/LAB_DIAGNOSIS.md`` as data, so it can grow without code and
later be served by the Health Center. A finding is always a proposal: the exact command, why, what
else it does, and which facts to re-check. The doctor never runs any of it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, NamedTuple, Optional

from .compare import render
from .report import OK, Report

TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remedies.json")


class Finding(NamedTuple):
    id: str
    title: str
    reason: str
    who: str
    command: Optional[str]
    side_effects: str
    verify: List[str]
    evidence: List[str]


def load_table(path: str = TABLE) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _matches(report: Report, cond: Dict[str, Any]) -> bool:
    fact = report.facts.get(cond["fact"])
    if "status" in cond:
        return fact is not None and fact.get("status") == cond["status"]
    if fact is None or fact.get("status") != OK:
        return False
    value = fact.get("value")
    if "equals" in cond:
        # JSON has one number type and booleans are not numbers here: False must not equal 0.
        want = cond["equals"]
        if isinstance(want, bool) or isinstance(value, bool):
            return value is want
        return value == want
    if "not_equals" in cond:
        return value != cond["not_equals"]
    if "in" in cond:
        return value in cond["in"]
    text = render(fact)
    if "contains" in cond:
        return cond["contains"] in text
    if "not_contains" in cond:
        return cond["not_contains"] not in text
    if "gt" in cond:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > cond["gt"]
    return False


def _fill(template: str, report: Report) -> str:
    return re.sub(r"\{([a-zA-Z0-9_.\-]+)\}", lambda m: render(report.facts.get(m.group(1))), template)


def diagnose(report: Report, table: Optional[Dict[str, Any]] = None) -> List[Finding]:
    table = table if table is not None else load_table()
    found = []
    for rule in table.get("rules", []):
        conds = rule.get("when") or []
        if not conds or not all(_matches(report, c) for c in conds):
            continue
        command = rule.get("command")
        if isinstance(command, dict):
            command = command.get(report.platform)
            if command is None:
                continue          # the rule has no fix on this platform, so it does not apply here
        evidence = []
        for key in [c["fact"] for c in conds] + list(rule.get("verify") or []):
            line = "%s = %s" % (key, render(report.facts.get(key)))
            if line not in evidence:
                evidence.append(line)
        found.append(Finding(rule["id"], _fill(rule["title"], report), _fill(rule.get("reason", ""), report),
                             rule.get("who", "you"), command, rule.get("side_effects", ""),
                             list(rule.get("verify") or []), evidence))
    return found


def format_text(findings: List[Finding]) -> str:
    if not findings:
        return "\n  Nothing in the remedy table matches this report.\n"
    out = ["", "What this report points to (the doctor runs none of these; you decide):", ""]
    for i, f in enumerate(findings, 1):
        who = "you can do this" if f.who == "you" else "needs an administrator"
        out.append("  %d. %s   [%s]" % (i, f.title, who))
        out.append("     why:      %s" % f.reason)
        for line in f.evidence:
            out.append("     evidence: %s" % line)
        if f.command:
            out.append("     run:      %s" % f.command)
            if f.side_effects:
                out.append("     also:     %s" % f.side_effects)
            groups = sorted({k.split(".", 1)[0] for k in f.verify})
            out.append("     then:     gini-doctor run --only %s   (re-checks %s)"
                       % (",".join(groups), ", ".join(f.verify)))
        else:
            out.append("     nothing to run; this is something to know.")
        out.append("")
    return "\n".join(out)

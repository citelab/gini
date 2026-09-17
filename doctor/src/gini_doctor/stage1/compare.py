"""compare — the product. N reports in, only the facts on which they disagree out.

The first report is the reference ("the machine that works"); the output reads as "what the
others have instead".

Facts that are ``n/a`` on some machines and present on others are shown separately, under
*platform differences*: a Mac has no subuid range and never will, and mixing that into the real
differences would bury the one line that matters under a list of things that were never supposed
to match.
"""
from __future__ import annotations

from typing import List, NamedTuple, Sequence

from .report import NA, OK, Report

# Differ on every machine for reasons nobody is diagnosing. --all keeps them.
NOISY = frozenset({
    "system.disk.home.free_gb",
    "system.doctor.python.executable",
    "engine.podman.images.count",
})


class Row(NamedTuple):
    key: str
    cells: List[str]


class Comparison(NamedTuple):
    names: List[str]
    differ: List[Row]
    platform: List[Row]
    total: int
    policy_mismatch: bool


def render(fact) -> str:
    if fact is None:
        return "(not collected)"
    status = fact.get("status")
    if status == OK:
        v = fact.get("value")
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v)
    detail = fact.get("detail")
    return "[%s] %s" % (status, detail) if detail else "[%s]" % status


def _names(reports: Sequence[Report]) -> List[str]:
    names, seen = [], {}
    for r in reports:
        n = r.host
        seen[n] = seen.get(n, 0) + 1
        names.append(n if seen[n] == 1 else "%s#%d" % (n, seen[n]))
    return names


def compare(reports: Sequence[Report], include_noisy: bool = False) -> Comparison:
    if len(reports) < 2:
        raise ValueError("need at least two reports to compare")
    keys = []
    seen = set()
    for r in reports:
        for k in sorted(r.facts):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    differ, plat = [], []
    for k in keys:
        if not include_noisy and k in NOISY:
            continue
        facts = [r.facts.get(k) for r in reports]
        cells = [render(f) for f in facts]
        if len(set(cells)) == 1:
            continue
        row = Row(k, cells)
        statuses = {(f or {}).get("status") for f in facts}
        if NA in statuses:
            plat.append(row)
        else:
            differ.append(row)
    versions = {(r.policy.get("source"), r.policy.get("version")) for r in reports}
    return Comparison(_names(reports), differ, plat, len(keys), len({v for _, v in versions}) > 1)


def format_text(c: Comparison, width: int = 110) -> str:
    lines = ["", "%d reports: %s (first is the reference)" % (len(c.names), ", ".join(c.names))]
    if c.policy_mismatch:
        lines.append("note: these reports were collected under different doctor policies")
    lines.append("")
    col = max(len(n) for n in c.names) + 2

    def block(rows):
        for row in rows:
            lines.append(row.key)
            for name, cell in zip(c.names, row.cells):
                lines.append("    %s%s" % (name.ljust(col), cell[:width]))

    if c.differ:
        block(c.differ)
    else:
        lines.append("  No differences on any fact both machines can have.")
    if c.platform:
        lines.append("")
        lines.append("platform differences (facts that do not exist on every platform compared):")
        block(c.platform)
    lines.append("")
    lines.append("%d fact(s) differ, %d differ by platform, out of %d collected."
                 % (len(c.differ), len(c.platform), c.total))
    lines.append("")
    return "\n".join(lines)

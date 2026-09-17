"""The Stage 1 command line.

    python -m gini_doctor.stage1                     # run the default groups, save a report
    python -m gini_doctor.stage1 run --only system   # just those groups
    python -m gini_doctor.stage1 run --stdout        # the JSON report to stdout, nothing saved
    python -m gini_doctor.stage1 compare a.json b.json [--all]
    python -m gini_doctor.stage1 show report.json
    python -m gini_doctor.stage1 groups

Offline is the normal case: nothing here needs the Health Center. When ``GINI_HEALTHCENTER`` (or
``--healthcenter``) is set, the doctor fetches its policy from there and falls back to the cached
or built-in policy if it cannot. It never uploads anything on its own.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import ENGINE_VERSION, platforms, policy as _policy, probes
from .compare import compare, format_text, render
from .redact import Redactor
from .report import Report

STAGE0_ENV = "GINI_DOCTOR_STAGE0"
MARK = {"ok": " ", "absent": "-", "error": "!", "n/a": "~"}


def ingest_stage0(report: Report, text: Optional[str], redactor: Redactor) -> None:
    """Stage 0 passes what it found about the machine's interpreters as ``key<TAB>value`` lines.
    They are facts about this machine like any other — the interpreter search IS a probe."""
    if not text:
        return
    for line in text.splitlines():
        if "\t" not in line:
            continue
        k, v = line.split("\t", 1)
        k = k.strip()
        if not k or not all(ch.isalnum() or ch in "._-" for ch in k):
            continue
        report.set("stage0." + k, "ok", redactor.text(v.strip()))
    if "stage0" not in report.groups:
        report.groups.insert(0, "stage0")


def collect(groups: List[str], pol: "_policy.Policy", platform: Optional[str] = None,
            redactor: Optional[Redactor] = None, stage0: Optional[str] = None) -> Report:
    plat = platform or platforms.current()
    red = redactor or Redactor()
    report = Report(plat, pol.source, pol.version)
    ingest_stage0(report, stage0, red)
    if pol.note:
        report.set("doctor.policy.note", "ok", red.text(pol.note))
    wanted = list(groups)
    # system always runs: a report that cannot be identified cannot be compared.
    if "system" not in wanted:
        wanted.insert(0, "system")
    timeout = float(pol.get("probe_timeout_s", 20))
    for g in wanted:
        if not probes.run_group(g, report, plat, red, timeout=timeout):
            report.set("doctor.unknown_group.%s" % g, "error", detail="no such probe group")
    return report


def summary(report: Report) -> str:
    out = ["", "gini-doctor %s  host=%s  platform=%s  policy=%s/%s"
           % (report.doctor.get("version"), report.host, report.platform,
              report.policy.get("source"), report.policy.get("version")), ""]
    current = None
    for key in sorted(report.facts):
        group = key.split(".", 1)[0]
        if group != current:
            current = group
            out.append("[%s]" % group)
        f = report.facts[key]
        out.append("  %s %-38s %s" % (MARK.get(f.get("status"), "?"), key.split(".", 1)[1]
                                      if "." in key else key, render(f)))
    out.append("")
    out.append("  legend:  '-' absent   '!' error (the machine's own words shown)   '~' not applicable here")
    return "\n".join(out)


def _cmd_run(args) -> int:
    pol = _policy.load(healthcenter=args.healthcenter, offline=args.offline)
    groups = [g for g in (args.only.split(",") if args.only else pol.get("groups_default")) if g]
    report = collect(groups, pol, stage0=os.environ.get(STAGE0_ENV))
    if args.stdout:
        sys.stdout.write(report.to_json())
        return 0
    path = args.out or report.default_filename()
    report.save(path)
    if not args.quiet:
        print(summary(report))
        print("\n  report saved: %s" % path)
        print("  compare with: gini-doctor compare <working-machine>.json %s\n" % path)
    return 0


def _cmd_compare(args) -> int:
    try:
        reports = [Report.load(p) for p in args.reports]
        print(format_text(compare(reports, include_noisy=args.all)))
    except (OSError, ValueError) as e:
        print("gini-doctor: %s" % e, file=sys.stderr)
        return 2
    return 0


def _cmd_show(args) -> int:
    try:
        print(summary(Report.load(args.report)))
    except (OSError, ValueError) as e:
        print("gini-doctor: %s" % e, file=sys.stderr)
        return 2
    return 0


def _cmd_groups(args) -> int:
    for g in probes.groups():
        print("  %-10s %s" % (g, probes.describe(g)))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gini-doctor", description="Is this machine able to run GINI, "
                                "and how does it differ from one that can?")
    p.add_argument("--version", action="version", version="gini-doctor stage1 %s" % ENGINE_VERSION)
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="probe this machine and save a report (the default)")
    r.add_argument("--only", help="comma-separated probe groups")
    r.add_argument("--out", help="where to save the report")
    r.add_argument("--stdout", action="store_true", help="print the JSON report instead of saving")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--healthcenter", help="Health Center URL (default: $GINI_HEALTHCENTER)")
    r.add_argument("--offline", action="store_true", help="do not contact the Health Center")
    r.set_defaults(func=_cmd_run)

    c = sub.add_parser("compare", help="show only the facts on which reports differ")
    c.add_argument("reports", nargs="+")
    c.add_argument("--all", action="store_true", help="include facts that always differ")
    c.set_defaults(func=_cmd_compare)

    s = sub.add_parser("show", help="print a saved report in human form")
    s.add_argument("report")
    s.set_defaults(func=_cmd_show)

    g = sub.add_parser("groups", help="list the probe groups")
    g.set_defaults(func=_cmd_groups)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv.insert(0, "run")
    args = parser().parse_args(argv)
    if args.command == "compare" and len(args.reports) < 2:
        print("gini-doctor: compare needs at least two reports", file=sys.stderr)
        return 2
    return args.func(args)

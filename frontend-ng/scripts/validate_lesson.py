#!/usr/bin/env python3
"""Validate a GINI Lesson pack before releasing it — the authoring safety net.

Checks that the lesson parses, its objectives' structural predicates are well-formed and name
real GINI elements, behavioral probes parse, and enums (help/persona/complete_when) are valid.
A professor should never hand a student a lesson that fails here.

    python scripts/validate_lesson.py path/to/lesson.yaml [more.yaml ...]

Exit code 0 = all valid, 1 = problems found. (Playtesting a lesson = opening it as a Mission in
GINI and playing it; this covers the static checks.)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gini.domain import lesson as L        # noqa: E402
from gini.domain import probes as P        # noqa: E402


def _check(path: Path) -> list[str]:
    try:
        les = L.from_yaml(path.read_text())
    except Exception as e:                  # noqa: BLE001
        return [f"parse error: {e}"]
    problems = list(L.validate(les))
    for o in les.objectives:               # behavioral probes must parse too
        if o.kind == "behavioral" and o.probe and not P.probe_ok(o.probe):
            problems.append(f"objective {o.id!r}: probe does not parse: {o.probe!r}")
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    ok = True
    for arg in argv:
        path = Path(arg)
        problems = _check(path)
        if problems:
            ok = False
            print(f"✗ {path.name}")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"✓ {path.name} — valid")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

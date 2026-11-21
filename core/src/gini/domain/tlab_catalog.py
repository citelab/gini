"""Every T-Lab shipped with gini-core, each loaded by its own engine.

T-Lab 02 was written "as if three more are coming" — later T-Labs were to be new level files, not
new code. That held for as long as the next lab was about schedules. T-Lab 03 (deadlock handling)
and T-Lab 04 (a file system) are not: their moves are requests and block reads, not lines of a
thread, so each has an engine of its own (`banklab`, `fslab`) beside `schedlab`. What they share
is the frame — levels, starts, goals, a Try every — and this module, which is the one place that
knows which engine reads which level file.

A level file names its engine at the top (`engine: bank`); one that does not is a scheduler lab,
which is every file written before the key existed. An engine module provides
`from_dict(d) -> LabFile`, where the lab file has at least `id`, `title`, `summary`, `hue`,
`released`, `levels` and `engine` — what the hub needs to draw a tile.

Like `schedlab.catalog`, a file that does not load RAISES rather than being skipped: a lab that
silently vanished from the hub is the failure the loaders' one-sentence refusals exist to prevent,
and the test suite loads every shipped file.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import yaml

TLABS_DIR = Path(__file__).parent / "tlabs"

#: `engine:` value → module in `gini.domain`.
ENGINES = {"sched": "schedlab", "bank": "banklab", "fs": "fslab"}
DEFAULT = "sched"


class CatalogError(ValueError):
    """A level file names an engine this gini-core does not have."""


def engine_of(d: dict) -> str:
    return str((d or {}).get("engine") or DEFAULT)


def module(engine: str):
    name = ENGINES.get(engine)
    if name is None:
        raise CatalogError(f"no T-Lab engine called {engine!r} (known: "
                           f"{', '.join(sorted(ENGINES))})")
    return importlib.import_module(f"gini.domain.{name}")


def from_dict(d: dict):
    return module(engine_of(d)).from_dict(d or {})


def load(path):
    return from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def files(engine: str | None = None) -> tuple:
    """The shipped level files in filename order, optionally only those of one engine."""
    if not TLABS_DIR.is_dir():
        return ()
    out = []
    for p in sorted(TLABS_DIR.glob("*.yaml")):
        if engine is not None:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if engine_of(d) != engine:
                continue
        out.append(p)
    return tuple(out)


def catalog() -> tuple:
    """Every T-Lab, in filename order (`t-lab-02`, `t-lab-03`, …), each from its own engine."""
    return tuple(load(p) for p in files())


def get(lab_id: str):
    return next((lab for lab in catalog() if lab.id == lab_id), None)

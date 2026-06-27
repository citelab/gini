"""Project persistence — save/load a topology as a JSON project.

Uses the domain model's own to_dict/from_dict, so the on-disk format is just the
topology (devices + links + positions + properties) wrapped with a version. A legacy
.gsav reader can be added later; this is the clean native format.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.topology import Topology

PROJECT_EXT = ".gini"
FORMAT = "gini-project"
VERSION = 1


def save_project(topo: Topology, path: str | Path) -> None:
    data = {"format": FORMAT, "version": VERSION, "topology": topo.to_dict()}
    Path(path).write_text(json.dumps(data, indent=2))


def load_project(path: str | Path) -> Topology:
    data = json.loads(Path(path).read_text())
    if data.get("format") != FORMAT:
        raise ValueError(f"not a GINI project file: {path}")
    return Topology.from_dict(data["topology"])

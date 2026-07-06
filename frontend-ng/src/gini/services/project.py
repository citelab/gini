"""Project = a folder on disk.

A gBuilder project bundles one experiment together: its topology, the Ask GINI
conversation (so switching projects switches the AI's memory), and a short
teacher's *brief* that frames the lab for the assistant. Layout:

    MyExperiment/
      topology.gini     the topology (native .gini JSON — see persistence.py)
      project.json      {name, brief, created, modified}
      ai.json           {messages, history}  (the Ask GINI transcript + model history)

Keeping these as separate files means the topology stays clean and shareable on
its own, while the AI state and framing travel alongside it. The single-file
`.gini` format still opens (as a topology with a blank conversation) for
backward compatibility.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..domain.topology import Topology
from .persistence import load_project as _load_topology
from .persistence import save_project as _save_topology

TOPOLOGY_FILE = "topology.gini"
META_FILE = "project.json"
AI_FILE = "ai.json"
META_FORMAT = "gini-project-dir"
META_VERSION = 1


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:                       # missing or malformed -> empty
        return {}


def is_project_dir(path) -> bool:
    p = Path(path)
    return p.is_dir() and (p / TOPOLOGY_FILE).exists()


def save_project_dir(path, topology: Topology, *, name: str | None = None,
                     brief: str = "", ai_state: dict | None = None) -> str:
    """Write (or overwrite) the project folder. Returns the folder path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    _save_topology(topology, p / TOPOLOGY_FILE)
    prev = _read_json(p / META_FILE)
    now = time.time()
    meta = {
        "format": META_FORMAT, "version": META_VERSION,
        "name": name or topology.name or p.name,
        "brief": brief or "",
        "created": prev.get("created", now),
        "modified": now,
    }
    (p / META_FILE).write_text(json.dumps(meta, indent=2))
    (p / AI_FILE).write_text(json.dumps(ai_state or {}, indent=2))
    return str(p)


def load_project_dir(path) -> dict:
    """Return {topology, name, brief, ai_state, path} for a project folder."""
    p = Path(path)
    topo = _load_topology(p / TOPOLOGY_FILE)
    meta = _read_json(p / META_FILE)
    return {
        "topology": topo,
        "name": meta.get("name") or topo.name or p.name,
        "brief": meta.get("brief", ""),
        "ai_state": _read_json(p / AI_FILE),
        "path": str(p),
    }


def list_projects(projects_root) -> list[dict]:
    """All project folders under `projects_root`, most-recently-modified first."""
    root = Path(projects_root)
    out: list[dict] = []
    if root.is_dir():
        for d in root.iterdir():
            if is_project_dir(d):
                meta = _read_json(d / META_FILE)
                out.append({"name": meta.get("name", d.name), "path": str(d),
                            "modified": meta.get("modified", 0)})
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out

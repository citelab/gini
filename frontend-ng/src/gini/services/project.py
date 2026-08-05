"""Project = a folder holding a FAMILY of related experiments.

A project is the unit of *context*: several experiments that belong together (build a LAN, route
between two LANs, convert it to SDN) share one teacher's **brief** and one **Ask GINI conversation**,
so the tutor remembers the whole arc instead of forgetting everything each time you move on. An
*experiment* is one topology inside that project. Layout:

    MyProject/
      project.json          {name, brief, created, modified, current}
      ai.json               {messages, history}   ← the shared Ask GINI transcript
      experiments/
        Basic LAN.gini      a topology (native .gini JSON — see persistence.py)
        Routed LAN.gini
        SDN.gini

Two older shapes still open, and are migrated in place on first load:
  * the single-experiment project folder (`topology.gini` at the root) — moved into
    `experiments/` under the project's own name;
  * a bare single-file `.gini` — still loads as a topology via persistence.py.

Keeping experiments as separate files means each topology stays clean and shareable on its own,
while the brief and the AI conversation live once, at the project level.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..domain.topology import Topology
from .persistence import load_project as _load_topology
from .persistence import save_project as _save_topology

TOPOLOGY_FILE = "topology.gini"          # legacy single-experiment layout
META_FILE = "project.json"
AI_FILE = "ai.json"
EXPERIMENTS_DIR = "experiments"
EXT = ".gini"
META_FORMAT = "gini-project-dir"
META_VERSION = 2                         # v1 = one topology.gini at the root
# A project's first experiment gets a neutral, numbered name — NEVER the project's own. Naming it
# after the project made the experiment list read as though the project were an item inside itself.
# The -01 suffix matches the Project-01 house style and invites Experiment-02, -03 alongside it.
FIRST_EXPERIMENT = "Experiment-01"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:                       # missing or malformed -> empty
        return {}


def safe_name(name: str) -> str:
    """An experiment name that is safe as a filename but still human-readable on disk."""
    s = re.sub(r'[/\\:*?"<>|]+', "-", (name or "").strip()).strip(". ")
    return s or "Untitled"


def is_project_dir(path) -> bool:
    """True for a v2 project (experiments/) or a v1 one (topology.gini at the root)."""
    p = Path(path)
    if not p.is_dir():
        return False
    return (p / TOPOLOGY_FILE).exists() or (p / EXPERIMENTS_DIR).is_dir()


def experiments_dir(path) -> Path:
    return Path(path) / EXPERIMENTS_DIR


# -- experiments ------------------------------------------------------------- #
def list_experiments(path) -> list[dict]:
    """Every experiment in the project, most-recently-modified first."""
    d = experiments_dir(path)
    out: list[dict] = []
    if d.is_dir():
        for f in d.glob(f"*{EXT}"):
            out.append({"name": f.stem, "path": str(f), "modified": f.stat().st_mtime})
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out


def experiment_path(path, name: str) -> Path:
    return experiments_dir(path) / f"{safe_name(name)}{EXT}"


def save_experiment(path, topology: Topology, name: str) -> str:
    """Write one experiment's topology into the project. Returns the file path."""
    d = experiments_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    fp = experiment_path(path, name)
    _save_topology(topology, fp)
    return str(fp)


def load_experiment(path, name: str) -> Topology:
    return _load_topology(experiment_path(path, name))


def delete_experiment(path, name: str) -> bool:
    fp = experiment_path(path, name)
    if not fp.exists():
        return False
    fp.unlink()
    meta = _read_json(Path(path) / META_FILE)
    if meta.get("current") == safe_name(name):       # don't point at a file that's gone
        meta["current"] = ""
        (Path(path) / META_FILE).write_text(json.dumps(meta, indent=2))
    return True


def rename_experiment(path, old: str, new: str) -> bool:
    src, dst = experiment_path(path, old), experiment_path(path, new)
    if not src.exists() or dst.exists():
        return False
    src.rename(dst)
    meta = _read_json(Path(path) / META_FILE)
    if meta.get("current") == safe_name(old):
        meta["current"] = safe_name(new)
        (Path(path) / META_FILE).write_text(json.dumps(meta, indent=2))
    return True


# -- migration --------------------------------------------------------------- #
def migrate_project(path) -> bool:
    """v1 → v2 in place: move a root `topology.gini` into `experiments/<project name>.gini`.
    Returns True if anything moved. Safe to call on an already-migrated project."""
    p = Path(path)
    legacy = p / TOPOLOGY_FILE
    if not legacy.exists():
        return False
    meta = _read_json(p / META_FILE)
    name = FIRST_EXPERIMENT
    d = experiments_dir(p)
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{name}{EXT}"
    if not target.exists():
        legacy.rename(target)
    else:
        legacy.unlink()                              # already migrated; drop the stale copy
    meta.update({"format": META_FORMAT, "version": META_VERSION, "current": name,
                 "name": meta.get("name") or p.name})
    (p / META_FILE).write_text(json.dumps(meta, indent=2))
    return True


# -- project ----------------------------------------------------------------- #
def save_project_dir(path, topology: Topology, *, name: str | None = None, brief: str = "",
                     ai_state: dict | None = None, experiment: str | None = None) -> str:
    """Write the project: the CURRENT experiment's topology plus the project-level brief and AI
    conversation. `experiment` names the topology being saved (defaults to the project name, which
    is what a freshly-created project uses). Returns the folder path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    prev = _read_json(p / META_FILE)
    pname = name or topology.name or p.name
    exp = safe_name(experiment or prev.get("current") or FIRST_EXPERIMENT)
    save_experiment(p, topology, exp)
    now = time.time()
    meta = {
        "format": META_FORMAT, "version": META_VERSION,
        "name": pname,
        "brief": brief or "",
        "current": exp,
        "created": prev.get("created", now),
        "modified": now,
    }
    (p / META_FILE).write_text(json.dumps(meta, indent=2))
    (p / AI_FILE).write_text(json.dumps(ai_state or {}, indent=2))
    return str(p)


def load_project_dir(path) -> dict:
    """Return {topology, name, brief, ai_state, path, experiment, experiments} for a project.

    `topology` is the *current* experiment. A v1 project is migrated first, so old projects open
    unchanged from the user's point of view."""
    p = Path(path)
    migrate_project(p)
    meta = _read_json(p / META_FILE)
    pname = meta.get("name") or p.name
    exps = list_experiments(p)
    cur = meta.get("current") or ""
    known = {e["name"] for e in exps}
    if cur not in known:                             # missing/renamed -> newest, else a fresh one
        cur = exps[0]["name"] if exps else FIRST_EXPERIMENT
    try:
        topo = load_experiment(p, cur)
    except Exception:                                # noqa: BLE001 — an empty/new project
        topo = Topology(cur)
    return {
        "topology": topo,
        "name": pname,
        "brief": meta.get("brief", ""),
        "ai_state": _read_json(p / AI_FILE),
        "path": str(p),
        "experiment": cur,
        "experiments": [e["name"] for e in exps] or [cur],
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
                            "modified": meta.get("modified", 0),
                            "experiments": len(list_experiments(d))})
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out

"""GINI's home on disk — ``~/.gini`` — for settings and the user's saved projects.

Everything user-level lives under one directory so save/restore is predictable:

    ~/.gini/
      config.json        persisted settings (theme, LLM, …)
      projects/          default location for saved .gini topologies

Override the location with the ``GINI_HOME_DIR`` environment variable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def gini_home() -> Path:
    return Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()


def projects_dir() -> Path:
    return gini_home() / "projects"


def config_path() -> Path:
    return gini_home() / "config.json"


def ensure_dirs() -> None:
    projects_dir().mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except Exception:                       # missing or malformed -> defaults
        return {}


def save_config(data: dict) -> None:
    ensure_dirs()
    try:
        config_path().write_text(json.dumps(data, indent=2))
    except Exception:
        pass


# -- recent projects (last-opened + MRU list), kept out of config.json so a settings
#    write never clobbers them --------------------------------------------------- #
def recents_path() -> Path:
    return gini_home() / "recents.json"


def load_recents() -> dict:
    try:
        data = json.loads(recents_path().read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_recents(data: dict) -> None:
    ensure_dirs()
    try:
        recents_path().write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def remember_project(path: str, *, limit: int = 8) -> None:
    """Record `path` as the last-opened project and push it to the front of the MRU list."""
    data = load_recents()
    mru = [p for p in data.get("list", []) if p != path]
    mru.insert(0, path)
    save_recents({"last": path, "list": mru[:limit]})


# settings fields that are persisted to / loaded from config.json
PERSISTED_KEYS = (
    "theme", "reduced_motion",
    "llm_enabled", "llm_url", "llm_model", "llm_think",
    "auto_internet", "name_prefixes", "prices",
    "backend", "gini_server_host", "gini_server_port", "gini_server_user",
    "show_help_on_launch",
    "tc_url", "tc_course", "tc_student", "tc_token",   # Teaching Center enrolment
)

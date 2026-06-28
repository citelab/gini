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


# settings fields that are persisted to / loaded from config.json
PERSISTED_KEYS = (
    "theme", "reduced_motion",
    "llm_enabled", "llm_url", "llm_model", "llm_think",
    "auto_internet", "name_prefixes", "prices",
    "backend", "gini_server_host", "gini_server_port", "gini_server_user",
)

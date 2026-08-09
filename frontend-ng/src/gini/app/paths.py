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


def captures_dir() -> Path:
    """Host directory for packet captures. Bind-mounted into each gRouter container at
    ``/captures``, so a ``tap`` inline VNF's ``.pcap`` lands here on the host machine and
    opens directly in Wireshark. Survives topology teardown (the compose workdir does not)."""
    return gini_home() / "captures"


def oszoo_cache_dir() -> Path:
    """Host directory for OS Zoo guest images. Bind-mounted into each OS Zoo container at
    ``/zoo/cache``, so a historical OS's ISO/disk downloads once and is reused across runs
    (an anonymous volume would be discarded on recreate, forcing a re-download every Run).
    Survives topology teardown."""
    return gini_home() / "oszoo-cache"


def scripts_dir() -> Path:
    """Host directory for student-written router modules. Bind-mounted (read-only) into each
    gRouter container at ``/scripts``, so a Lua data-plane VNF edited on the host machine loads
    with ``gpipe add lua /scripts/<name>.lua`` from the router console (Chapter on designing and
    implementing protocols). Survives topology teardown."""
    return gini_home() / "scripts"


def config_path() -> Path:
    return gini_home() / "config.json"


def ensure_dirs() -> None:
    projects_dir().mkdir(parents=True, exist_ok=True)
    captures_dir().mkdir(parents=True, exist_ok=True)
    scripts_dir().mkdir(parents=True, exist_ok=True)


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
    "theme", "reduced_motion", "text_size",
    "llm_enabled", "llm_url", "llm_model", "llm_think",
    "auto_internet", "name_prefixes", "prices", "autobuild_images",
    "connector_style",                  # bent ↔ straight, toggled from the toolbar
    "backend", "gini_server_host", "gini_server_port", "gini_server_user",
    "show_help_on_launch",
    "tc_url", "tc_course", "tc_student", "tc_token",   # Teaching Center enrolment
    "tc_allow_insecure",                              # the conscious plaintext-password override
    # GINI32 hardware. `laptop_id` must be stable or every claimed board orphans at
    # once; `claimed_boards` is a property of this laptop, never of a topology, so a
    # colleague's .gini file cannot hand you their hardware.
    "laptop_id", "claimed_boards",
    # The lab Wi-Fi written to a board over USB. Remembered so setting up the second
    # and subsequent boards is one click — it is the same network for a whole class.
    "board_wifi_ssid", "board_wifi_password", "known_boards",
)

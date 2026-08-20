"""The setup marker — ~/.gini/setup.json — records that the runtime + images were brought in, so
`gbuilder` can tell whether live Run is ready without re-probing everything on launch."""
from __future__ import annotations

import json
import os
from pathlib import Path


def gini_home() -> Path:
    """Same location + same override as app.paths.gini_home (GINI_HOME_DIR), so the marker
    always lands where gbuilder looks. GINI_HOME kept as a legacy fallback."""
    return Path(os.environ.get("GINI_HOME_DIR") or os.environ.get("GINI_HOME")
                or (Path.home() / ".gini")).expanduser()


def marker_path() -> Path:
    return gini_home() / "setup.json"


def read_marker() -> dict:
    try:
        return json.loads(marker_path().read_text())
    except Exception:
        return {}


def write_marker(info: dict) -> None:
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(info, indent=2))


def is_setup_done() -> bool:
    """True once at least one image has been pulled and recorded."""
    return bool(read_marker().get("images"))


def setup_version() -> str | None:
    return read_marker().get("version")


def needs_update(app_version: str) -> bool:
    """The app was upgraded past the version setup last ran for -> user should re-run gini-setup."""
    v = setup_version()
    return is_setup_done() and v is not None and v != app_version

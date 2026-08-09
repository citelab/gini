"""Where fragment content lives on disk — the system layer and the user layer.

Two content roots, so gBuilder can be a packaged app (no source tree) AND have somewhere for
teacher-authored / OTA-pulled fragments to land:

  * SYSTEM layer — read-only, ships with the app. The built-in fragments. From source this is the
    package dir; packaged it's bundled package data (resolved via importlib.resources, which works
    from source, a wheel, or a bundle). Moves only with a software update.
  * USER layer — writable, ``~/.gini/content/fragments``. Teacher-authored fragments and content
    pulled over the air from the Teaching Center. Overlays the system layer: a user fragment with the
    same id as a built-in wins (so a course can override), but usually it just adds new ones.

`ENGINE_VERSION` is the vocabulary version a fragment is authored/validated against; a fragment YAML
stamps it so the Teaching Center and each student client can refuse-with-reason on a version gap
rather than mis-compose. See GINI_AUTHORING_DESIGN.md.
"""
from __future__ import annotations

import os
from pathlib import Path

# The engine's vocabulary version. Bump when the set of primitives (elements / predicates / probes /
# capabilities) changes in a way that content must be re-validated against. Authored fragments stamp
# the version they were blessed on.
ENGINE_VERSION = "6.0"
FRAGMENT_SCHEMA = 1               # the fragment YAML shape; bump on breaking schema changes


def _home() -> Path:
    # Same rule as app.paths.gini_home, replicated so `domain` stays free of an `app` import.
    return Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()


def system_content_dir() -> Path:
    """The bundled built-in fragments — packaging-safe."""
    try:
        from importlib.resources import files
        p = Path(str(files("gini.domain"))) / "missions" / "networking"
        if p.exists():
            return p
    except Exception:                                    # noqa: BLE001 — fall back to __file__
        pass
    return Path(__file__).parent / "missions" / "networking"


def user_content_dir() -> Path:
    """Where authored / OTA fragments live. Created on demand (never at import)."""
    return _home() / "content" / "fragments"


def ensure_user_content_dir() -> Path:
    d = user_content_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def content_dirs() -> list[Path]:
    """System first, user second — user overlays system when ids collide."""
    return [system_content_dir(), user_content_dir()]

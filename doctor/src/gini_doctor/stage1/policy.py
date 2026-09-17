"""Doctor policy: how the doctor behaves, settable from the Health Center.

Precedence is **live → cached → built-in**. The doctor always has a complete built-in policy, so
it never needs the Health Center to run; a live policy, when one is fetched, is also cached so
the next offline run uses the most recent one the machine has seen. Every report records which
source and version it ran under, because two reports gathered under different policies are not
the same measurement.

The Health Center serves the same policy two ways: ``/doctor/policy.json`` for Stage 1, and a
plain ``key=value`` ``/doctor/policy.txt`` for Stage 0, which has no JSON parser.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, NamedTuple, Optional

from . import platforms

BUILTIN: Dict[str, Any] = {
    "version": "builtin-1",
    # The oldest Python Stage 1 is written for and tested on.
    "min_python": "3.8",
    "groups_default": ["system"],
    "probe_timeout_s": 20,
}

BUILTIN_SOURCE = "builtin"
CACHED_SOURCE = "cached"
LIVE_SOURCE = "live"

HEALTHCENTER_ENV = "GINI_HEALTHCENTER"


class Policy(NamedTuple):
    data: Dict[str, Any]
    source: str
    note: Optional[str] = None   # why a higher-precedence source was not used, if it was not

    @property
    def version(self) -> str:
        return str(self.data.get("version", "unknown"))

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def parse_version(text: str):
    parts = []
    for piece in str(text).strip().split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def validate(data: Any) -> Dict[str, Any]:
    """Merge a policy over the built-in one, keeping only keys the doctor understands and values
    of the right type. A bad field falls back to its built-in value rather than failing the
    whole policy: a typo on the Health Center must not stop thirty doctors from running."""
    if not isinstance(data, dict):
        raise ValueError("policy must be a JSON object")
    merged = dict(BUILTIN)
    if isinstance(data.get("version"), (str, int)):
        merged["version"] = str(data["version"])
    mp = data.get("min_python")
    if isinstance(mp, str) and len(parse_version(mp)) >= 2:
        merged["min_python"] = mp
    groups = data.get("groups_default")
    if isinstance(groups, list) and groups and all(isinstance(g, str) for g in groups):
        merged["groups_default"] = list(groups)
    t = data.get("probe_timeout_s")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and 1 <= t <= 600:
        merged["probe_timeout_s"] = t
    return merged


CACHE_DIR_ENV = "GINI_DOCTOR_CACHE_DIR"


def cache_dir(platform: Optional[str] = None) -> str:
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return override
    p = platform or platforms.current()
    home = os.path.expanduser("~")
    if p == platforms.WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "gini-doctor")
    if p == platforms.MACOS:
        return os.path.join(home, "Library", "Caches", "gini-doctor")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    return os.path.join(base, "gini-doctor")


def _cache_path(directory: Optional[str]) -> str:
    return os.path.join(directory or cache_dir(), "policy.json")


def fetch_live(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    endpoint = url.rstrip("/") + "/doctor/policy.json"
    req = urllib.request.Request(endpoint, headers={"Accept": "application/json",
                                                    "User-Agent": "gini-doctor"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (URL is operator-set)
        body = resp.read(256 * 1024)
    return validate(json.loads(body.decode("utf-8")))


def load(healthcenter: Optional[str] = None, cache_directory: Optional[str] = None,
         offline: bool = False, timeout: float = 5.0) -> Policy:
    notes = []
    url = healthcenter if healthcenter is not None else os.environ.get(HEALTHCENTER_ENV)
    path = _cache_path(cache_directory)
    if url and not offline:
        try:
            data = fetch_live(url, timeout=timeout)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
            except OSError as e:
                notes.append("could not cache policy: %s" % e)
            return Policy(data, LIVE_SOURCE, "; ".join(notes) or None)
        except (urllib.error.URLError, OSError, ValueError) as e:
            notes.append("Health Center policy unavailable (%s)" % getattr(e, "reason", e))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return Policy(validate(json.load(fh)), CACHED_SOURCE, "; ".join(notes) or None)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        notes.append("cached policy unreadable (%s)" % e)
    return Policy(dict(BUILTIN), BUILTIN_SOURCE, "; ".join(notes) or None)

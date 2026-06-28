"""Per-student isolation: a stable namespaced compose project + workdir per user, so two
students sharing one Kata host never collide (project names, container names, files)."""
from __future__ import annotations

import re
from pathlib import Path


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower()) or "user"


class SessionManager:
    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    def project_name(self, user: str) -> str:
        """The docker compose project (`-p`) for this user — keeps stacks separate."""
        return "gini-" + _slug(user)

    def workdir(self, user: str) -> Path:
        """A per-user project directory under the server's base dir."""
        return self._base / _slug(user)

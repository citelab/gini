"""What does not leave the machine: usernames and home paths.

Applied when a fact is recorded, not when a report is uploaded, so the file on disk and the file
at the Health Center are the same file — and so two users' machines compare equal on paths that
differ only by whose home they are in. The hostname is kept: it is how a report is identified.
Credential files are never read at all, so there is nothing of theirs here to redact.
"""
from __future__ import annotations

import getpass
import os
import re
from typing import Iterable, List, Tuple

USER_TOKEN = "<user>"


def _home_variants() -> List[str]:
    homes = set()
    for h in (os.path.expanduser("~"), os.environ.get("HOME", ""),
              os.environ.get("USERPROFILE", "")):
        if h and len(h) > 1:
            homes.add(os.path.normpath(h))
    out = set()
    for h in homes:
        out.add(h)
        out.add(h.replace("\\", "/"))
    # Longest first, so /home/alice/x is not half-replaced by a shorter variant.
    return sorted(out, key=len, reverse=True)


def _usernames() -> List[str]:
    names = set()
    try:
        names.add(getpass.getuser())
    except Exception:  # getuser raises on some Windows service accounts and in odd containers
        pass
    for var in ("USER", "USERNAME", "LOGNAME"):
        v = os.environ.get(var)
        if v:
            names.add(v)
    # A two-letter username would redact half the report ("id", "os"); those are left alone and
    # home-path redaction still covers the paths they appear in.
    return sorted((n for n in names if len(n) >= 3), key=len, reverse=True)


class Redactor:
    def __init__(self, homes: Iterable[str] | None = None, users: Iterable[str] | None = None):
        self.homes = list(homes) if homes is not None else _home_variants()
        self.users = list(users) if users is not None else _usernames()
        self._user_res: List[Tuple[re.Pattern, str]] = [
            (re.compile(r"(?<![A-Za-z0-9_.-])%s(?![A-Za-z0-9_-])" % re.escape(u)), USER_TOKEN)
            for u in self.users
        ]

    def text(self, value: str) -> str:
        for h in self.homes:
            if h and h in value:
                value = value.replace(h, "~")
        for rx, token in self._user_res:
            value = rx.sub(token, value)
        return value

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        return value

"""Firewall rules -> gRouter ACL deploy commands (the firewall face of the Router Lab).

A GINI Firewall is a gRouter with an ACL data-plane module. A rule `deny <cidr>` (or a bare
CIDR) becomes `gpipe add acl <cidr>` on the real router; deploying clears the pipeline then
adds each rule in order — the same live-deploy path as the Service Function Chain, just a
firewall-shaped front-end. Pure/text-only, so it's unit-tested without Docker.
"""
from __future__ import annotations

import re

_CIDR = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?$")


def parse_rules(text: str) -> list[tuple[str, str]]:
    """[(action, target)] from a rules block — one `deny <cidr>` (or bare CIDR) per line.
    Blank lines and `#` comments are ignored; unrecognised lines are skipped."""
    out: list[tuple[str, str]] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        toks = s.split()
        if toks[0].lower() in ("deny", "drop", "block") and len(toks) >= 2 and _CIDR.match(toks[1]):
            out.append(("deny", toks[1]))
        elif _CIDR.match(toks[0]):
            out.append(("deny", toks[0]))
    return out


def deploy_commands(text: str) -> list[str]:
    """The `gpipe` arg-lines that program this firewall's ACL into the running gRouter:
    `clear`, then `add acl <cidr>` per deny rule, in order."""
    return ["clear"] + [f"add acl {target}" for _action, target in parse_rules(text)]

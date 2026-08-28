"""Parse the gRouter's LIVE inline module chain — the deployed Service Function Chain.

The gRouter's data-plane pipeline is editable/inspectable over the rctl socket via `gpipe`
(`element_query(router, "gpipe list")`). Its `list` output (gr_control.c) looks like:

    base: parse -> [0:acl] -> [1:nat] -> route -> rewrite

i.e. the fixed base (parse … route → rewrite) with the ordered inline NF modules in the
middle. This module turns that into the ordered functions actually running in the router,
so the Router Lab can show the *deployed* chain next to the *edited* one. Pure/text-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_MOD_RE = re.compile(r"\[(\d+):([^\]]+)\]")


@dataclass
class DeployedModule:
    index: int
    type: str          # gRouter module type: acl | nat | counter | block | lua | filter | …


def parse_chain(text: str) -> list[DeployedModule]:
    """The ordered inline NF modules currently deployed in the router (from `gpipe list`)."""
    return [DeployedModule(int(i), t.strip()) for i, t in _MOD_RE.findall(text or "")]


def chain_summary(text: str) -> str:
    """A compact one-line view of the deployed chain, e.g. 'parse → acl → nat → route → rewrite'."""
    mods = parse_chain(text)
    if not mods:
        return "parse → route → rewrite  (no service functions deployed)"
    return "parse → " + " → ".join(m.type for m in mods) + " → route → rewrite"

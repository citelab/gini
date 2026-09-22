"""Parse the gRouter's LIVE inline module chain — the deployed Service Function Chain.

The gRouter's data-plane pipeline is editable/inspectable over the rctl socket via `gpipe`
(`element_query(router, "gpipe list")`). Its `list` output (gr_control.c) looks like:

    base: parse -> [0:acl] -> [1:nat] -> route -> rewrite

i.e. the fixed base (parse … route → rewrite) with the ordered inline NF modules in the
middle. A module that was added with an argument carries it — `[0:acl:10.0.3.0/24]`,
`[1:lua:/scripts/loss.lua]` — and one that was not does not: `[2:counter]`. Routers built
before the argument was recorded print the short form for everything, and this reads both.

This module turns that into the ordered functions actually running in the router, so the
Router Lab can MIRROR the live chain into its editor rather than merely caption it. The
argument is what makes that mirror usable: without it a loaded `loss.lua` was a box that said
"lua", which reads as "not loaded", and two Lua modules were indistinguishable. Pure/text-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: `[index:type]` or `[index:type:arg]`. The type has no colon; the arg runs to the closing
#: bracket, so classify's own `cidr:dscp` argument survives intact.
_MOD_RE = re.compile(r"\[(\d+):([^:\]]+)(?::([^\]]*))?\]")


@dataclass
class DeployedModule:
    index: int
    type: str          # gRouter module type: acl | nat | counter | block | lua | filter | …
    arg: str = ""      # what it was added with — a CIDR, an IP, a script path; "" if none

    @property
    def short(self) -> str:
        """`lua(loss.lua)`, `acl(10.0.3.0/24)` — the type, and what it was given.

        A path is shortened to its file name, because `/scripts/` is the same on every router
        and the name is the part a student chose. Anything else — a CIDR, an IP, a `cidr:dscp`
        — is shown whole; a CIDR's `/24` is not a directory.
        """
        if not self.arg:
            return self.type
        shown = self.arg.rsplit("/", 1)[-1] if self.arg.startswith("/") else self.arg
        return f"{self.type}({shown})"


def parse_chain(text: str) -> list[DeployedModule]:
    """The ordered inline NF modules currently deployed in the router (from `gpipe list`)."""
    return [DeployedModule(int(i), t.strip(), (a or "").strip())
            for i, t, a in _MOD_RE.findall(text or "")]


def chain_summary(text: str) -> str:
    """A compact one-line view of the deployed chain, e.g. 'parse → acl → nat → route → rewrite'."""
    mods = parse_chain(text)
    if not mods:
        return "parse → route → rewrite  (no service functions deployed)"
    return "parse → " + " → ".join(m.short for m in mods) + " → route → rewrite"

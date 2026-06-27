"""Router Lab model — the gRouter's data-plane as a composable module graph.

Pure-Python and Qt-free so it's testable and (later) maps onto the real gRouter's
module graph over the control protocol. Today it drives the Router Lab editor and a
local packet trace for the step-through debugger.

Model (matches the consolidated gRouter design):
  * a MODE gate at ingress: 'legacy' | 'openflow' (OpenFlow = flow table front door,
    legacy pipeline becomes its NORMAL action),
  * a fixed BASE pipeline (parse -> route -> rewrite),
  * an ordered list of INLINE add-on / custom modules that compose in series.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModuleType:
    key: str
    label: str
    icon: str           # an icon key from ui.theme.icons
    accent: str         # accent color key
    kind: str           # base | inline | custom
    description: str
    default_params: dict = field(default_factory=dict)


BASE: list[ModuleType] = [
    ModuleType("parse", "Parse", "grid", "slate", "base", "Ethernet / ARP / IP parse"),
    ModuleType("route", "Route lookup", "router", "blue", "base", "Longest-prefix match"),
    ModuleType("rewrite", "Rewrite", "send", "blue", "base", "Dec TTL, checksum, next-hop MAC"),
]

INLINE: list[ModuleType] = [
    ModuleType("acl", "ACL / Firewall", "firewall", "amber", "inline",
               "Stateless packet filter", {"deny": "10.0.3.0/24"}),
    ModuleType("nat", "NAT", "gateway", "indigo", "inline",
               "Source / destination translation", {"mode": "snat"}),
    ModuleType("rate", "Rate limit", "queue", "green", "inline",
               "Token-bucket policer", {"rate": "10mbps"}),
    ModuleType("classify", "QoS classifier", "layout", "teal", "inline",
               "Tag traffic into classes", {}),
    ModuleType("tap", "Tap / capture", "link", "purple", "inline",
               "Mirror to a collector (original continues)", {}),
]

CUSTOM: list[ModuleType] = [
    ModuleType("lua", "Lua module", "compile", "cyan", "custom",
               "Per-packet Lua hook", {"script": "function process(pkt, ctx)\n  return CONTINUE\nend"}),
    ModuleType("native", "Native module", "controller", "purple", "custom",
               "Zig / C module", {}),
]

MODULE_BY_KEY: dict[str, ModuleType] = {m.key: m for m in (*BASE, *INLINE, *CUSTOM)}


@dataclass
class ModuleInstance:
    type_key: str
    name: str
    params: dict = field(default_factory=dict)

    @property
    def type(self) -> ModuleType:
        return MODULE_BY_KEY[self.type_key]


@dataclass
class Stage:
    label: str
    kind: str            # ingress | mode | base | inline | egress
    key: str | None      # module type key (None for ingress/egress)
    accent: str
    locked: bool
    index: int | None    # index into program.inline (for inline stages), else None


class RouterProgram:
    def __init__(self) -> None:
        self.mode = "legacy"             # 'legacy' | 'openflow'
        self.inline: list[ModuleInstance] = []

    # editing -------------------------------------------------------------- #
    def add(self, type_key: str) -> ModuleInstance:
        mt = MODULE_BY_KEY[type_key]
        inst = ModuleInstance(type_key, mt.label, dict(mt.default_params))
        self.inline.append(inst)
        return inst

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.inline):
            self.inline.pop(index)

    def move(self, index: int, delta: int) -> None:
        j = index + delta
        if 0 <= index < len(self.inline) and 0 <= j < len(self.inline):
            self.inline[index], self.inline[j] = self.inline[j], self.inline[index]

    def set_mode(self, mode: str) -> None:
        self.mode = "openflow" if mode == "openflow" else "legacy"

    # the canonical ordered pipeline --------------------------------------- #
    def stages(self) -> list[Stage]:
        out: list[Stage] = [Stage("ingress", "ingress", None, "slate", True, None)]
        if self.mode == "openflow":
            out.append(Stage("OpenFlow flow table", "mode", "openflow", "teal", True, None))
        parse = BASE[0]
        out.append(Stage(parse.label, "base", parse.key, parse.accent, True, None))
        for i, inst in enumerate(self.inline):
            mt = inst.type
            out.append(Stage(inst.name, "inline", mt.key, mt.accent, False, i))
        for mt in BASE[1:]:
            out.append(Stage(mt.label, "base", mt.key, mt.accent, True, None))
        out.append(Stage("egress", "egress", None, "slate", True, None))
        return out

    # local packet trace (drives the step debugger) ------------------------ #
    def trace(self, dst: str = "10.0.2.10") -> list[str]:
        verdicts: list[str] = []
        dropped = False
        for st in self.stages():
            if dropped:
                verdicts.append("—")
                continue
            if st.kind == "ingress":
                verdicts.append(f"in · dst {dst}")
            elif st.kind == "mode":
                verdicts.append("match → NORMAL (to legacy)")
            elif st.kind == "egress":
                verdicts.append("sent ✓")
            elif st.key == "acl":
                deny = self.inline[st.index].params.get("deny", "")
                if deny and dst.split(".")[:2] == deny.split(".")[:2]:
                    verdicts.append(f"DROP (matches deny {deny})")
                    dropped = True
                else:
                    verdicts.append("pass")
            elif st.key == "route":
                verdicts.append("→ next-hop eth1")
            elif st.key == "rewrite":
                verdicts.append("ttl-- · checksum")
            elif st.key == "tap":
                verdicts.append("mirror → collector, continue")
            else:
                verdicts.append("pass")
        return verdicts

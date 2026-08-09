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
    # How this VNF maps onto the REAL gRouter data-plane module (gpipe): (module_name,
    # arg_param_key or None). None => illustrative (no real backend yet — labeled as such).
    gpipe: tuple | None = None

    @property
    def real(self) -> bool:
        """True if this service function has a real gRouter data-plane backend (deployable
        via `gpipe add …`), False if it's an illustrative teaching stub."""
        return self.gpipe is not None


BASE: list[ModuleType] = [
    ModuleType("parse", "Parse", "grid", "slate", "base", "Ethernet / ARP / IP parse"),
    ModuleType("route", "Route lookup", "router", "blue", "base", "Longest-prefix match"),
    ModuleType("rewrite", "Rewrite", "send", "blue", "base", "Dec TTL, checksum, next-hop MAC"),
]

# Inline VNFs (service functions). `gpipe` names the gRouter data-plane function that
# actually runs, so a module carrying one is REAL — it is programmed into the router and
# moves packets. Every inline VNF now qualifies: classify and tap were the last two
# illustrative entries, and the QoS and Tap work gave both a backend.
#
# The illustrative modules are now the CUSTOM ones below: a Lua or native VNF is real
# only once you have written and compiled it, which is the point of that tier.
INLINE: list[ModuleType] = [
    ModuleType("acl", "ACL / Firewall", "firewall", "amber", "inline",
               "Stateless packet filter (drop a CIDR)", {"deny": "10.0.3.0/24"},
               gpipe=("acl", "deny")),
    ModuleType("nat", "NAT", "gateway", "indigo", "inline",
               "Source NAT / masquerade to an address", {"ip": "203.0.113.1"},
               gpipe=("nat", "ip")),
    ModuleType("block", "Block IP", "firewall", "red", "inline",
               "Drop packets to a destination IP (native Zig module)", {"ip": "10.0.3.5"},
               gpipe=("block", "ip")),
    ModuleType("rate", "Rate limit", "queue", "green", "inline",
               "Token-bucket policer — drops packets that exceed a set rate",
               {"spec": "100/200"}, gpipe=("rate", "spec")),
    ModuleType("classify", "QoS classifier", "layout", "teal", "inline",
               "Mark matching traffic with a DSCP class", {"spec": "10.0.3.0/24:ef"},
               gpipe=("classify", "spec")),
    ModuleType("tap", "Tap / capture", "link", "purple", "inline",
               "Mirror matching packets to a .pcap under /captures "
               "(host: ~/.gini/captures) — open it in Wireshark",
               {"path": "/captures/cap.pcap"}, gpipe=("tap", "path")),
]

# Inline VNFs you write yourself — a Lua script (interpreted per packet) or a native module you
# compile in. Both share the same gpipe seam as the built-in native functions once implemented.
CUSTOM: list[ModuleType] = [
    ModuleType("lua", "Lua VNF", "compile", "cyan", "custom",
               "Per-packet Lua hook — a `process(pkt, ctx)` function you write",
               {"script": "function process(pkt, ctx)\n  return CONTINUE\nend"}),
    ModuleType("native", "Native VNF", "controller", "purple", "custom",
               "A native (Zig / C) data-plane module you compile in", {}),
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


def _ip_in_cidr(ip: str, cidr: str) -> bool:
    """True if dotted-quad `ip` falls inside `cidr` (e.g. '10.0.2.0/24'). Mirrors the C ACL's
    mask-and-compare, so the offline trace agrees with what the real gRouter would drop."""
    try:
        net, _, bits = cidr.partition("/")
        bits = int(bits) if bits else 32
        to_int = lambda a: sum(int(o) << (24 - 8 * i) for i, o in enumerate(a.split(".")))
        mask = (0xffffffff << (32 - bits)) & 0xffffffff if bits else 0
        return (to_int(ip) & mask) == (to_int(net) & mask)
    except Exception:
        return False


class RouterProgram:
    def __init__(self) -> None:
        self.mode = "legacy"             # 'legacy' | 'openflow'
        self.inline: list[ModuleInstance] = []
        self.classifier = ""             # which traffic enters the chain ("" = all)

    # SFC: classifier + deploy to the real gRouter -------------------------- #
    def set_classifier(self, expr: str) -> None:
        self.classifier = (expr or "").strip()

    def deploy_commands(self) -> list[str]:
        """The `gpipe` argument-lines that program THIS chain into the running gRouter (each
        is sent as `gpipe <cmd>`): clear, then add each service function that has a real
        data-plane backend, in order. Illustrative modules are skipped."""
        cmds = ["clear"]
        for inst in self.inline:
            g = inst.type.gpipe
            if g is None:
                continue
            name, argkey = g
            arg = inst.params.get(argkey) if argkey else None
            cmds.append(f"add {name} {arg}" if arg else f"add {name}")
        return cmds

    def illustrative(self) -> list[ModuleInstance]:
        """Inline functions with no real gRouter backend yet (shown, not deployed)."""
        return [i for i in self.inline if i.type.gpipe is None]

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
                if deny and _ip_in_cidr(dst, deny):
                    verdicts.append(f"DROP (matches deny {deny})")
                    dropped = True
                else:
                    verdicts.append("pass")
            elif st.key == "block":
                tgt = self.inline[st.index].params.get("ip", "")
                if tgt and dst == tgt:
                    verdicts.append(f"DROP (blocks {tgt})")
                    dropped = True
                else:
                    verdicts.append("pass")
            elif st.key == "nat":
                verdicts.append("rewrite source · continue")
            elif st.key == "route":
                verdicts.append("→ next-hop eth1")
            elif st.key == "rewrite":
                verdicts.append("ttl-- · checksum")
            elif st.key == "rate":
                verdicts.append("policer · within rate → pass")
            elif st.key == "classify":
                verdicts.append("mark DSCP · continue")
            elif st.key == "tap":
                verdicts.append("mirror → pcap · continue")
            else:
                verdicts.append("pass")
        return verdicts

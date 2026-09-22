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
# Of the CUSTOM ones below, Lua is real too — the router has run scripts since gr_mod_lua.c
# landed, and the box now names one. Only `native` is still illustrative: a compiled module
# has to be built into the image before there is anything to add.
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
    # REAL, not illustrative, since the gRouter's data-plane Lua module (gr_mod_lua.c) has been
    # there all along: `gpipe add lua /scripts/<name>.lua` loads a script that defines
    # `process(pkt, ctx)` and returns DROP or CONTINUE per packet. This box used to carry an
    # inline `script` string and no backend, so dropping it into the chain deployed nothing —
    # "it goes nowhere" — while the console command that DID work never appeared in the editor.
    # The parameter is now the script's container path; the file lives in ~/.gini/scripts on
    # the host, mounted read-only at /scripts on every router. Still `custom`: the code is the
    # student's, and `native` beside it stays illustrative until compiled in.
    ModuleType("lua", "Lua VNF", "compile", "cyan", "custom",
               "Per-packet Lua hook — a `process(pkt, ctx)` you write in ~/.gini/scripts, "
               "given here as /scripts/<name>.lua",
               {"path": ""}, gpipe=("lua", "path")),
    ModuleType("native", "Native VNF", "controller", "purple", "custom",
               "A native (Zig / C) data-plane module you compile in", {}),
]

MODULE_BY_KEY: dict[str, ModuleType] = {m.key: m for m in (*BASE, *INLINE, *CUSTOM)}

#: gpipe module name -> editor type key: the OTHER direction of `ModuleType.gpipe`, for turning a
#: live `gpipe list` back into boxes. Derived, so it cannot drift from the forward mapping.
GPIPE_TO_KEY: dict[str, str] = {m.gpipe[0]: m.key for m in MODULE_BY_KEY.values() if m.gpipe}

#: Where a router sees the student's scripts. `~/.gini/scripts` on the host is bind-mounted here
#: read-only on every gRouter (services/orchestrator), so this is the only prefix a path can have.
SCRIPTS_MOUNT = "/scripts"


def lua_container_path(text: str) -> str:
    """Whatever a student typed for a Lua script, as the path the ROUTER will read it from.

    Students type the name they know — `loss.lua`, or the host path they just saved to — and the
    router knows only `/scripts/<name>`. Normalising here means "loss.lua", "scripts/loss.lua",
    "~/.gini/scripts/loss.lua" and "/scripts/loss.lua" all deploy the same file; a path that is
    already a container path is left exactly alone. Empty stays empty, so an unconfigured box is
    still recognisably unconfigured.
    """
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith(SCRIPTS_MOUNT + "/"):
        return t
    return f"{SCRIPTS_MOUNT}/{t.rsplit('/', 1)[-1]}"


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
    """The chain as the student has composed it — and, when they have not touched it, as the
    router actually has it.

    Two sources feed `inline`. The palette and the parameter fields are one; the live router,
    read back over `gpipe list`, is the other. They are reconciled with ONE flag: `dirty` is set
    by every local edit and cleared by a deploy or a mirror. While it is clear the editor
    follows the router, so a module a student loaded from the console appears as a box a moment
    later. While it is set the editor is a draft and the router is left where it is, so a poll
    cannot pull a half-built chain out from under someone mid-edit.

    Before this the editor only ever showed its own draft, and the live chain was a caption
    under it. A student who loaded loss.lua at the console and opened the Lab saw an empty
    pipeline and the word "lua" in small grey text — and the status said "in sync".
    """

    def __init__(self) -> None:
        self.mode = "legacy"             # 'legacy' | 'openflow'
        self.inline: list[ModuleInstance] = []
        self.classifier = ""             # which traffic enters the chain ("" = all)
        self.dirty = False               # local edits not yet deployed — see the class docstring

    def touch(self) -> None:
        """A local edit happened. The editor is a draft until it is deployed."""
        self.dirty = True

    def mark_deployed(self) -> None:
        """What is in the editor is now what the router has (or a poll will correct it)."""
        self.dirty = False

    # mirroring the live router ------------------------------------------- #
    def matches_live(self, deployed) -> bool:
        """Does the editor already say what the router says? Type and argument, in order."""
        return self._live_key() == self._deployed_key(deployed)

    def sync_from_live(self, deployed) -> bool:
        """Replace the chain with what the router reports. Returns whether anything changed.

        Only called while not `dirty` — the caller's job — so this never overwrites an edit.
        A module type the editor has no box for is kept as a generic native VNF named for its
        type, rather than dropped: the point is that the editor shows what is running, and an
        unfamiliar module is still running.
        """
        if self.matches_live(deployed):
            self.dirty = False
            return False
        out: list[ModuleInstance] = []
        for d in deployed:
            key = GPIPE_TO_KEY.get(d.type)
            # A module the editor has no box for — `counter`, a native VNF — OR a `lua` with no
            # path, which is what a router built before the argument was recorded reports for a
            # loaded script. Either way it is RUNNING but not something we can present as an
            # editable box: a lua box with an empty `path` reads as unconfigured, so `_live_key`
            # would skip it and the mirror would disagree with the router on every poll and
            # re-run forever — a visible flicker that also makes the remove button easy to
            # misclick. The generic native box carries the real type and argument, round-trips
            # cleanly (matches_live stays true), and still shows in the chain.
            if key is None or (key == "lua" and not d.arg):
                out.append(ModuleInstance("native", d.type, {"type": d.type, "arg": d.arg}))
                continue
            mt = MODULE_BY_KEY[key]
            params = dict(mt.default_params)
            if mt.gpipe and mt.gpipe[1]:
                params[mt.gpipe[1]] = d.arg
            name = mt.label
            if key == "lua" and d.arg:
                name = f"{mt.label} · {d.arg.rsplit('/', 1)[-1]}"
            out.append(ModuleInstance(key, name, params))
        self.inline = out
        self.dirty = False
        return True

    @staticmethod
    def _mirrored(inst) -> bool:
        """A generic native box that came from the router, and so names a real module."""
        return inst.type_key == "native" and bool(inst.params.get("type"))

    def _live_key(self) -> list[tuple[str, str]]:
        out = []
        for inst in self.inline:
            g = inst.type.gpipe
            if g is None:
                if self._mirrored(inst):
                    out.append((inst.params["type"], str(inst.params.get("arg", "") or "")))
                continue                              # illustrative: not on the router
            name, argkey = g
            arg = str(inst.params.get(argkey, "") or "") if argkey else ""
            if argkey and not arg:
                continue                              # unconfigured: not deployed either
            out.append((name, arg))
        return out

    @staticmethod
    def _deployed_key(deployed) -> list[tuple[str, str]]:
        return [(d.type, d.arg or "") for d in deployed or []]

    # SFC: classifier + deploy to the real gRouter -------------------------- #
    def set_classifier(self, expr: str) -> None:
        self.classifier = (expr or "").strip()

    def deploy_commands(self) -> list[str]:
        """The `gpipe` argument-lines that program THIS chain into the running gRouter (each
        is sent as `gpipe <cmd>`): clear, then add each service function that has a real
        data-plane backend AND something to deploy, in order.

        Skipped: illustrative modules (no backend), and a module whose backend needs an
        argument that is still blank — a Lua box with no script named. Sending `add lua` with
        nothing after it would only come back as a usage error from the router.

        `clear` first is what makes deploying idempotent rather than additive. It is no longer
        destructive of hand-loaded modules, because while the editor is not dirty it mirrors
        them — so they are in the chain being re-added.
        """
        cmds = ["clear"]
        for inst in self.inline:
            g = inst.type.gpipe
            if g is None:
                if self._mirrored(inst):              # re-add a router module we have no box for
                    arg = str(inst.params.get("arg", "") or "").strip()
                    cmds.append(f"add {inst.params['type']} {arg}".rstrip())
                continue
            name, argkey = g
            arg = str(inst.params.get(argkey, "") or "").strip() if argkey else ""
            if argkey and not arg:
                continue
            cmds.append(f"add {name} {arg}" if arg else f"add {name}")
        return cmds

    def illustrative(self) -> list[ModuleInstance]:
        """Inline functions that are shown but will not be deployed: no real gRouter backend,
        or a backend whose required argument is still blank."""
        out = []
        for i in self.inline:
            g = i.type.gpipe
            if g is None:
                if not self._mirrored(i):             # a mirrored module IS deployed
                    out.append(i)
            elif g[1] and not str(i.params.get(g[1], "") or "").strip():
                out.append(i)
        return out

    # editing -------------------------------------------------------------- #
    def add(self, type_key: str) -> ModuleInstance:
        mt = MODULE_BY_KEY[type_key]
        inst = ModuleInstance(type_key, mt.label, dict(mt.default_params))
        self.inline.append(inst)
        self.dirty = True
        return inst

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.inline):
            self.inline.pop(index)
            self.dirty = True

    def move(self, index: int, delta: int) -> None:
        j = index + delta
        if 0 <= index < len(self.inline) and 0 <= j < len(self.inline):
            self.inline[index], self.inline[j] = self.inline[j], self.inline[index]
            self.dirty = True

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

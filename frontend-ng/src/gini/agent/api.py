"""GiniAPI — the programmatic surface AI agents drive.

This is the single, UI-independent entry point for *building*, *inspecting*, and
*explaining* topologies. The in-app assistant calls it directly; the MCP server
(`mcp_server.py`) wraps these same methods as tools for external agents like Claude.

The `explain_*` methods are deterministic (rule-based) so they work with no LLM and
are unit-testable; an LLM assistant can layer richer narration on top of them.
"""
from __future__ import annotations

from ..app import AppContext
from ..domain import Category, all_devices, devices
from ..domain.topology import DeviceInstance


class GiniAPI:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # -- resolution --------------------------------------------------------- #
    def _resolve(self, ref: str) -> DeviceInstance:
        t = self.ctx.topology
        if ref in t.devices:
            return t.devices[ref]
        d = t.find_by_name(ref)
        if d is None:
            raise KeyError(f"no device named or id'd {ref!r}")
        return d

    # -- catalog ------------------------------------------------------------ #
    def list_device_types(self) -> list[dict]:
        return [
            {"key": d.key, "label": d.label, "category": d.category.value,
             "cloud": d.cloud, "description": d.description}
            for d in all_devices()
        ]

    # -- build -------------------------------------------------------------- #
    def add_device(self, type_key: str, name: str | None = None,
                   x: float = 0.0, y: float = 0.0,
                   properties: dict | None = None) -> dict:
        if type_key not in devices.REGISTRY:
            raise KeyError(f"unknown device type {type_key!r}")
        inst = self.ctx.add_device(type_key, x=x, y=y, name=name, properties=properties)
        return self._device_dict(inst)

    def connect(self, a: str, b: str, label: str = "") -> dict:
        da, db = self._resolve(a), self._resolve(b)
        # ctx.connect auto-attaches Source/Sink riders (dotted edge) and links everything else —
        # same behaviour as the canvas, so agent/recipe wiring matches what a teacher would draw.
        link = self.ctx.connect(da.id, db.id, label)
        return {"id": link.id, "source": da.name, "target": db.name, "label": label}

    def remove_device(self, ref: str) -> None:
        self.ctx.remove_device(self._resolve(ref).id)

    def set_property(self, ref: str, key: str, value: str) -> dict:
        d = self._resolve(ref)
        d.properties[key] = value
        if key == "Name":
            self.ctx.topology.rename(d.id, value)
        self.ctx.bus.device_changed.emit(d.id)
        return self._device_dict(d)

    # -- manual addressing -------------------------------------------------- #
    def set_manual_addressing(self, on: bool) -> None:
        """Toggle manual addressing: stop auto-assigning IPs and honor static_ips
        (auto-filling any interface left blank)."""
        self.ctx.topology.manual_addressing = bool(on)
        self.ctx.bus.topology_changed.emit()

    def set_interface_ip(self, ref: str, link_id: str, ip: str) -> dict:
        """Set (or clear, with an empty string) a device's static IP on one interface
        (identified by the link it sits on). Honored only in manual addressing mode."""
        d = self._resolve(ref)
        bare = (ip or "").strip().split("/")[0]
        si = dict(getattr(d, "static_ips", None) or {})
        if bare:
            si[link_id] = bare
        else:
            si.pop(link_id, None)
        d.static_ips = si
        self.ctx.bus.topology_changed.emit()
        return self._device_dict(d)

    # -- recipes (Wizard blueprints) ---------------------------------------- #
    def list_recipes(self) -> list[dict]:
        from ..domain.recipes import RECIPES
        return [{"id": r.id, "name": r.name, "summary": r.summary,
                 "teaches": r.teaches, "intent": list(r.intent)} for r in RECIPES]

    def suggest_recipes(self, query: str) -> list[dict]:
        """Rank recipes against a free-text intent (deterministic; the LLM mirrors this
        but can also explain). Returns the matching recipes, best first."""
        from ..domain.recipes import suggest_recipes
        return [{"id": r.id, "name": r.name, "summary": r.summary, "teaches": r.teaches}
                for r in suggest_recipes(query)]

    def apply_recipe(self, recipe_id: str) -> dict:
        """Instantiate a curated blueprint onto the canvas — add its elements (laid out
        on a grid), set their properties, and connect them. Deterministic: no LLM in this
        path, so the result is always a valid, known-good topology."""
        from ..domain.recipes import get_recipe
        r = get_recipe(recipe_id)
        if r is None:
            raise KeyError(f"unknown recipe {recipe_id!r}")
        # offset the layout below anything already on the canvas
        base_y = 0.0
        if self.ctx.topology.devices:
            base_y = max(d.y for d in self.ctx.topology.devices.values()) + 170.0
        refs: dict[str, str] = {}
        for el in r.elements:
            x = 60.0 + el.col * 220.0
            y = base_y + 40.0 + el.row * 150.0
            d = self.add_device(el.type_key, x=x, y=y, properties=el.props or None)
            refs[el.ref] = d["id"]
            # box containment (VPC / Subnet / Region): set parent_id so the compiler
            # reads membership. Parents precede children in element order.
            parent = getattr(el, "parent", "")
            if parent and parent in refs:
                dev = self.ctx.topology.devices.get(d["id"])
                if dev is not None:
                    dev.parent_id = refs[parent]
        links = 0
        for a, b in r.links:
            self.connect(refs[a], refs[b])
            links += 1
        return {"recipe": r.id, "name": r.name, "added": list(refs.values()),
                "links": links}

    # -- inspect ------------------------------------------------------------ #
    def get_topology(self) -> dict:
        return self.ctx.topology.to_dict()

    def inspect(self, ref: str) -> dict:
        d = self._resolve(ref)
        out = self._device_dict(d)
        out["neighbors"] = [n.name for n in self.ctx.topology.neighbors(d.id)]
        out["degree"] = self.ctx.topology.degree(d.id)
        return out

    def summary(self) -> dict:
        t = self.ctx.topology
        return {
            "name": t.name,
            "devices": len(t.devices),
            "links": len(t.links),
            "by_category": t.counts_by_category(),
        }

    # -- explain (deterministic narration) ---------------------------------- #
    def explain_topology(self) -> str:
        t = self.ctx.topology
        if not t.devices:
            return "The canvas is empty. Drag a device from the palette to begin."
        counts = t.counts_by_category()
        has_net = any(c in counts for c in (Category.NETWORKING.value, Category.SDN.value))
        has_cloud = any(k in counts for k in (
            Category.CONTAINERS.value, Category.CLOUD_NETWORK.value,
            Category.STORAGE.value, Category.SERVERLESS.value))

        parts = [f"This topology “{t.name}” has {len(t.devices)} elements "
                 f"and {len(t.links)} links."]
        cat_bits = [f"{n} {cat.lower()}" for cat, n in counts.items()]
        parts.append("It spans " + ", ".join(cat_bits) + ".")
        if has_net and has_cloud:
            parts.append("It is a hybrid experiment combining classic network devices "
                         "with cloud-computing primitives.")
        elif has_cloud:
            parts.append("This is primarily a cloud-computing scenario.")
        else:
            parts.append("This is primarily a computer-networking scenario.")

        # connectivity notes
        isolated = [d.name for d in t.devices.values() if t.degree(d.id) == 0]
        if isolated:
            parts.append("Not yet connected: " + ", ".join(isolated) + ".")
        hubs = sorted(t.devices.values(), key=lambda d: t.degree(d.id), reverse=True)
        if hubs and t.degree(hubs[0].id) >= 2:
            parts.append(f"{hubs[0].name} is the most connected node "
                         f"({t.degree(hubs[0].id)} links).")
        return " ".join(parts)

    def context_digest(self) -> str:
        """A compact, ground-truth snapshot of the current canvas for the AI: every
        device with its type, IP/subnet, and what it connects to. Injected into the
        model each turn so the assistant always knows the topology without a tool call.
        """
        t = self.ctx.topology
        if not t.devices:
            return "The canvas is currently empty (no devices placed yet)."
        try:
            from ..services.compiler import address_map
            addr = address_map(t)
        except Exception:
            addr = {}
        lines = [f'Topology "{t.name}": {len(t.devices)} devices, {len(t.links)} links.']
        subnets: set[str] = set()
        for d in t.devices.values():
            info = addr.get(d.name, {})
            ifaces = info.get("interfaces", [])
            ip_bits = []
            for itf in ifaces:
                if itf.get("ip"):
                    ip_bits.append(itf["ip"])
                if itf.get("subnet"):
                    subnets.add(itf["subnet"])
            ip_s = (" — " + ", ".join(ip_bits)) if ip_bits else ""
            nbrs = [n.name for n in t.neighbors(d.id)]
            conn = ("connected to " + ", ".join(nbrs)) if nbrs else "not connected yet"
            gw = next((itf.get("gateway") for itf in ifaces if itf.get("gateway")), None)
            gw_s = f", gateway {gw}" if gw else ""
            lines.append(f"- {d.name} ({d.type.label}){ip_s} — {conn}{gw_s}")
        if subnets:
            lines.append("Subnets: " + ", ".join(sorted(subnets)) + ".")
        return "\n".join(lines)

    def explain_element_type(self, type_key: str) -> str:
        """Teaching guide for a palette element TYPE (what it is + when to use it).
        Falls back to the catalog description if the element isn't in the guide."""
        from ..domain.devices import REGISTRY
        from ..domain.element_guide import guide_for
        dt = REGISTRY.get(type_key)
        if dt is None:
            return f"I don't recognize the element '{type_key}'."
        return guide_for(type_key) or f"{dt.label} ({dt.category.value}). {dt.description}"

    def trace_path(self, src: str, dst: str) -> list[str]:
        """The hop-by-hop device path a packet takes from src to dst (by name).

        Shortest path over the topology graph — which is exactly the forwarding path,
        since routing follows it. Returns device names (e.g. [M1, S1, R1, R2, S2, M5])
        or [] if there's no path. Drives the tutor's packet animation.
        """
        from collections import deque
        s, d = self._resolve(src), self._resolve(dst)
        t = self.ctx.topology
        nbrs: dict[str, list] = {}
        for l in t.links.values():
            nbrs.setdefault(l.source_id, []).append(l.target_id)
            nbrs.setdefault(l.target_id, []).append(l.source_id)
        prev: dict[str, str | None] = {s.id: None}
        q = deque([s.id])
        while q:
            cur = q.popleft()
            if cur == d.id:
                break
            for nb in nbrs.get(cur, []):
                if nb not in prev:
                    prev[nb] = cur
                    q.append(nb)
        if d.id not in prev:
            return []
        path, cur = [], d.id
        while cur is not None:
            path.append(t.devices[cur].name)
            cur = prev[cur]
        path.reverse()
        return path

    def explain_device(self, ref: str) -> str:
        d = self._resolve(ref)
        dt = d.type
        nbrs = [n.name for n in self.ctx.topology.neighbors(d.id)]
        msg = f"{d.name} is a {dt.label} ({dt.category.value}). {dt.description}"
        props = {k: v for k, v in d.properties.items() if v and k != "Name"}
        if props:
            msg += " Configured: " + ", ".join(f"{k}={v}" for k, v in props.items()) + "."
        msg += (f" It connects to {', '.join(nbrs)}." if nbrs
                else " It is not connected to anything yet.")
        return msg

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def _device_dict(d: DeviceInstance) -> dict:
        return {
            "id": d.id, "name": d.name, "type": d.type_key,
            "category": d.type.category.value,
            "x": d.x, "y": d.y, "properties": dict(d.properties),
        }

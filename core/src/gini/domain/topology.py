"""In-memory topology model (pure Python, no Qt).

This is the single source of truth for what's on the canvas. The UI renders it,
the compiler/persistence layers read it, and the AI agent layer mutates it.

FORWARD COMPATIBILITY. A saved file may come from a NEWER GINI than the one reading it: a student
who has not upgraded, the remote run server, a Teaching Center marker on last term's release. This
loader used to build every record with `Cls(**record)`, so one field it did not know -- the first
link property, say -- raised TypeError and the whole file failed to open. Now a record's unknown
keys are kept in `extra` and written back out unchanged, flat, beside the known ones (never under
an "extra" key, which a still-older loader would itself choke on). So a file passes through an
older GINI and keeps what it did not understand. The topology's own unknown top-level keys are kept
the same way. See docs/design/link-properties.md, Phase 0.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field, asdict, fields

from . import devices
from .devices import DeviceType


@dataclass
class DeviceInstance:
    id: str
    type_key: str
    name: str
    x: float = 0.0
    y: float = 0.0
    parent_id: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    # manual addressing: link_id -> static IPv4 (bare dotted-quad). Only honored when
    # the topology is in manual_addressing mode; empty/missing entries auto-fill.
    static_ips: dict[str, str] = field(default_factory=dict)
    # instance "size" tier (1=S … 4=XL) for resizable elements — bigger = more vCPU/mem
    # and proportionally more GINI $/hr. See domain/pricing.py SIZE_TIERS.
    size: int = 1
    # for container elements (VPC/Subnet/Region): the box's drawn size on the canvas.
    # 0 = use the type's default; non-container elements ignore these.
    w: float = 0.0
    h: float = 0.0
    # composition slot this device belongs to (a scaffold/bound dependency, e.g. "A"). Empty = the
    # fragment's own delta. Predicates reference it as `type@slot`.
    slot: str = ""
    # WHICH fragment was materialized into that slot (e.g. "cap-lan"). Provenance only — no predicate
    # reads it; it exists so the canvas can label a slot group with what actually fills it
    # ("nets · cap-lan ×4") instead of just a count, and so a composed board is self-describing.
    slot_source: str = ""
    # saved keys this version does not know, kept so they survive a load/save (module docstring)
    extra: dict = field(default_factory=dict, repr=False)

    @property
    def type(self) -> DeviceType:
        return devices.get(self.type_key)


@dataclass
class Link:
    id: str
    source_id: str
    target_id: str
    label: str = ""
    # "link" = a network cable (carries traffic, compiled to real wiring).
    # "attach" = a rider→donor mount: a Source/Sink runs ON the donor. Carries no traffic and is
    # NOT compiled as a cable — a "runs on" relationship, drawn dotted. source_id is the rider.
    kind: str = "link"
    # saved keys this version does not know, kept so they survive a load/save (module docstring)
    extra: dict = field(default_factory=dict, repr=False)


def _record(obj) -> dict:
    """A dataclass as a saved record: its fields, with `extra` flattened back in beside them. A
    known field always wins over a same-named leftover."""
    rec = asdict(obj)
    extra = rec.pop("extra", None) or {}
    return {**{k: v for k, v in extra.items() if k not in rec}, **rec}


def _from_record(cls, rec: dict):
    """Build `cls` from a saved record, keeping the keys it does not know in `extra`."""
    known = {f.name for f in fields(cls)} - {"extra"}
    obj = cls(**{k: v for k, v in rec.items() if k in known})
    obj.extra = {k: v for k, v in rec.items() if k not in known and k != "extra"}
    return obj


_TOPOLOGY_KEYS = ("name", "manual_addressing", "routing_mode", "devices", "links")

# What makes a link THIS link rather than any link: its identity and endpoints are re-made when a
# link is copied (new ids, remapped endpoints), and its kind decides add_link vs add_attach.
_LINK_IDENTITY = ("id", "source_id", "target_id", "kind")


def link_attributes(link) -> dict:
    """Everything a copied link must keep: the label, any future field (link properties), and keys
    this version does not know. Takes a Link or a saved link record, because the paths that copy
    links hold one or the other.

    These paths -- loading a fragment's board, composing a lab, merging a scaffold, applying a
    staged spec -- used to rebuild each link from its two endpoints alone, so a label was already
    silently lost there, and link properties would have been next."""
    rec = _record(link) if isinstance(link, Link) else dict(link or {})
    rec.pop("extra", None)
    return {k: v for k, v in rec.items() if k not in _LINK_IDENTITY}


def apply_link_attributes(link: "Link", attrs: dict) -> "Link":
    """Put `link_attributes(...)` onto a freshly made link: known fields set, the rest kept in
    `extra`. Identity keys in `attrs` are ignored."""
    known = {f.name for f in fields(Link)} - {"extra"} - set(_LINK_IDENTITY)
    for k, v in (attrs or {}).items():
        if k in _LINK_IDENTITY or k == "extra":
            continue
        if k in known:
            setattr(link, k, v)
        else:
            link.extra[k] = v
    return link


class Topology:
    """A graph of device instances and the links between them."""

    def __init__(self, name: str = "untitled") -> None:
        self.name = name
        self.devices: dict[str, DeviceInstance] = {}
        self.links: dict[str, Link] = {}
        # when True the compiler stops auto-assigning IPs and honors each device's
        # static_ips, auto-filling any interface left blank.
        self.manual_addressing: bool = False
        # "static": the compiler pre-installs shortest-path inter-router routes at boot.
        # "dynamic": routers boot with CONNECTED routes only — a routing protocol (e.g. a
        # student's RIP in the Lua control plane) owns the table. One author per table,
        # so the two computations never fight.
        self.routing_mode: str = "static"
        self._ids = itertools.count(1)
        self._name_counters: dict[str, int] = {}
        # per-type auto-name prefix overrides (type_key -> prefix), set from Settings;
        # empty means use the curated DEFAULT_PREFIXES (R1, S1, M1, …).
        self.prefix_overrides: dict[str, str] = {}
        # top-level keys from a saved file this version does not know (module docstring)
        self.extra: dict = {}

    # -- creation ----------------------------------------------------------- #
    def _new_id(self, prefix: str) -> str:
        # Never hand out an id already in use. The counter can be behind after a load (it's rebuilt
        # from the saved ids), so skip any collision — otherwise a new link/device id would overwrite
        # an existing one in self.links / self.devices (silently deleting an interconnect).
        while True:
            cand = f"{prefix}{next(self._ids)}"
            if cand not in self.devices and cand not in self.links:
                return cand

    def _auto_name(self, dt: DeviceType) -> str:
        # prefix: a user override (e.g. "Mach_"), else the curated default (M, R, S, …)
        base = self.prefix_overrides.get(dt.key) or devices.default_prefix(dt.key)
        # Next number = one past the highest already in use. The counter alone is 0 right after a
        # LOAD (devices deserialize with explicit names, never touching the counter), so also scan
        # the current names — otherwise a device added to a loaded topology collides at {base}1.
        # Manual edits / paste / delete-and-re-add are handled the same way.
        n = self._name_counters.get(base, 0)
        pat = re.compile(rf"^{re.escape(base)}(\d+)$")
        for d in self.devices.values():
            m = pat.match(d.name or "")
            if m:
                n = max(n, int(m.group(1)))
        n += 1
        self._name_counters[base] = n
        return f"{base}{n}"

    def add_device(
        self,
        type_key: str,
        name: str | None = None,
        x: float = 0.0,
        y: float = 0.0,
        parent_id: str | None = None,
        properties: dict[str, str] | None = None,
    ) -> DeviceInstance:
        dt = devices.get(type_key)
        name = name or self._auto_name(dt)
        props = dict(dt.default_properties)
        if properties:
            props.update(properties)
        props["Name"] = name
        inst = DeviceInstance(
            id=self._new_id(dt.key + "-"),
            type_key=type_key,
            name=name,
            x=x,
            y=y,
            parent_id=parent_id,
            properties=props,
        )
        self.devices[inst.id] = inst
        return inst

    def add_link(self, source_id: str, target_id: str, label: str = "") -> Link:
        if source_id not in self.devices or target_id not in self.devices:
            raise KeyError("link endpoints must be existing devices")
        link = Link(self._new_id("link-"), source_id, target_id, label)
        self.links[link.id] = link
        return link

    def add_attach(self, rider_id: str, donor_id: str, label: str = "") -> Link:
        """Mount a rider (Source/Sink) onto its donor. Distinct from a network link: it carries no
        traffic and is not compiled — the rider merely RUNS ON the donor. `rider_id` is source_id."""
        if rider_id not in self.devices or donor_id not in self.devices:
            raise KeyError("attach endpoints must be existing devices")
        link = Link(self._new_id("attach-"), rider_id, donor_id, label, kind="attach")
        self.links[link.id] = link
        return link

    # -- mutation ----------------------------------------------------------- #
    def remove_device(self, device_id: str) -> None:
        self.devices.pop(device_id, None)
        for lid in [l.id for l in self.links.values()
                    if l.source_id == device_id or l.target_id == device_id]:
            self.links.pop(lid, None)

    def remove_link(self, link_id: str) -> None:
        self.links.pop(link_id, None)

    def rename(self, device_id: str, name: str) -> None:
        d = self.devices[device_id]
        d.name = name
        d.properties["Name"] = name

    # -- queries ------------------------------------------------------------ #
    def find_by_name(self, name: str) -> DeviceInstance | None:
        for d in self.devices.values():
            if d.name == name:
                return d
        return None

    def neighbors(self, device_id: str) -> list[DeviceInstance]:
        out = []
        for l in self.links.values():
            if l.source_id == device_id:
                out.append(self.devices[l.target_id])
            elif l.target_id == device_id:
                out.append(self.devices[l.source_id])
        return out

    def degree(self, device_id: str) -> int:
        return sum(1 for l in self.links.values()
                   if device_id in (l.source_id, l.target_id))

    # -- riders / attach edges ---------------------------------------------- #
    def net_links(self) -> list["Link"]:
        """Only the network cables — what the compiler wires. Attach edges are excluded."""
        return [l for l in self.links.values() if l.kind != "attach"]

    def donor_of(self, rider_id: str) -> "DeviceInstance | None":
        """The donor a rider is mounted on (the far end of its attach edge), or None."""
        for l in self.links.values():
            if l.kind == "attach" and l.source_id == rider_id:
                return self.devices.get(l.target_id)
        return None

    def riders_on(self, donor_id: str) -> list["DeviceInstance"]:
        """Every Source/Sink rider mounted on this donor."""
        return [self.devices[l.source_id] for l in self.links.values()
                if l.kind == "attach" and l.target_id == donor_id
                and l.source_id in self.devices]

    def counts_by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.devices.values():
            cat = d.type.category.value
            out[cat] = out.get(cat, 0) + 1
        return out

    # -- serialization (used by persistence + agent layer) ------------------ #
    def to_dict(self) -> dict:
        out = {k: v for k, v in self.extra.items() if k not in _TOPOLOGY_KEYS}
        out.update({
            "name": self.name,
            "manual_addressing": self.manual_addressing,
            "routing_mode": self.routing_mode,
            "devices": [_record(d) for d in self.devices.values()],
            "links": [_record(l) for l in self.links.values()],
        })
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Topology":
        t = cls(data.get("name", "untitled"))
        t.manual_addressing = bool(data.get("manual_addressing", False))
        t.routing_mode = data.get("routing_mode", "static") or "static"
        t.extra = {k: v for k, v in data.items() if k not in _TOPOLOGY_KEYS}
        max_n = 0

        def _bump(ident: str) -> None:
            nonlocal max_n
            for token in ident.replace("-", " ").split():
                if token.isdigit():
                    max_n = max(max_n, int(token))

        for d in data.get("devices", []):
            inst = _from_record(DeviceInstance, d)
            t.devices[inst.id] = inst
            _bump(inst.id)
        for l in data.get("links", []):
            link = _from_record(Link, l)
            t.links[link.id] = link
            _bump(link.id)                       # links share the id counter — MUST count them too,
            #                                      else new ids collide with existing links on load
        t._ids = itertools.count(max_n + 1)
        return t

    def __repr__(self) -> str:
        return f"<Topology {self.name!r}: {len(self.devices)} devices, {len(self.links)} links>"

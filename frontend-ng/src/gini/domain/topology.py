"""In-memory topology model (pure Python, no Qt).

This is the single source of truth for what's on the canvas. The UI renders it,
the compiler/persistence layers read it, and the AI agent layer mutates it.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, asdict

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


class Topology:
    """A graph of device instances and the links between them."""

    def __init__(self, name: str = "untitled") -> None:
        self.name = name
        self.devices: dict[str, DeviceInstance] = {}
        self.links: dict[str, Link] = {}
        # when True the compiler stops auto-assigning IPs and honors each device's
        # static_ips, auto-filling any interface left blank.
        self.manual_addressing: bool = False
        self._ids = itertools.count(1)
        self._name_counters: dict[str, int] = {}
        # per-type auto-name prefix overrides (type_key -> prefix), set from Settings;
        # empty means use the curated DEFAULT_PREFIXES (R1, S1, M1, …).
        self.prefix_overrides: dict[str, str] = {}

    # -- creation ----------------------------------------------------------- #
    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def _auto_name(self, dt: DeviceType) -> str:
        # prefix: a user override (e.g. "Mach_"), else the curated default (M, R, S, …)
        base = self.prefix_overrides.get(dt.key) or devices.default_prefix(dt.key)
        n = self._name_counters.get(base, 0) + 1
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
        return {
            "name": self.name,
            "manual_addressing": self.manual_addressing,
            "devices": [asdict(d) for d in self.devices.values()],
            "links": [asdict(l) for l in self.links.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Topology":
        t = cls(data.get("name", "untitled"))
        t.manual_addressing = bool(data.get("manual_addressing", False))
        max_n = 0
        for d in data.get("devices", []):
            inst = DeviceInstance(**d)
            t.devices[inst.id] = inst
            for token in inst.id.replace("-", " ").split():
                if token.isdigit():
                    max_n = max(max_n, int(token))
        for l in data.get("links", []):
            link = Link(**l)
            t.links[link.id] = link
        t._ids = itertools.count(max_n + 1)
        return t

    def __repr__(self) -> str:
        return f"<Topology {self.name!r}: {len(self.devices)} devices, {len(self.links)} links>"

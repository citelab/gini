"""Pure-Python domain model — no Qt dependency."""
from . import devices, topology
from .devices import Category, DeviceType, REGISTRY, all_devices, by_category, get
from .topology import DeviceInstance, Link, Topology

__all__ = [
    "devices", "topology", "Category", "DeviceType", "REGISTRY",
    "all_devices", "by_category", "get", "DeviceInstance", "Link", "Topology",
]

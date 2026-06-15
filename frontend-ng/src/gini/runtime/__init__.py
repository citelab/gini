"""GINI portable runtime — user-space data plane (Ethernet-in-UDP).

Productized from the proven R0 spike: machines (TAP+shuttle) and a fabric of
user-space switches and gRouters, wired by UDP. The compiler lowers a Topology
onto these; the orchestrator launches them (in-process for tests, Docker for real).
"""
from .frame import (
    BROADCAST, ETH_ARP, ETH_IP, PROTO_ICMP, build_eth, parse_eth,
)
from .grouter import Router
from .hostsim import HostSim
from .switch import LearningSwitch
from .transport import Port, run_loop

__all__ = [
    "Router", "HostSim", "LearningSwitch", "Port", "run_loop",
    "BROADCAST", "ETH_ARP", "ETH_IP", "PROTO_ICMP", "build_eth", "parse_eth",
]

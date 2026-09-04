"""User-space learning switch.

One UDP port per link. Learns source MAC -> ingress port; floods broadcast/multicast
and unknown unicast to all other ports; unicasts known destinations. This is the
"multiplexed fabric switch" from the plan, here as a standalone process for the spike.

Run as a process:  SWITCH_CONFIG='{"name":"s1","ports":[{...}]}' python -m dataplane.switch
"""
from __future__ import annotations

import json
import os
import sys

from .control import maybe_start
from .frame import is_multicast_mac, parse_eth
from .transport import Port, run_loop


class LearningSwitch:
    def __init__(self, cfg: dict) -> None:
        self.name = cfg["name"]
        self.ports = [Port.from_cfg(p, name=f"p{i}") for i, p in enumerate(cfg["ports"])]
        self.table: dict[str, Port] = {}     # mac -> port
        self.log = cfg.get("log", False)
        self._ctrl = maybe_start(self.name, self._control, f"switch {self.name}")

    def _control(self, cmd: str) -> str:
        cmd = cmd.lower()
        if cmd in ("help", "?", "h"):
            return "commands: mactable, ports, help, exit"
        if cmd in ("mactable", "mac", "table"):
            if not self.table:
                return "(mac table empty)"
            return "\n".join(f"  {m}  ->  {p.name}" for m, p in self.table.items())
        if cmd == "ports":
            return ", ".join(p.name for p in self.ports)
        return f"unknown command: {cmd} (try 'help')"

    def handle(self, inport: Port, frame: bytes) -> None:
        dst, src, _etype, _payload = parse_eth(frame)
        self.table[src] = inport
        if dst == "ff:ff:ff:ff:ff:ff" or is_multicast_mac(dst):
            self._flood(inport, frame)
            return
        out = self.table.get(dst)
        if out is None:
            self._flood(inport, frame)
        elif out is not inport:
            out.send(frame)
        if self.log:
            print(f"[{self.name}] {src} -> {dst} in={inport.name}", file=sys.stderr)

    def _flood(self, inport: Port, frame: bytes) -> None:
        for p in self.ports:
            if p is not inport:
                p.send(frame)

    def run(self, stop=None) -> None:
        print(f"[{self.name}] learning switch up, {len(self.ports)} ports", file=sys.stderr)
        run_loop(self.ports, self.handle, stop=stop)


class Hub(LearningSwitch):
    """A Layer-1 repeater — the device a switch replaced.

    A hub is "dumb wire with more ports": every frame that arrives is repeated out of
    every *other* port, with no MAC learning and no filtering. All ports therefore share
    one collision domain and one broadcast domain. Pedagogically this is the foil to the
    Switch: send a unicast h1->h2 through a hub and h3 still sees it (repeated to all);
    through a switch, once it has learned, only h2 does. The control console makes the
    difference explicit — a hub has no MAC table to show."""

    def handle(self, inport: Port, frame: bytes) -> None:
        self._flood(inport, frame)                 # repeat everything; never learn, never filter
        if self.log:
            dst, src, _etype, _payload = parse_eth(frame)
            print(f"[{self.name}] repeat {src} -> {dst} in={inport.name} (flood all)",
                  file=sys.stderr)

    def _control(self, cmd: str) -> str:
        cmd = cmd.lower()
        if cmd in ("help", "?", "h"):
            return ("commands: ports, help, exit  "
                    "(a hub has no MAC table — it repeats every frame to all ports)")
        if cmd in ("mactable", "mac", "table"):
            return "(a hub has no MAC table — it repeats every frame out all other ports)"
        if cmd == "ports":
            return ", ".join(p.name for p in self.ports)
        return f"unknown command: {cmd} (try 'help')"

    def run(self, stop=None) -> None:
        print(f"[{self.name}] hub up (L1 repeater), {len(self.ports)} ports", file=sys.stderr)
        run_loop(self.ports, self.handle, stop=stop)


def make_switch(cfg: dict):
    """Build the right L2 node for a fabric entry: a Hub (flood-all repeater) when the
    compiler marked it `hub`, else a learning Switch."""
    return Hub(cfg) if cfg.get("hub") else LearningSwitch(cfg)


def main() -> None:
    cfg = json.loads(os.environ["SWITCH_CONFIG"])
    make_switch(cfg).run()


if __name__ == "__main__":
    main()

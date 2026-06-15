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

from .frame import is_multicast_mac, parse_eth
from .transport import Port, run_loop


class LearningSwitch:
    def __init__(self, cfg: dict) -> None:
        self.name = cfg["name"]
        self.ports = [Port.from_cfg(p, name=f"p{i}") for i, p in enumerate(cfg["ports"])]
        self.table: dict[str, Port] = {}     # mac -> port
        self.log = cfg.get("log", False)

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

    def run(self) -> None:
        print(f"[{self.name}] learning switch up, {len(self.ports)} ports", file=sys.stderr)
        run_loop(self.ports, self.handle)


def main() -> None:
    cfg = json.loads(os.environ["SWITCH_CONFIG"])
    LearningSwitch(cfg).run()


if __name__ == "__main__":
    main()

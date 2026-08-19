"""Parse the gRouter `queue stats` output for the Router Lab Traffic & QoS panel.

The router console prints, for each per-class queue, its discipline, weight, current
backlog, and forwarded/dropped counters, preceded by the active scheduling policy:

    Scheduling policy: drr
    queue        qdisc       weight  cursize   fwd(pkts)  drop(pkts)   fwd(bytes)
    default      taildrop        1.0        0          12           0          9000
    flowA        taildrop        1.0        5         210          40        220500
    flowB        taildrop        3.0       11         630           3        661500

parse_queue_stats() turns that into (policy, [QueueStat, ...]).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QueueStat:
    name: str
    qdisc: str
    weight: float
    backlog: int          # current occupancy (cursize)
    fwd_pkts: int
    drop_pkts: int
    fwd_bytes: int

    def share_pct(self, total_bytes: int) -> float:
        """This queue's share of the forwarded bytes, as a percentage."""
        return (100.0 * self.fwd_bytes / total_bytes) if total_bytes else 0.0


def parse_queue_stats(text: str) -> tuple[str, list[QueueStat]]:
    """Return (scheduling_policy, [QueueStat...]). Tolerant of blank/garbled lines."""
    policy = ""
    rows: list[QueueStat] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("scheduling policy"):
            # "Scheduling policy: drr (deficit round robin)" -> "drr"
            after = line.split(":", 1)[1].strip() if ":" in line else ""
            policy = after.split()[0] if after else ""
            continue
        toks = line.split()
        # header row: "queue qdisc weight ..."
        if toks[0].lower() == "queue" and len(toks) > 1 and toks[1].lower() == "qdisc":
            continue
        if len(toks) < 7:
            continue
        try:
            rows.append(QueueStat(
                name=toks[0],
                qdisc=toks[1],
                weight=float(toks[2]),
                backlog=int(toks[3]),
                fwd_pkts=int(toks[4]),
                drop_pkts=int(toks[5]),
                fwd_bytes=int(toks[6]),
            ))
        except (ValueError, IndexError):
            continue
    return policy, rows

"""What the gRouter reports about its own traffic: packet fates (`watch`) and interface counters
(`ifstat`), parsed for the Router Lab's packet visualizer and bandwidth meter.

Both commands are the router's own (backend/src/grouter/gr_watch.c) and print one record per line.
Pure: no Qt, no Docker — the Lab polls, this reads.

**Packet fates.** `watch dump <since>` returns the packets recorded after `since`:

    WATCH <on|off> <next_seq> <ms_now>
    W <seq> <ms> <fate> <in_if> <out_if> <module_idx> <module_type|-> <src> <dst> <proto> <ttl> <len> <sport> <dport>

A fate is one letter, so the router can afford to print hundreds a second: F forwarded, D dropped
by a pipeline module (which one is recorded), N no route, T TTL expired, L for the router itself,
A an ARP that arrived (answered, learned or ignored), and S a packet the router itself SENT — its
own ARP requests and replies, and the IP it originates (echo replies, TTL exceeded). ARP comes as
protocol 2054 (its EtherType) with the opcode where a port would be; ICMP carries its type and
code in the two port fields, so a packet can be named "echo request" rather than just "ICMP".
Watching is OFF by default on the router — it costs one branch per packet while off — and the Lab
turns it on only while a student is looking.

**Interface counters.** `ifstat` prints cumulative bytes and packets per interface. They never
reset; bandwidth is the difference between two readings divided by the time between them, which
`bandwidth()` does — and it treats a counter that went DOWN (the router restarted) as a fresh
start rather than a negative rate.

The console client puts its prompt in front of the first output line (`GINI-r1 $ WATCH on …`), so
nothing here anchors to the start of a line except the W/IFSTAT records, which the router always
starts on a fresh line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Fate letter → what the Lab says about it.
FATES = {
    "F": "forwarded",
    "D": "dropped",
    "N": "no route",
    "T": "TTL expired",
    "L": "to the router",
    "A": "ARP",
    "S": "sent by the router",
}

ARP = 2054
PROTOCOLS = {1: "ICMP", 6: "TCP", 17: "UDP", ARP: "ARP"}

#: ICMP type → what a student calls it.
ICMP_TYPES = {0: "echo reply", 3: "unreachable", 5: "redirect", 8: "echo request",
              11: "TTL exceeded"}

_HEAD = re.compile(r"WATCH (on|off) (\d+) (\d+)")
_W = re.compile(r"^W (\d+) (\d+) ([FDNTLAS]) (-?\d+) (-?\d+) (-?\d+) (\S+) "
                r"(\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+) (\d+) (\d+) (\d+) (\d+) (\d+)\s*$", re.M)
_IF = re.compile(r"IFSTAT (\d+) (\S+) (\d+\.\d+\.\d+\.\d+) (\d+) (\d+) (\d+) (\d+)")


@dataclass(frozen=True)
class PacketEvent:
    """One packet's fate at the router."""
    seq: int
    ms: int                 # router's monotonic clock — for spacing, not wall time
    fate: str               # F D N T L
    in_if: int
    out_if: int             # -1 unless forwarded
    module: int             # pipeline index that dropped it, or -1
    module_type: str        # "block", "acl", "lua"... or ""
    src: str
    dst: str
    proto: int
    ttl: int
    length: int
    sport: int = 0
    dport: int = 0

    @property
    def protocol(self) -> str:
        return PROTOCOLS.get(self.proto, f"proto {self.proto}")

    @property
    def is_arp(self) -> bool:
        return self.proto == ARP

    @property
    def verdict(self) -> str:
        """Plain words: `forwarded`, `dropped by block`, `no route`, `ARP answered`…"""
        if self.fate == "D" and self.module_type:
            return f"dropped by {self.module_type}"
        if self.fate == "A":
            return f"ARP {self.module_type}" if self.module_type else "ARP"
        return FATES.get(self.fate, self.fate)

    def what(self) -> str:
        """The packet itself, in a student's words — without its fate."""
        if self.is_arp:
            if self.sport == 1:
                return f"ARP who-has {self.dst}? (asked by {self.src})"
            return f"ARP {self.src} is-at — reply to {self.dst}"
        if self.proto == 1:
            name = ICMP_TYPES.get(self.sport, f"type {self.sport}")
            return f"{self.src} → {self.dst}  ICMP {name}  {self.length} B"
        ports = f" {self.sport}→{self.dport}" if self.proto in (6, 17) else ""
        return f"{self.src} → {self.dst}  {self.protocol}{ports}  {self.length} B"

    def describe(self) -> str:
        """One line: `10.0.1.10 → 10.0.2.10  TCP 56664→8080  84 B  forwarded`."""
        return f"{self.what()}  {self.verdict}"


@dataclass
class WatchDump:
    on: bool = False
    next_seq: int = 0       # pass this back as `since` on the next poll
    ms_now: int = 0
    events: list = field(default_factory=list)
    ok: bool = False        # False when the text carried no WATCH header (an old router image)

    def skipped(self, since: int) -> int:
        """Packets that came and went between two polls faster than the ring could hold them.
        Said, not hidden: a visualizer that quietly shows a sample would teach the wrong rate."""
        if not self.events:
            return 0
        return max(0, self.events[0].seq - since - 1)


def parse_watch(text: str) -> WatchDump:
    t = text or ""
    m = _HEAD.search(t)
    if not m:
        return WatchDump()
    out = WatchDump(on=m.group(1) == "on", next_seq=int(m.group(2)), ms_now=int(m.group(3)),
                    ok=True)
    for g in _W.findall(t):
        out.events.append(PacketEvent(
            seq=int(g[0]), ms=int(g[1]), fate=g[2], in_if=int(g[3]), out_if=int(g[4]),
            module=int(g[5]), module_type="" if g[6] == "-" else g[6], src=g[7], dst=g[8],
            proto=int(g[9]), ttl=int(g[10]), length=int(g[11]), sport=int(g[12]),
            dport=int(g[13])))
    return out


@dataclass(frozen=True)
class IfStat:
    iface: int
    name: str               # tun1, tun2…
    ip: str
    rx_bytes: int
    rx_pkts: int
    tx_bytes: int
    tx_pkts: int


def parse_ifstat(text: str) -> dict[int, IfStat]:
    """`{interface id: IfStat}`. Empty for a router image that predates the command."""
    return {int(g[0]): IfStat(int(g[0]), g[1], g[2], int(g[3]), int(g[4]), int(g[5]), int(g[6]))
            for g in _IF.findall(text or "")}


@dataclass(frozen=True)
class Rate:
    rx_bps: float           # bits per second IN on this interface
    tx_bps: float           # bits per second OUT


def bandwidth(before: dict, after: dict, seconds: float) -> dict[int, Rate]:
    """Bits per second per interface between two `parse_ifstat` readings `seconds` apart.

    A counter that went DOWN means the router restarted between the readings, and is read as
    zero rather than as a huge negative rate. An interface present in only one reading is left
    out — it has no rate yet.
    """
    if seconds <= 0:
        return {}
    out: dict[int, Rate] = {}
    for i, b in after.items():
        a = before.get(i)
        if a is None:
            continue
        rx = b.rx_bytes - a.rx_bytes
        tx = b.tx_bytes - a.tx_bytes
        out[i] = Rate(rx_bps=max(0, rx) * 8 / seconds, tx_bps=max(0, tx) * 8 / seconds)
    return out


def human_bps(bps: float) -> str:
    """`0 b/s`, `840 b/s`, `12.4 kb/s`, `3.20 Mb/s` — decimal units, as link speeds are quoted.

    The unit follows the ROUNDED value: 999,999.6 b/s is "1.00 Mb/s", not "1000.0 kb/s" — a
    rate measured over a poll interval is never exactly round, and the meter showed exactly that.
    """
    if round(bps) < 1000:
        return f"{bps:.0f} b/s"
    if round(bps / 1e3, 1) < 1000:
        return f"{bps / 1e3:.1f} kb/s"
    if round(bps / 1e6, 2) < 1000:
        return f"{bps / 1e6:.2f} Mb/s"
    return f"{bps / 1e9:.2f} Gb/s"


# -- the persistent console session ---------------------------------------------------------------
#
# The Lab's tables poll with one `docker compose exec … grconsole --once` per command: a few hundred
# milliseconds of process start-up each, fine every 2.5 s, far too slow for packets. The visualizer
# and the meter instead keep ONE console session open and send it a command at a time. The console
# answers with the command's output followed by its prompt (`GINI-r1 $ `, no newline), so the prompt
# is the end-of-reply marker. This splits the byte stream there.

_PROMPT = re.compile(r"GINI-\S+ \$ ")


class ConsoleFeed:
    """Feed raw console output in; get complete replies out, one per prompt.

    The banner before the first prompt is discarded — it is the reply to no command. A reply can
    arrive in any number of chunks; nothing is returned until its prompt has.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._ready = False         # the first prompt has been seen

    @property
    def ready(self) -> bool:
        """True once the console has printed its first prompt and will take a command."""
        return self._ready

    def feed(self, text: str) -> list:
        self._buf += text or ""
        replies = []
        while True:
            m = _PROMPT.search(self._buf)
            if not m:
                break
            reply, self._buf = self._buf[:m.start()], self._buf[m.end():]
            if self._ready:
                replies.append(reply)
            self._ready = True
        return replies

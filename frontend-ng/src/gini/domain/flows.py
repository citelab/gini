"""Parse `ss -tin` output into live TCP-flow samples for the Flow HUD.

`ss -tin` prints, per socket, a connection line then an indented tcp_info line:

    State  Recv-Q Send-Q  Local Address:Port   Peer Address:Port
    ESTAB  0      0       10.0.1.10:5201       10.0.2.10:43210
         cubic wscale:7,7 rto:240 rtt:39.5/2.1 mss:1448 cwnd:14 ssthresh:9
         bytes_sent:9000000 bytes_retrans:52200 ... retrans:0/36 delivery_rate 42.1Mbps

parse_ss() pairs each connection line with its info line and pulls out the fields a
congestion-control lab cares about: the algorithm, cwnd, ssthresh, smoothed RTT, the
cumulative retransmit count (a proxy for drops seen by this sender), and the delivery
rate. Only ESTAB sockets carrying data are returned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ADDR = re.compile(r"^(\S+):(\d+)$")


@dataclass
class FlowSample:
    host: str            # the station we polled (source of this sample)
    local_ip: str
    local_port: int
    peer_ip: str
    peer_port: int
    cc: str = ""         # congestion-control algorithm (cubic / reno)
    cwnd: int = 0        # congestion window, in MSS
    ssthresh: int = 0    # slow-start threshold, in MSS (0 = not set)
    rtt_ms: float = 0.0  # smoothed round-trip time
    retrans: int = 0     # cumulative retransmits (loss proxy)
    delivery_mbps: float = 0.0

    @property
    def key(self) -> str:
        """Stable per-flow identity (direction-sensitive: sender -> receiver)."""
        return f"{self.local_ip}:{self.local_port}->{self.peer_ip}:{self.peer_port}"

    @property
    def label(self) -> str:
        """Short chip label, by host/IP (e.g. '10.0.1.10 -> 10.0.2.10')."""
        return f"{self.local_ip} -> {self.peer_ip}"

    @property
    def pair_key(self) -> frozenset:
        """Order-independent identity: the same connection seen from either endpoint
        (A shows local=A peer=B, B shows local=B peer=A) maps to one key."""
        return frozenset((f"{self.local_ip}:{self.local_port}",
                          f"{self.peer_ip}:{self.peer_port}"))


def _num(pattern: str, text: str, cast, default):
    m = re.search(pattern, text)
    if not m:
        return default
    try:
        return cast(m.group(1))
    except (ValueError, IndexError):
        return default


def _delivery_mbps(info: str) -> float:
    m = re.search(r"delivery_rate\s+([\d.]+)([KMG]?)bps", info)
    if not m:
        return 0.0
    val = float(m.group(1))
    return {"": val / 1e6, "K": val / 1e3, "M": val, "G": val * 1e3}.get(m.group(2), val)


def parse_ss(text: str, host: str = "") -> list[FlowSample]:
    lines = (text or "").splitlines()
    out: list[FlowSample] = []
    i = 0
    while i < len(lines):
        toks = lines[i].split()
        # a connection line: STATE recvq sendq local:port peer:port [...]
        if len(toks) >= 5 and toks[0] == "ESTAB":
            la, pa = _ADDR.match(toks[3]), _ADDR.match(toks[4])
            if la and pa:
                # the tcp_info line follows, indented (starts with whitespace)
                info = ""
                if i + 1 < len(lines) and (lines[i + 1][:1].isspace() or "cwnd:" in lines[i + 1]):
                    info = lines[i + 1].strip()
                    i += 1
                cc = ""
                itoks = info.split()
                if itoks and ":" not in itoks[0]:
                    cc = itoks[0]           # first bare word is the algorithm name
                out.append(FlowSample(
                    host=host,
                    local_ip=la.group(1), local_port=int(la.group(2)),
                    peer_ip=pa.group(1), peer_port=int(pa.group(2)),
                    cc=cc,
                    cwnd=_num(r"\bcwnd:(\d+)", info, int, 0),
                    ssthresh=_num(r"\bssthresh:(\d+)", info, int, 0),
                    rtt_ms=_num(r"\brtt:([\d.]+)/", info, float, 0.0),
                    retrans=_num(r"\bretrans:\d+/(\d+)", info, int, 0),
                    delivery_mbps=_delivery_mbps(info),
                ))
        i += 1
    return out


@dataclass
class FlowSeries:
    """A single flow's history, built up across polls, for the Flow HUD plot."""
    key: str
    label: str
    cc: str = ""
    t: list = field(default_factory=list)          # sample timestamps (seconds)
    cwnd: list = field(default_factory=list)        # cwnd (MSS) at each timestamp
    drops: list = field(default_factory=list)       # timestamps where retrans increased
    rtt_ms: float = 0.0
    ssthresh: int = 0
    delivery_mbps: float = 0.0
    _last_retrans: int = -1
    RETAIN_S: float = 330.0     # keep enough history to cover the largest Flow HUD window (a circular buffer)
    MAXPTS: int = 600           # hard safety cap on point count

    def add(self, s: FlowSample, tnow: float) -> None:
        self.cc = s.cc or self.cc
        self.label = s.label
        self.rtt_ms = s.rtt_ms
        self.ssthresh = s.ssthresh
        self.delivery_mbps = s.delivery_mbps
        self.t.append(tnow)
        self.cwnd.append(s.cwnd)
        if self._last_retrans >= 0 and s.retrans > self._last_retrans:
            self.drops.append(tnow)                 # a loss since the last poll
        self._last_retrans = max(self._last_retrans, s.retrans)
        # circular buffer: drop points older than RETAIN_S seconds
        cutoff = tnow - self.RETAIN_S
        while len(self.t) > 1 and self.t[0] < cutoff:
            self.t.pop(0)
            self.cwnd.pop(0)
        if len(self.t) > self.MAXPTS:               # hard safety cap
            del self.t[:-self.MAXPTS]
            del self.cwnd[:-self.MAXPTS]
        # forget drops that scrolled out of the retained window
        self.drops = [d for d in self.drops if not self.t or d >= self.t[0]]


class FlowTracker:
    """Ingests successive `parse_ss` results into per-flow time series.

    Each poll may report a flow twice (once from each endpoint). We keep the direction
    with the larger cwnd — the sender — as the representative for that connection.
    """
    def __init__(self) -> None:
        self.series: dict[str, FlowSeries] = {}

    def ingest(self, samples: list[FlowSample], tnow: float) -> None:
        best: dict[frozenset, FlowSample] = {}
        for s in samples:
            pk = s.pair_key
            if pk not in best or s.cwnd > best[pk].cwnd:
                best[pk] = s
        for pk, s in best.items():
            key = "|".join(sorted(pk))
            fs = self.series.get(key)
            if fs is None:
                fs = FlowSeries(key=key, label=s.label, cc=s.cc)
                self.series[key] = fs
            fs.add(s, tnow)

    def active(self, since: float | None = None) -> list[FlowSeries]:
        """Flows with at least one sample; if `since` given, only those seen after it."""
        out = list(self.series.values())
        if since is not None:
            out = [f for f in out if f.t and f.t[-1] >= since]
        return out

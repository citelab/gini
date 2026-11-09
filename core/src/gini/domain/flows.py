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

**Only the fabric is charted.** A machine container has two kinds of TCP socket: the
experiment's, on the `gini*` TAPs, and GINI's own, on Docker's `eth0` management NIC. The second
kind includes the in-app terminal, which is a websocket to ttyd on port 7681. Opening a
terminal is how a student starts iperf3, so that connection is ALWAYS there, it is there FIRST,
and it never ends. The HUD auto-selected it, stuck to it, and plotted a flat cwnd of 10 while
iperf3 ran in the chip next to it. So the poll also asks for eth0's addresses
(`STATION_CMD`), and `station_flows` drops every socket bound to one of them.

**"Busiest" is measured in bytes, not cwnd or delivery_rate.** An idle socket reports the
initial cwnd of 10, which beats an iperf3 control connection and ties with everything else; and
`delivery_rate` on an app-limited socket is a burst estimate (the terminal reported 3.8 Gbps
while idle). `bytes_acked` between two polls is what the sender actually got across.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ADDR = re.compile(r"^(.*):(\d+)$")

# What the Flow HUD runs in each station: eth0's addresses first, then the sockets. One exec,
# not two, because each exec is a round-trip through Docker and the poll is under a second.
# `ip` failing (a stripped image) prints nothing, which leaves every socket in — the old view.
STATION_CMD = "ip -o addr show dev eth0 2>/dev/null; ss -tin"

_INET = re.compile(r"\binet6?\s+([0-9A-Fa-f.:]+)/")

# An IPv4-mapped IPv6 address: a dual-stack listener (iperf3 -s binds ::) reports its peers
# as [::ffff:10.0.4.10], while the client's own socket reports plain 10.0.4.10. They are the
# SAME host, so without canonicalising them one connection shows up as two flows.
_V6_MAPPED = re.compile(r"^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$", re.IGNORECASE)


def _norm_ip(ip: str) -> str:
    """Canonical form of an address as `ss` printed it: brackets stripped, an IPv4-mapped
    IPv6 address reduced to its IPv4 form."""
    s = (ip or "").strip().strip("[]")
    m = _V6_MAPPED.match(s)
    return m.group(1) if m else s


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
    bytes_acked: int = 0  # cumulative bytes the peer acknowledged (what a sender got across)

    @property
    def key(self) -> str:
        """Stable per-flow identity (direction-sensitive: sender -> receiver)."""
        return f"{self.local_ip}:{self.local_port}->{self.peer_ip}:{self.peer_port}"

    @property
    def label(self) -> str:
        """Chip label. The ports are part of it because one pair of stations commonly carries
        SEVERAL connections at once (iperf3 opens a control connection and its data
        connections), and by IP alone those chips would be indistinguishable."""
        return f"{self.local_ip}:{self.local_port} -> {self.peer_ip}:{self.peer_port}"

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
                    local_ip=_norm_ip(la.group(1)), local_port=int(la.group(2)),
                    peer_ip=_norm_ip(pa.group(1)), peer_port=int(pa.group(2)),
                    cc=cc,
                    cwnd=_num(r"\bcwnd:(\d+)", info, int, 0),
                    ssthresh=_num(r"\bssthresh:(\d+)", info, int, 0),
                    rtt_ms=_num(r"\brtt:([\d.]+)/", info, float, 0.0),
                    retrans=_num(r"\bretrans:\d+/(\d+)", info, int, 0),
                    delivery_mbps=_delivery_mbps(info),
                    bytes_acked=_num(r"\bbytes_acked:(\d+)", info, int, 0),
                ))
        i += 1
    return out


def management_ips(text: str) -> set[str]:
    """The addresses `ip -o addr show dev eth0` printed, canonicalised like a socket's."""
    return {_norm_ip(m.group(1)) for m in _INET.finditer(text or "")}


def station_flows(text: str, host: str = "") -> list[FlowSample]:
    """The experiment's sockets in one station's `STATION_CMD` output: `parse_ss` without the
    ones bound to the management NIC (see the module docstring)."""
    mgmt = management_ips(text)
    return [s for s in parse_ss(text, host) if s.local_ip not in mgmt]


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
    sent_bps: float = 0.0       # bytes acked since the previous poll, as bits per second
    last_busy: float = -1.0     # when this flow last moved any bytes
    _last_retrans: int = -1
    _last_acked: int = -1
    RETAIN_S: float = 330.0     # keep enough history to cover the largest Flow HUD window (a circular buffer)
    MAXPTS: int = 600           # hard safety cap on point count

    def add(self, s: FlowSample, tnow: float) -> None:
        self.cc = s.cc or self.cc
        self.label = s.label
        self.rtt_ms = s.rtt_ms
        self.ssthresh = s.ssthresh
        self.delivery_mbps = s.delivery_mbps
        prev_t = self.t[-1] if self.t else None
        self.sent_bps = 0.0
        if self._last_acked >= 0 and prev_t is not None and tnow > prev_t \
                and s.bytes_acked > self._last_acked:
            self.sent_bps = (s.bytes_acked - self._last_acked) * 8 / (tnow - prev_t)
            self.last_busy = tnow
        self._last_acked = max(self._last_acked, s.bytes_acked)
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

    Each poll may report a flow twice (once from each endpoint). We keep the sender's view as
    the representative for that connection: the side whose peer has acknowledged more bytes,
    and on a tie (no data yet, or an `ss` too old to print bytes_acked) the larger cwnd.

    A connection that ends stops appearing in `ss`, so its series is dropped once it has been
    unseen for IDLE_S. Without that, finishing a transfer and starting another leaves the old
    chip on the panel forever and every re-run adds one more.
    """
    IDLE_S = 10.0        # unseen for this long => the socket is gone; retire the chip
    LIVE_S = 3.0         # seen this recently => the socket is still open (a poll is ~1 s)

    def __init__(self) -> None:
        self.series: dict[str, FlowSeries] = {}
        self.now = 0.0

    def ingest(self, samples: list[FlowSample], tnow: float) -> None:
        self.now = tnow
        best: dict[frozenset, FlowSample] = {}
        for s in samples:
            pk = s.pair_key
            if pk not in best or (s.bytes_acked, s.cwnd) > (best[pk].bytes_acked,
                                                            best[pk].cwnd):
                best[pk] = s
        for pk, s in best.items():
            key = "|".join(sorted(pk))
            fs = self.series.get(key)
            if fs is None:
                fs = FlowSeries(key=key, label=s.label, cc=s.cc)
                self.series[key] = fs
            fs.add(s, tnow)
        # a flow missing from this poll moved nothing in it — otherwise a finished run keeps its
        # last rate and goes on outbidding the run that replaced it
        for f in self.series.values():
            if f.t and f.t[-1] != tnow:
                f.sent_bps = 0.0
        # retire flows whose sockets have disappeared from ss
        for k in [k for k, f in self.series.items()
                  if f.t and (tnow - f.t[-1]) > self.IDLE_S]:
            self.series.pop(k, None)

    def active(self, since: float | None = None) -> list[FlowSeries]:
        """Flows with at least one sample; if `since` given, only those seen after it."""
        out = list(self.series.values())
        if since is not None:
            out = [f for f in out if f.t and f.t[-1] >= since]
        return out

    def is_live(self, key: str) -> bool:
        """Was this flow's socket in one of the last few polls (still open)?"""
        f = self.series.get(key)
        return bool(f and f.t and self.now - f.t[-1] <= self.LIVE_S)

    def busiest(self) -> FlowSeries | None:
        """The live flow that moved the most bytes since the previous poll, or None when every
        flow is idle (between iperf3 runs, or with only a control connection open)."""
        live = [f for f in self.series.values() if self.is_live(f.key) and f.sent_bps > 0]
        return max(live, key=lambda f: f.sent_bps, default=None)

    def last_busy(self) -> FlowSeries | None:
        """The flow that most recently moved any bytes, or None if none ever has."""
        moved = [f for f in self.series.values() if f.last_busy >= 0]
        return max(moved, key=lambda f: f.last_busy, default=None)

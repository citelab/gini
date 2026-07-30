"""Riders — how a Source/Sink runs on its donor, and what its output *means*.

A rider has no container: it runs a tool INSIDE its donor (a Machine, Router, …). This module is the
pure, testable half of that — it turns a rider + its properties into a command to exec on the donor
(`build_command`) and turns the tool's raw output into a single gradable measurement
(`parse_measurement`). No Docker here: the thin adapter that actually execs lives in
`services.rider_runner`, exactly like `domain.probes` ↔ `services.probe_runner`.

The measurement is the "output-as-measurement" from the I/O design: one scalar (loss %, ok %, packet
count) that grading asserts on and that a composition edge can branch on. The raw text is the
"output-as-data" shown in the console.
"""
from __future__ import annotations

import re

from . import devices as _devices


class RiderError(ValueError):
    pass


def _int(v, default: int) -> int:
    try:
        return max(0, int(str(v).strip()))
    except (TypeError, ValueError):
        return default


def build_command(rider_type: str, props: dict | None = None) -> list[str]:
    """The argv to exec INSIDE the donor for this rider. Raises RiderError if a required field
    (e.g. a ping/http Target) is missing, so the UI can prompt instead of running a broken command."""
    props = props or {}
    dt = _devices.REGISTRY.get(rider_type)
    if dt is None or not getattr(dt, "rider", False):
        raise RiderError(f"{rider_type} is not a rider")

    # Count == 0 → run CONTINUOUSLY (until the student stops it); Count == N → do N and auto-stop.
    if rider_type == "ping_probe":
        target = (props.get("Target") or "").strip()
        if not target:
            raise RiderError("Ping Probe has no Target — set one in the inspector, or attach it to a "
                             "donor that's wired to something to auto-pick a reachable neighbour.")
        n = _int(props.get("Count"), 0)
        argv = ["ping"]
        if n > 0:
            argv += ["-c", str(n)]
        argv += ["-W", "1", target]
        return argv

    if rider_type == "http_probe":
        target = (props.get("Target") or "").strip()
        if not target:
            raise RiderError("HTTP Probe needs a Target (the host/service to request).")
        path = (props.get("Path") or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        n = _int(props.get("Count"), 0)
        url = f"http://{target}{path}"
        # one line per request: "<http_code> <time_total_seconds>"
        one = (f'curl -sS -o /dev/null -m 5 -w "%{{http_code}} %{{time_total}}\\n" {url} '
               f'|| echo "000 0"')
        if n > 0:
            script = f'for i in $(seq {n}); do {one}; done'
        else:
            script = f'while true; do {one}; sleep 0.5; done'
        return ["sh", "-lc", script]

    if rider_type == "packet_view":
        n = _int(props.get("Count"), 0)
        filt = (props.get("Filter") or "").strip()
        # default to the GINI overlay (gini0), NOT `any` — otherwise students see Docker-bridge
        # (eth0) plumbing mixed with their drawn network. Interface is overridable.
        iface = (props.get("Interface") or "gini0").strip() or "gini0"
        argv = ["tcpdump", "-n", "-l", "-i", iface]     # -l = line-buffered, so it streams live
        if n > 0:
            argv += ["-c", str(n)]
        if filt:
            argv += filt.split()
        return argv

    if rider_type == "dns_probe":
        name = (props.get("Target") or props.get("Query") or "").strip()
        if not name:
            raise RiderError("DNS Probe needs a Target — the hostname or service name to resolve.")
        n = _int(props.get("Count"), 0)
        # `getent hosts` uses the system resolver (files→DNS), so it honours the overlay entries
        # GINI writes into /etc/hosts and returns the gini0 address — `dig` would bypass hosts and
        # hit Docker's bridge DNS instead. Output line: "<HH:MM:SS> <ip-or-empty>".
        lookup = "getent hosts %s 2>/dev/null | head -1 | awk '{print $1}'" % name
        one = 'echo "$(date +%%T) $(%s)"' % lookup
        script = ("for i in $(seq %d); do %s; sleep 1; done" % (n, one)) if n > 0 \
            else ("while true; do %s; sleep 1; done" % one)
        return ["sh", "-lc", script]

    if rider_type == "traceroute_probe":
        target = (props.get("Target") or "").strip()
        if not target:
            raise RiderError("Traceroute needs a Target to trace a path to.")
        return ["traceroute", "-n", "-w", "2", target]

    if rider_type == "iperf_client":
        target = (props.get("Target") or "").strip()
        if not target:
            raise RiderError("iPerf Client needs a Target running an iPerf Server.")
        secs = _int(props.get("Seconds"), 10) or 10
        argv = ["iperf3", "-c", target, "-t", str(secs)]
        # cap the rate: unlimited iperf saturates the user-space fabric and pins the Docker VM's CPU.
        # A bounded rate is a controlled, repeatable measurement. "0"/"unlimited" opts out.
        rate = (props.get("Bitrate") or "").strip()
        if rate and rate.lower() not in ("0", "unlimited", "max"):
            argv += ["-b", rate]
        return argv

    if rider_type == "iperf_server":
        return ["iperf3", "-s"]

    if rider_type == "iface_stats":
        # stream rx/tx counters from /proc/net/dev, one snapshot per second
        return ["sh", "-lc", 'while true; do cat /proc/net/dev; echo "==="; sleep 1; done']

    if rider_type in ("xv6_shell", "xv6_workload"):
        raise RiderError("xv6 riders run over the console, not docker — use xv6_command().")

    raise RiderError(f"no command builder for rider {rider_type}")


def xv6_command(rider_type: str, props: dict | None = None) -> str:
    """The line to TYPE into an xv6 Machine's console (the qemu-serial driver), for riders that ride
    an xv6 donor. Raises RiderError if a required field is missing."""
    props = props or {}
    if rider_type == "xv6_shell":
        cmd = (props.get("Command") or "").strip()
        if not cmd:
            raise RiderError("Shell Probe needs a Command to run on the xv6 Machine (e.g. 'ls').")
        return cmd
    if rider_type == "xv6_workload":
        prog = (props.get("Program") or "spin").strip()
        args = (props.get("Args") or "").strip()
        line = prog + (f" {args}" if args else "")
        if str(props.get("Background", "true")).lower() in ("true", "1", "yes"):
            line += " &"                                 # run in the background so several compete
        return line
    raise RiderError(f"{rider_type} is not an xv6 rider")


# --------------------------------------------------------------------------- #
# measurement parsing — raw tool output → one gradable scalar (+ a few extras)
# --------------------------------------------------------------------------- #
_PING_LOSS = re.compile(r"(\d+(?:\.\d+)?)%\s*packet loss")
_PING_RTT = re.compile(r"=\s*[\d.]+/([\d.]+)/[\d.]+")     # min/AVG/max…
_PING_TXRX = re.compile(r"(\d+)\s+packets transmitted,\s*(\d+)\s+(?:packets )?received")
_TCPDUMP_CAP = re.compile(r"(\d+)\s+packets captured")


def parse_measurement(rider_type: str, raw: str) -> dict:
    """Reduce a rider's raw output to a measurement dict. Always returns a dict (never raises); an
    unparseable output yields {'ok': False} so the caller can show 'no reading' rather than crash."""
    raw = raw or ""

    if rider_type == "ping_probe":
        loss = _PING_LOSS.search(raw)
        rtt = _PING_RTT.search(raw)
        txrx = _PING_TXRX.search(raw)
        times = [float(t) for t in re.findall(r"time=([\d.]+)", raw)]     # per-reply RTTs
        seqs = [int(s) for s in re.findall(r"(?:icmp_seq|seq)=(\d+)", raw)]
        out: dict = {"ok": bool(loss or times)}
        if loss:                                             # finite run: trust the summary
            out["loss_pct"] = float(loss.group(1))
        elif seqs:                                           # streaming: derive from per-packet lines
            sent, recv = max(seqs) + 1, len(times)
            out["loss_pct"] = round(max(0.0, (sent - recv) / sent * 100), 1) if sent else 0.0
        if rtt:
            out["rtt_avg_ms"] = float(rtt.group(1))
        elif times:
            out["rtt_avg_ms"] = round(sum(times) / len(times), 3)
        if txrx:
            out["transmitted"], out["received"] = int(txrx.group(1)), int(txrx.group(2))
        elif seqs:
            out["transmitted"], out["received"] = max(seqs) + 1, len(times)
        return out

    if rider_type == "http_probe":
        codes, times = [], []
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit():
                codes.append(int(parts[0]))
                try:
                    times.append(float(parts[1]))
                except ValueError:
                    pass
        if not codes:
            return {"ok": False}
        ok = sum(1 for c in codes if 200 <= c < 300)
        out = {"ok": True, "requests": len(codes), "ok_count": ok,
               "ok_pct": round(100.0 * ok / len(codes), 1)}
        if times:
            out["avg_ms"] = round(1000.0 * sum(times) / len(times), 1)
        return out

    if rider_type == "packet_view":
        low = raw.lower()
        if "not found" in low or "permission denied" in low or "no such device" in low:
            return {"ok": False, "error": raw.strip().splitlines()[-1] if raw.strip() else "failed"}
        cap = _TCPDUMP_CAP.search(raw)
        if cap:
            return {"ok": True, "packets": int(cap.group(1))}
        lines = [ln for ln in raw.splitlines() if re.match(r"^\d\d:\d\d:\d\d", ln)]
        if lines:
            return {"ok": True, "packets": len(lines)}
        if "listening on" in low:            # tcpdump ran fine, the link was just idle
            return {"ok": True, "packets": 0}
        return {"ok": False}

    if rider_type == "dns_probe":
        # each line: "<HH:MM:SS> <ip> <ip> ..."  (empty tail = failed to resolve)
        rows = [ln.split() for ln in raw.splitlines() if re.match(r"^\d\d:\d\d:\d\d", ln)]
        if not rows:
            return {"ok": False}
        answered = sum(1 for r in rows if len(r) > 1)
        last = next((" ".join(r[1:]) for r in reversed(rows) if len(r) > 1), "")
        return {"ok": True, "queries": len(rows), "resolved": answered,
                "resolved_pct": round(100.0 * answered / len(rows), 1), "answer": last}

    if rider_type == "traceroute_probe":
        hops = [ln for ln in raw.splitlines() if re.match(r"^\s*\d+\s", ln)]
        if not hops:
            return {"ok": False}
        reached = not any("* * *" in h for h in hops[-1:])
        return {"ok": True, "hops": len(hops), "reached": reached}

    if rider_type in ("iperf_client", "iperf_server"):
        # iperf3 prints "... 942 Mbits/sec ..." lines; take the last rate seen
        rates = re.findall(r"([\d.]+)\s*([KMG])bits/sec", raw)
        if "not found" in raw.lower() or "unable" in raw.lower() or "error" in raw.lower():
            return {"ok": bool(rates), "error": raw.strip().splitlines()[-1] if raw.strip() else ""}
        if not rates:
            return {"ok": "iperf" in raw.lower() or "Server listening" in raw}
        val, unit = rates[-1]
        mbps = float(val) * {"K": 0.001, "M": 1.0, "G": 1000.0}[unit]
        return {"ok": True, "mbps": round(mbps, 1)}

    if rider_type == "iface_stats":
        # sum non-loopback interface counters from the LAST /proc/net/dev block
        blocks = raw.split("===")
        block = next((b for b in reversed(blocks) if ":" in b), "")
        rx_p = tx_p = 0
        for ln in block.splitlines():
            if ":" not in ln or ln.strip().startswith("Inter") or ln.strip().startswith("face"):
                continue
            name, _, rest = ln.partition(":")
            nm = name.strip()
            if nm == "lo" or nm.startswith("eth"):   # hide loopback + the Docker-bridge plumbing
                continue
            f = rest.split()
            if len(f) >= 16:
                rx_p += int(f[1]); tx_p += int(f[9])
        if not block.strip():
            return {"ok": False}
        return {"ok": True, "rx_packets": rx_p, "tx_packets": tx_p}

    return {"ok": False}


# the gradable metrics each rider exposes (metric key, human label) — drives output-check authoring
METRICS: dict[str, list[tuple[str, str]]] = {
    "ping_probe": [("loss_pct", "packet loss %"), ("rtt_avg_ms", "avg RTT (ms)")],
    "http_probe": [("ok_pct", "2xx success %"), ("avg_ms", "avg latency (ms)")],
    "dns_probe": [("resolved_pct", "resolved %")],
    "traceroute_probe": [("hops", "hop count")],
    "iperf_client": [("mbps", "throughput (Mbit/s)")],
    "iperf_server": [("mbps", "throughput (Mbit/s)")],
    "packet_view": [("packets", "packets captured")],
    "iface_stats": [("rx_packets", "rx packets"), ("tx_packets", "tx packets")],
}


def metrics_for(rider_type: str) -> list[tuple[str, str]]:
    return METRICS.get(rider_type, [])


def summarize(rider_type: str, m: dict) -> str:
    """A one-line, human summary of a measurement for the inspector chip."""
    if not m or not m.get("ok"):
        return "no reading"
    if rider_type == "ping_probe":
        s = f"{m.get('loss_pct', '?')}% loss"
        if "rtt_avg_ms" in m:
            s += f" · {m['rtt_avg_ms']} ms avg"
        return s
    if rider_type == "http_probe":
        s = f"{m.get('ok_pct', '?')}% 2xx ({m.get('ok_count', 0)}/{m.get('requests', 0)})"
        if "avg_ms" in m:
            s += f" · {m['avg_ms']} ms avg"
        return s
    if rider_type == "packet_view":
        return f"{m.get('packets', 0)} packets"
    if rider_type == "dns_probe":
        ans = m.get("answer", "")
        return f"{m.get('resolved_pct', '?')}% resolved" + (f" → {ans}" if ans else "")
    if rider_type == "traceroute_probe":
        return f"{m.get('hops', 0)} hops" + ("" if m.get("reached", True) else " (unreached)")
    if rider_type in ("iperf_client", "iperf_server"):
        return f"{m.get('mbps', 0)} Mbit/s" if "mbps" in m else "listening"
    if rider_type == "iface_stats":
        return f"rx {m.get('rx_packets', 0)} · tx {m.get('tx_packets', 0)} pkts"
    return "ok"

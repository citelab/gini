"""OpenFlow flow-table parsing — turn the gRouter's `openflow …` CLI dumps into rows the
SDN dashboard can render.

The OVS element is the C gRouter in `--openflow` mode; its CLI (reachable at runtime via
`element_query(ovs, "openflow …")`) prints the live flow table:

  • `openflow entry all`        -> match fields + actions per active entry
  • `openflow stats entry all`  -> match + packet/byte counts + duration per active entry

Both share the block format (delimited by `=====\nEntry N\n=====`, match fields one tab in,
actions two tabs in). This module parses either, and `flows(entry_dump, stats_dump)` merges
them by index into `FlowEntry` rows. Pure/text-only, so it's fully unit-tested without Docker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ENTRY_RE = re.compile(r"^Entry\s+(\d+)\s*$")
_OFPP = {"OFPP_FLOOD": "flood", "OFPP_CONTROLLER": "controller", "OFPP_NORMAL": "normal",
         "OFPP_ALL": "all", "OFPP_IN_PORT": "in_port", "OFPP_LOCAL": "local",
         "OFPP_TABLE": "table", "OFPP_NONE": "none"}


@dataclass
class FlowEntry:
    index: int
    match: dict = field(default_factory=dict)     # e.g. {"Input port": "1", "IP protocol": "TCP"}
    actions: list = field(default_factory=list)   # e.g. ["output:2", "flood"]
    priority: int | None = None
    packets: int | None = None
    bytes: int | None = None
    duration: int | None = None
    idle_timeout: int | None = None
    hard_timeout: int | None = None

    def match_summary(self) -> str:
        m = self.match
        parts = []
        if m.get("Input port"):
            parts.append("in:" + m["Input port"])
        src, dst = m.get("IP source address"), m.get("IP destination address")
        smac, dmac = (m.get("Ethernet source MAC address"),
                      m.get("Ethernet destination MAC address"))
        if src or dst:
            parts.append(f"{src or '*'}→{dst or '*'}")
        elif smac or dmac:
            parts.append(f"{smac or '*'}→{dmac or '*'}")
        kind = m.get("IP protocol") or m.get("Ethernet frame type")
        if kind:
            parts.append(kind.lower())
        dport = m.get("TCP destination port") or m.get("UDP destination port")
        if dport:
            parts.append("dport " + dport)
        return "  ".join(parts) or "any (wildcard)"

    def action_summary(self) -> str:
        return ", ".join(self.actions) or "—"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _split_blocks(text: str):
    """Yield (index, [lines]) per `Entry N` block, keeping raw lines (with tabs)."""
    idx, buf = None, []
    for raw in (text or "").splitlines():
        line = raw.rstrip("\n")
        if set(line.strip()) == {"="} and line.strip():          # a `=====` divider
            continue
        m = _ENTRY_RE.match(line.strip())
        if m:
            if idx is not None:
                yield idx, buf
            idx, buf = int(m.group(1)), []
        elif idx is not None:
            buf.append(line)
    if idx is not None:
        yield idx, buf


def _port_name(v: str) -> str:
    v = v.strip()
    if v in _OFPP:
        return _OFPP[v]
    if v.startswith("OFPP_"):
        return v[5:].lower()
    return f"output:{v}"


def parse(text: str) -> list[FlowEntry]:
    """Parse one dump (`entry all` or `stats entry all`) into active FlowEntry rows."""
    out: list[FlowEntry] = []
    for index, lines in _split_blocks(text):
        if any(l.strip() == "Entry inactive" for l in lines):
            continue
        e = FlowEntry(index=index)
        section = None
        for line in lines:
            depth = len(line) - len(line.lstrip("\t"))
            s = line.strip()
            if not s:
                continue
            if depth == 0:
                if s == "Match:":
                    section = "match"; continue
                if s == "Actions:":
                    section = "actions"; continue
                section = None                       # a top-level field ends the block
                key, _, val = s.partition(":")
                key, val = key.strip(), val.strip()
                if key == "Priority":
                    e.priority = _int(val)
                elif key == "Packet count":
                    e.packets = _int(val)
                elif key == "Byte count":
                    e.bytes = _int(val)
                elif key == "Duration (seconds)":
                    e.duration = _int(val)
                elif key == "Last matched timeout (seconds)":
                    e.idle_timeout = _int(val)
                elif key == "Last modified timeout (seconds)":
                    e.hard_timeout = _int(val)
            elif depth == 1 and section == "match":
                key, sep, val = s.partition(":")
                if sep:
                    e.match[key.strip()] = val.strip()
            elif depth >= 2 and section == "actions":
                key, sep, val = s.partition(":")
                if sep and key.strip() == "Output port":
                    e.actions.append(_port_name(val))
        out.append(e)
    return out


def merge(entries: list, stats: list) -> list[FlowEntry]:
    """Merge an `entry all` parse (match+actions) with a `stats entry all` parse
    (counters), keyed by entry index. Missing fields are filled from whichever dump has them."""
    by_idx: dict[int, FlowEntry] = {e.index: e for e in entries}
    for s in stats:
        e = by_idx.get(s.index)
        if e is None:
            by_idx[s.index] = s
            continue
        e.match = e.match or s.match
        e.actions = e.actions or s.actions
        for attr in ("priority", "packets", "bytes", "duration",
                     "idle_timeout", "hard_timeout"):
            if getattr(e, attr) is None and getattr(s, attr) is not None:
                setattr(e, attr, getattr(s, attr))
    return [by_idx[i] for i in sorted(by_idx)]


def parse_table_stats(text: str) -> dict:
    """Parse `openflow stats table` into the table-level counters that prove the switch is
    being programmed: active flows, packets looked up, and packets that matched a flow."""
    out: dict[str, int] = {}
    keymap = {
        "Number of active entries": "active",
        "Number of packets looked up in tables": "lookups",
        "Number of packets that hit tables": "matched",
        "Maximum number of supported entries": "max",
    }
    for line in (text or "").splitlines():
        k, sep, v = line.strip().partition(":")
        if sep and k.strip() in keymap:
            n = _int(v.strip())
            if n is not None:
                out[keymap[k.strip()]] = n
    return out


def flows(entry_dump: str, stats_dump: str = "") -> list[FlowEntry]:
    """Convenience: parse both dumps and merge into the display rows."""
    return merge(parse(entry_dump), parse(stats_dump) if stats_dump else [])

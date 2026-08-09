"""gbridge relay: attach real GINI32 (ESP32) boards to the emulated fabric.

A GINI32 board is a real radio sitting on the physical LAN; the fabric is a set of
UDP endpoints on Docker's `gini` bridge. The board cannot reach those endpoints
directly (router containers publish no ports, and on macOS Docker runs in a VM), and
the C gRouter only ever replies to the peer address it was configured with, so a
board on DHCP could not be addressed anyway.

This relay closes both gaps. It is one container on the `gini` network with ONE
published UDP port. Boards send to that port on the host's LAN address; the relay
learns each board's address from its own traffic and shuttles frames to the router
interface that the board's canvas element is wired to.

    phone --802.11--> [board: gBridge] --G32/UDP--> host:5555 --> [relay] --eth/UDP--> [gRouter tun]
                                        \\________ physical LAN ________/    \\__ docker gini net __/

Framing. On the *fabric* hop the payload is a bare Ethernet frame, exactly as every
other GINI link (see transport.Port) -- nothing here changes that contract. On the
*board* hop each datagram carries a fixed 24-byte header so that one published port
can serve many boards and so a board can announce itself before it has traffic:

    off 0  magic   3   b"G32"
    off 3  version 1   = 1
    off 4  type    1   HELLO | HELLO_ACK | FRAME | KEEPALIVE
    off 5  rsv     3   zero
    off 8  board   16  board id, NUL-padded ASCII
    off 24 payload ..  (FRAME: the Ethernet frame; HELLO: optional ASCII info)

Fixed offsets keep the ESP32 side trivial. The relay is deliberately permissive on
ingress (any source address may speak for a board it names) and strict on egress
(frames only go to a board that has checked in) -- the same asymmetry the gRouter's
own tun_recvfrom uses, for the same reason: boards move, and dropping on a mismatch
silently breaks the link.

Run:  GBRIDGE_CONFIG='{"listen_port":5555,"boards":[...]}' python -m dataplane.gbridge
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

from .control import maybe_start
from .transport import Port

MAGIC = b"G32"
VERSION = 1
HDR_LEN = 24
ID_LEN = 16

T_HELLO = 0x01
T_HELLO_ACK = 0x02
T_FRAME = 0x03
T_KEEPALIVE = 0x04
# --- claiming: a board belongs to exactly one laptop ------------------------- #
T_CLAIM = 0x05        # laptop -> board: "you are mine";   payload: owner=<laptop id>
T_CLAIM_ACK = 0x06    # board -> laptop: the claim outcome; payload: owner=<id> [busy=1]
T_RELEASE = 0x07      # laptop -> board: "you are free again"
T_BLINK = 0x08        # laptop -> board: flash the LED so a human can find it

_TYPE_NAME = {T_HELLO: "HELLO", T_HELLO_ACK: "HELLO_ACK",
              T_FRAME: "FRAME", T_KEEPALIVE: "KEEPALIVE",
              T_CLAIM: "CLAIM", T_CLAIM_ACK: "CLAIM_ACK",
              T_RELEASE: "RELEASE", T_BLINK: "BLINK"}

# The board's keepalive period (gbridge_config.h: GB_KEEPALIVE_MS). Also the yardstick
# for "is this datagram late?" — the board decides the real cadence, and nothing breaks
# if the two drift apart.
BOARD_KEEPALIVE_S = 5.0

# How many keepalives may go missing before we call a board offline.
#
# Expressed as a MULTIPLE rather than a flat number of seconds, so the relationship to
# the board's cadence cannot silently rot if either is retuned. Three missed hellos is
# the usual convention for exactly this trade-off (OSPF's dead interval is 4x hello,
# RIP's timeout 6x): enough to ride out the two-in-a-row losses that a busy 2.4 GHz
# channel produces, few enough that unplugging a board is noticed while the student is
# still looking at the screen.
#
# This was 30 s — SIX missed keepalives — which meant pulling a board's power left it
# reading "connected" for over half a minute. Nothing needed that much slack; it was
# just a round number chosen before the keepalive interval existed.
OFFLINE_GRACE = 3.2                     # 3 missed keepalives, plus a little jitter
OFFLINE_AFTER = BOARD_KEEPALIVE_S * OFFLINE_GRACE       # 16 s


def encode(msg_type: int, board_id: str, payload: bytes = b"") -> bytes:
    """Wrap a payload in the board-hop header."""
    bid = board_id.encode("ascii", "ignore")[:ID_LEN]
    return (MAGIC + bytes([VERSION, msg_type, 0, 0, 0])
            + bid.ljust(ID_LEN, b"\x00") + payload)


def decode(data: bytes) -> tuple[int, str, bytes] | None:
    """Parse a board-hop datagram -> (type, board_id, payload), or None if not ours."""
    if len(data) < HDR_LEN or data[:3] != MAGIC or data[3] != VERSION:
        return None
    msg_type = data[4]
    board_id = data[8:8 + ID_LEN].rstrip(b"\x00").decode("ascii", "ignore")
    return msg_type, board_id, data[HDR_LEN:]


class BoardLink:
    """One canvas GINI32 element: its fabric port plus wherever the real board is."""

    def __init__(self, cfg: dict) -> None:
        self.board_id: str = cfg["board_id"]
        self.name: str = cfg.get("name", self.board_id)
        self.port = Port.from_cfg(cfg["fabric"], name=self.board_id)
        # The canvas is the source of truth for the board's fabric-side identity; it is
        # handed to the board in the HELLO_ACK so the firmware needs no network config.
        self.ip: str = cfg.get("ip", "")
        self.mask: str = cfg.get("mask", "255.255.255.0")
        self.gw: str = cfg.get("gw", "")
        self.mac: str = cfg.get("mac", "")
        self.mtu: int = int(cfg.get("mtu", 1400))
        self.mode: str = cfg.get("mode", "nat")     # nat | routed — see netcfg()
        self.label: str = cfg.get("label", self.board_id)   # the canvas element's name
        self.physical_subnet: str = cfg.get("physical_subnet", "")
        self.ap_ssid: str = cfg.get("ap_ssid", "")
        self.ap_pass: str = cfg.get("ap_pass", "")
        # Resolver for devices on the board's radio; "" when the canvas has no Internet
        # element. Rides the HELLO_ACK like everything else, so drawing or deleting the
        # Internet element reaches a RUNNING board on its next keepalive — no reflash,
        # no rejoin of the topology.
        self.dns: str = cfg.get("dns", "")
        # --- reported BY the board (telemetry in each keepalive) --------------- #
        self.channel: int = 0          # forced by the uplink in APSTA; observed only
        self.rssi: int = 0             # uplink signal strength, dBm
        self.uplink: str = ""          # the lab Wi-Fi the board joined
        self.clients: list[dict] = []  # devices on this board's hotspot right now
        self.addr: tuple[str, int] | None = None   # learned from the board's own traffic
        self.last_seen: float = 0.0
        self.rx = 0          # frames board -> fabric
        self.tx = 0          # frames fabric -> board
        self.dropped = 0     # fabric -> board while the board was unknown
        # --- evidence for "why is this flaky?", cheap enough to always collect ----
        # The board keepalives every 5s, so the arrival pattern is a free heartbeat.
        # These three separate the candidate causes instead of leaving us to guess:
        #   worst_gap_s  long silences => the board or the radio went away (RSSI, power)
        #   addr_changes the source we learned for this board moved. Docker's published
        #                port is a stateful translation, so a re-map here breaks the
        #                RETURN path until the board speaks again — invisible from the
        #                board, which sees its own transmits succeed.
        #   late         datagrams arriving more than 2x the keepalive interval apart
        self.worst_gap_s = 0.0
        self.addr_changes = 0
        self.late = 0

    def netcfg(self) -> bytes:
        """The board's fabric-side settings, as the HELLO_ACK payload.

        `mode` matters to the firmware, not just to the compiler: in `nat` the board
        translates its devices onto `ip`, while in `routed` it must forward them
        untouched so the emulated side can address them directly. Sending it here
        keeps the canvas the single source of truth for that decision too.
        """
        parts = [f"ip={self.ip}", f"mask={self.mask}", f"gw={self.gw}",
                 f"mac={self.mac}", f"mtu={self.mtu}", f"mode={self.mode}"]
        # The hotspot is a canvas decision too: the board raises whatever we name
        # here, so a lab can be renamed without touching hardware. `apnet` is the
        # subnet it serves — the board takes .1 and hands out the rest by DHCP.
        if self.physical_subnet:
            parts.append(f"apnet={self.physical_subnet}")
        if self.ap_ssid:
            parts.append(f"apssid={self.ap_ssid}")
        if self.ap_pass:
            parts.append(f"appass={self.ap_pass}")
        # ALWAYS sent, even empty. "dns=" with no value is the instruction to stop
        # offering a resolver — which is what deleting the Internet element means. If it
        # were omitted when empty, a board told about DNS once would keep handing it out
        # forever, promising name resolution through a topology that no longer has a way
        # out. Absent and empty must not mean the same thing here.
        parts.append(f"dns={self.dns}")
        return " ".join(parts).encode("ascii")

    def note_telemetry(self, payload: bytes) -> None:
        """Absorb what the board reports in a keepalive.

        The board sends its FULL client list every time rather than deltas, so a
        missed datagram cannot leave us with a phantom device on the canvas — the
        next keepalive is authoritative.
        Format:  ch=6 rssi=-71 up=lab-wifi c=aa:bb:cc:dd:ee:ff/10.0.9.2 c=...
        """
        clients: list[dict] = []
        for tok in payload.decode("ascii", "ignore").split():
            key, _, val = tok.partition("=")
            if key == "ch" and val.isdigit():
                self.channel = int(val)
            elif key == "rssi":
                try:
                    self.rssi = int(val)
                except ValueError:
                    pass
            elif key == "up":
                self.uplink = val
            elif key == "c" and "/" in val:
                mac, _, ip = val.partition("/")
                clients.append({"mac": mac, "ip": ip})
        if any(t.startswith("c=") or t.startswith("sta=")
               for t in payload.decode("ascii", "ignore").split()):
            self.clients = clients      # only replace when the board actually reported

    @property
    def online(self) -> bool:
        return self.addr is not None and (time.time() - self.last_seen) < OFFLINE_AFTER

    def seen(self, addr: tuple[str, int]) -> bool:
        """Record that we heard from the board. Returns True if its address moved."""
        moved = self.addr is not None and self.addr != addr
        now = time.time()
        # The board is on a fixed 5s keepalive, so the arrival pattern measures the
        # whole path for free. Record the shape of it rather than only the last event:
        # a fault that happened two minutes ago leaves no trace in `last_seen`, and by
        # the time anyone looks, the link is healthy again and the evidence is gone.
        if self.last_seen:
            gap = now - self.last_seen
            if gap > self.worst_gap_s:
                self.worst_gap_s = gap
            if gap > 2 * BOARD_KEEPALIVE_S:
                self.late += 1
        if moved:
            self.addr_changes += 1
        self.addr = addr
        self.last_seen = now
        return moved


class SeenBoard:
    """A board that has spoken to us but is not (yet) wired to a canvas element.

    Kept so gBuilder can offer it for claiming. Boards owned by ANOTHER laptop are
    never recorded — they are not ours to show, and listing them would invite exactly
    the cross-claiming the design exists to prevent.
    """

    def __init__(self, name: str, mac: str, owner: str) -> None:
        self.name = name
        self.mac = mac
        self.owner = owner          # "" = unclaimed and available
        self.addr: tuple[str, int] | None = None
        self.last_seen = 0.0

    @property
    def online(self) -> bool:
        return (time.time() - self.last_seen) < OFFLINE_AFTER

    def as_dict(self) -> dict:
        return {"name": self.name, "mac": self.mac, "owner": self.owner,
                "online": self.online, "last_seen": self.last_seen,
                "claimed": bool(self.owner)}


class GBridge:
    def __init__(self, cfg: dict) -> None:
        self.name = cfg.get("name", "gbridge")
        self.listen_port = int(cfg.get("listen_port", 5555))
        # Who we are to a board. A board that has been claimed by a different laptop
        # ignores us entirely, and we ignore it — that is the whole point.
        self.laptop_id: str = cfg.get("laptop_id", "")
        self.seen: dict[str, SeenBoard] = {}     # board name -> availability
        self._pending: dict[str, str] = {}       # board name -> "claim" | "release" | "blink"
        self.log = bool(cfg.get("log", False))
        self.links: dict[str, BoardLink] = {}
        for b in cfg.get("boards", []):
            link = BoardLink(b)
            self.links[link.board_id] = link
        self.unknown = 0        # datagrams naming a board that is not on the canvas
        self.malformed = 0      # datagrams that are not G32 at all
        # Boards heard on the network but claimed by a DIFFERENT laptop: board -> owner.
        # Deliberately not offered for use, but reported, so "I can see it and you
        # can't" has an explanation instead of looking like broken hardware.
        self.foreign: dict[str, str] = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.listen_port))
        self.sock.setblocking(False)
        self._ctrl = maybe_start(self.name, self._control, f"gbridge {self.name}")

    # ---------------- control console (`boards`, `stats`) ---------------- #

    def _control(self, cmd: str) -> str:
        cmd = (cmd or "").strip().lower()
        if cmd in ("help", "?", "h"):
            return "commands: boards, stats, help, exit"
        if cmd in ("boards", "board", "ls"):
            if not self.links:
                return "(no GINI32 elements on the canvas)"
            rows = []
            for l in self.links.values():
                where = f"{l.addr[0]}:{l.addr[1]}" if l.addr else "-"
                age = f"{time.time() - l.last_seen:.0f}s ago" if l.last_seen else "never"
                rows.append(f"  {l.board_id:<16} {'online ' if l.online else 'OFFLINE'} "
                            f"{where:<22} last seen {age}")
            return "\n".join(rows)
        if cmd in ("stats", "counters"):
            rows = [f"  listening on udp/{self.listen_port}",
                    f"  unknown board id: {self.unknown}   malformed: {self.malformed}"]
            for l in self.links.values():
                rows.append(f"  {l.board_id:<16} rx={l.rx} tx={l.tx} dropped={l.dropped}")
            return "\n".join(rows)
        return f"unknown command: {cmd} (try 'help')"

    def status(self) -> dict:
        """Machine-readable board table (gBuilder polls this to show board health)."""
        return {
            "listen_port": self.listen_port,
            "laptop_id": self.laptop_id,
            # Every board we are allowed to see: ours, plus any that are unclaimed.
            # Boards owned by another laptop never appear here.
            "available": self.available(),
            # Heard, but owned by another laptop. The UI needs this to tell "nothing is
            # out there" apart from "it is out there and not yours".
            "foreign": [{"board_id": b, "owner": o} for b, o in self.foreign.items()],
            "boards": [
                {"board_id": l.board_id, "name": l.name, "label": l.label,
                 "online": l.online,
                 "addr": f"{l.addr[0]}:{l.addr[1]}" if l.addr else None,
                 "last_seen": l.last_seen, "rx": l.rx, "tx": l.tx, "dropped": l.dropped,
                 # path health, cumulative for the run — see BoardLink.seen()
                 "worst_gap_s": round(l.worst_gap_s, 1), "late": l.late,
                 "addr_changes": l.addr_changes,
                 "ip": l.ip, "mode": l.mode, "physical_subnet": l.physical_subnet,
                 "ap_ssid": l.ap_ssid,
                 # observed from the hardware
                 "channel": l.channel, "rssi": l.rssi, "uplink": l.uplink,
                 "clients": list(l.clients)}
                for l in self.links.values()
            ],
        }

    # ---------------- data path ---------------- #

    @staticmethod
    def _kv(payload: bytes) -> dict:
        out = {}
        for tok in payload.decode("ascii", "ignore").split():
            k, _, v = tok.partition("=")
            if k:
                out[k] = v
        return out

    def _note_seen(self, board_id: str, payload: bytes,
                   addr: tuple[str, int]) -> str | None:
        """Record a board's availability. Returns its owner, or None if not ours to see.

        A board owned by another laptop is deliberately NOT recorded: we must not offer
        it for claiming, and we must not answer it.
        """
        kv = self._kv(payload)
        owner = kv.get("owner", "")
        if owner and self.laptop_id and owner != self.laptop_id:
            # Someone else's board: we neither answer it nor list it. But staying out
            # of it must not mean pretending it is not there — a board orphaned by an
            # owner id that no longer exists is otherwise indistinguishable from a dead
            # board, and the only way out (USB `unpair`) is the one nobody thinks to
            # try. Count it, and say so once per board.
            if board_id not in self.foreign:
                print(f"[{self.name}] {board_id} is on the air but claimed by "
                      f"{owner!r}, not us ({self.laptop_id!r}) — ignoring it. "
                      f"Free it with: gini32 unpair, then type `unpair`",
                      file=sys.stderr, flush=True)
            self.foreign[board_id] = owner
            return None
        s = self.seen.get(board_id)
        if s is None:
            s = self.seen[board_id] = SeenBoard(board_id, kv.get("mac", ""), owner)
            print(f"[{self.name}] board {board_id} "
                  f"{'is available to claim' if not owner else 'checked in (ours)'}"
                  f" ({kv.get('mac', '?')})", file=sys.stderr, flush=True)
        s.owner = owner
        if kv.get("mac"):
            s.mac = kv["mac"]
        s.addr = addr
        s.last_seen = time.time()
        return owner

    def _from_board(self, data: bytes, addr: tuple[str, int]) -> None:
        parsed = decode(data)
        if parsed is None:
            self.malformed += 1
            return
        msg_type, board_id, payload = parsed

        # Availability + ownership come first: they decide whether this board is even
        # ours to talk to, regardless of whether the canvas has a role for it.
        if msg_type in (T_HELLO, T_KEEPALIVE, T_CLAIM_ACK):
            owner = self._note_seen(board_id, payload, addr)
            if owner is None:
                return                        # claimed by another laptop — silence
            if msg_type == T_CLAIM_ACK:
                kv = self._kv(payload)
                if kv.get("busy"):
                    print(f"[{self.name}] {board_id} refused: already claimed by "
                          f"{kv.get('owner', 'another laptop')}", file=sys.stderr, flush=True)
                else:
                    print(f"[{self.name}] {board_id} is now claimed by this laptop",
                          file=sys.stderr, flush=True)
                return
            # Anything queued by the UI (claim / release / blink) rides the next contact,
            # because that is the moment we know where the board actually is.
            want = self._pending.pop(board_id, None)
            if want == "claim":
                self._send_to(addr, T_CLAIM, board_id, f"owner={self.laptop_id}".encode())
                return
            if want == "release":
                self._send_to(addr, T_RELEASE, board_id, b"")
                self.seen.pop(board_id, None)
                return
            if want == "blink":
                self._send_to(addr, T_BLINK, board_id, b"")

            if not owner and self.laptop_id:
                # An unclaimed board that this canvas has a ROLE for is claimed by
                # USING it — drawing the element and pressing Run is the intent, and
                # demanding a separate click would be ceremony. Names are baked and
                # visible in the air, so only the laptop whose canvas names this board
                # reaches here. An unclaimed board with no role stays merely visible,
                # waiting to be adopted from the Inspector.
                if board_id in self.links:
                    self._send_to(addr, T_CLAIM, board_id,
                                  f"owner={self.laptop_id}".encode())
                else:
                    return
            # With no laptop identity configured, claiming is simply off and every
            # board is served as before — a single-board bench should not need it.

        link = self.links.get(board_id)
        if link is None:
            self.unknown += 1
            if self.log:
                print(f"[{self.name}] datagram from {addr[0]} names unknown board "
                      f"{board_id!r}; is it on the canvas?", file=sys.stderr)
            return

        moved = link.seen(addr)
        if moved or msg_type == T_HELLO:
            print(f"[{self.name}] board {board_id} at {addr[0]}:{addr[1]}"
                  f"{' (moved)' if moved else ''}", file=sys.stderr, flush=True)

        if msg_type == T_KEEPALIVE and payload:
            before = {c["mac"] for c in link.clients}
            link.note_telemetry(payload)
            after = {c["mac"] for c in link.clients}
            for mac in after - before:
                ip = next((c["ip"] for c in link.clients if c["mac"] == mac), "?")
                print(f"[{self.name}] {link.board_id}: device joined {mac} ({ip})",
                      file=sys.stderr, flush=True)
            for mac in before - after:
                print(f"[{self.name}] {link.board_id}: device left {mac}",
                      file=sys.stderr, flush=True)

        if msg_type in (T_HELLO, T_KEEPALIVE):
            # Hand the board the fabric-side identity the canvas assigned it. A
            # KEEPALIVE is answered for two reasons: it is the board's only proof
            # that we are still here (an unanswered one makes it declare the link
            # dead and re-run discovery), and replying with the current netcfg means
            # a board picks up canvas edits without anyone touching the hardware.
            self._to_board(link, T_HELLO_ACK, link.netcfg())
        elif msg_type == T_FRAME and payload:
            if link.rx == 0:
                print(f"[{self.name}] first frame board->fabric from {board_id} "
                      f"({len(payload)}B) -> {link.port.peer_host}:{link.port.peer_port}",
                      file=sys.stderr, flush=True)
            link.rx += 1
            link.port.send(payload)          # bare Ethernet onto the fabric

    def _send_to(self, addr: tuple[str, int], msg_type: int, board_id: str,
                 payload: bytes) -> None:
        """Send to a raw address — used before a board has a canvas role."""
        try:
            self.sock.sendto(encode(msg_type, board_id, payload), addr)
        except OSError:
            pass

    # ---------------- claiming (called by gBuilder) ---------------- #

    def available(self) -> list[dict]:
        """Boards this laptop may claim, plus the ones it already owns."""
        return [s.as_dict() for s in self.seen.values()]

    def claim(self, board_name: str) -> bool:
        """Queue a claim. It is sent on the board's next contact, which is also the
        moment we know its current address — boards move."""
        if not self.laptop_id:
            return False
        s = self.seen.get(board_name)
        if s is None:
            return False
        if s.owner and s.owner != self.laptop_id:
            return False                      # not ours to take
        self._pending[board_name] = "claim"
        return True

    def release(self, board_name: str) -> bool:
        s = self.seen.get(board_name)
        if s is None or (s.owner and s.owner != self.laptop_id):
            return False
        self._pending[board_name] = "release"
        return True

    def blink(self, board_name: str) -> bool:
        """Flash a board's LED so a human can tell which physical object it is."""
        if board_name not in self.seen:
            return False
        self._pending[board_name] = "blink"
        return True

    def _to_board(self, link: BoardLink, msg_type: int, payload: bytes) -> None:
        if link.addr is None:
            link.dropped += 1
            return
        try:
            self.sock.sendto(encode(msg_type, link.board_id, payload), link.addr)
            if msg_type == T_FRAME:
                link.tx += 1
        except OSError:
            link.dropped += 1

    def _from_fabric(self, link: BoardLink, frame: bytes) -> None:
        # The single most valuable line when a board "sees nothing": it proves the
        # emulated side is actually sending, and separates "the router never tried"
        # from "the board never got it".
        if link.tx == 0 and link.dropped == 0:
            where = f"{link.addr[0]}:{link.addr[1]}" if link.addr else "NOWHERE (board unknown)"
            print(f"[{self.name}] first frame fabric->board for {link.board_id} "
                  f"({len(frame)}B, eth type 0x{frame[12]:02x}{frame[13]:02x}) -> {where}",
                  file=sys.stderr, flush=True)
        self._to_board(link, T_FRAME, frame)

    def run(self) -> None:
        import selectors
        print(f"[{self.name}] up on udp/{self.listen_port}, "
              f"{len(self.links)} board(s): {', '.join(self.links) or '-'}",
              file=sys.stderr, flush=True)
        sel = selectors.DefaultSelector()
        sel.register(self.sock, selectors.EVENT_READ, None)
        for link in self.links.values():
            sel.register(link.port.sock, selectors.EVENT_READ, link)
        last_report = 0.0
        last_counts: tuple = ()
        while True:
            # Periodic counters, so `docker compose logs gbridge` alone is enough to
            # tell a live link from a dead one. Only prints when something changed.
            now = time.time()
            if now - last_report > 10:
                counts = tuple((l.board_id, l.rx, l.tx, l.dropped, l.online)
                               for l in self.links.values())
                if counts != last_counts:
                    for bid, rx, tx, dr, on in counts:
                        print(f"[{self.name}] {bid}: {'online' if on else 'OFFLINE'} "
                              f"rx={rx} tx={tx} dropped={dr}", file=sys.stderr, flush=True)
                    last_counts = counts
                last_report = now
            for key, _ in sel.select(timeout=0.5):
                link: BoardLink | None = key.data
                if link is None:                      # the published board-facing socket
                    while True:
                        try:
                            data, addr = self.sock.recvfrom(65535)
                        except BlockingIOError:
                            break
                        self._from_board(data, addr)
                else:                                  # a fabric port
                    while True:
                        frame = link.port.recv()
                        if frame is None:
                            break
                        self._from_fabric(link, frame)


def _serve_status(relay: "GBridge", port: int) -> None:
    """Expose status() over HTTP so gBuilder can show real board state.

    Deliberately tiny and read-only. gBuilder reaches this through
    Orchestrator.board_status(), so if the relay later moves out of the container
    the UI does not change — only where that method looks.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                                   # noqa: N802
            self._reply(relay.status())

        def do_POST(self):                                  # noqa: N802
            """/claim, /release, /blink — the three things a human does to a board.

            Each only queues: the action is delivered on the board's next contact,
            which is the moment we actually know where it is.
            """
            path = self.path.strip("/").lower()
            n = int(self.headers.get("Content-Length") or 0)
            try:
                arg = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                arg = {}
            board = str(arg.get("board", ""))
            fn = {"claim": relay.claim, "release": relay.release,
                  "blink": relay.blink}.get(path)
            if fn is None:
                self._reply({"ok": False, "error": f"unknown action {path!r}"})
                return
            self._reply({"ok": bool(fn(board)), "board": board, "action": path})

        def log_message(self, *a):                          # keep the log for boards
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print(f"[{relay.name}] status endpoint unavailable on {port}: {e}",
              file=sys.stderr, flush=True)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main() -> None:
    cfg = json.loads(os.environ["GBRIDGE_CONFIG"])
    relay = GBridge(cfg)
    status_port = int(cfg.get("status_port", 0))
    if status_port:
        _serve_status(relay, status_port)
    relay.run()


if __name__ == "__main__":
    main()

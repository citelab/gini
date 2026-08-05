"""mDNS: let a GINI32 board find the lab without being told an address.

A board used to be flashed with the laptop's IP. That breaks the moment the laptop
moves to another network, gets a new DHCP lease, or is swapped for a classmate's —
which in a teaching lab is constantly. Here the laptop instead *announces itself*, and
boards ask "who runs GINI?" on the local link.

Two names are published:

    _gini._udp.local        the service (browsable; carries host, port and TXT)
    gini.local              a plain A record, for a board that only does a name lookup

**Why this lives in gBuilder and not in the gbridge container.** Multicast DNS is
link-local by design: it is sent to 224.0.0.251 with TTL 1. It does not cross Docker's
bridge, and on macOS the Docker VM adds a second boundary. The only process that is
genuinely *on* the classroom LAN is gBuilder itself, so the announcement has to come
from here — which is also convenient, since gBuilder is what knows a topology is running.

Implemented on the standard library alone: gBuilder is installed by students, so adding
a dependency for ~200 lines of well-specified protocol would be a poor trade. Only the
slice of DNS that mDNS needs is implemented (RFC 1035 wire format, RFC 6762 semantics).
"""
from __future__ import annotations

import socket
import struct
import threading
import time

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353

# record types we speak
T_A = 1
T_PTR = 12
T_TXT = 16
T_SRV = 33
T_ANY = 255

C_IN = 0x0001
FLUSH = 0x8000          # mDNS cache-flush bit, set on authoritative responses
QU = 0x8000             # in a question, "unicast reply requested"

SERVICE = "_gini._udp.local"
INSTANCE = "gbuilder._gini._udp.local"
HOSTNAME = "gini.local"
TTL = 120


# --------------------------------------------------------------- wire format

def encode_name(name: str) -> bytes:
    """Encode a dotted name as length-prefixed labels. No compression (always legal)."""
    out = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError(f"bad label {label!r}")
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def decode_name(buf: bytes, off: int) -> tuple[str, int]:
    """Decode a name at `off`, following compression pointers.

    Returns (name, offset just past the name *in the original stream*). Pointer loops
    are bounded rather than trusted — this parses packets from the network.
    """
    labels: list[str] = []
    jumped = False
    end = off
    hops = 0
    while True:
        if off >= len(buf):
            raise ValueError("truncated name")
        ln = buf[off]
        if ln & 0xC0 == 0xC0:                      # compression pointer
            if off + 1 >= len(buf):
                raise ValueError("truncated pointer")
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if not jumped:
                end = off + 2
            off = ptr
            hops += 1
            if hops > 16:
                raise ValueError("compression loop")
            jumped = True
            continue
        if ln == 0:
            if not jumped:
                end = off + 1
            break
        off += 1
        labels.append(buf[off:off + ln].decode("utf-8", "replace"))
        off += ln
    return ".".join(labels), end


def _rr(name: str, rtype: int, rdata: bytes, ttl: int = TTL, flush: bool = True) -> bytes:
    return (encode_name(name)
            + struct.pack("!HHIH", rtype, C_IN | (FLUSH if flush else 0), ttl, len(rdata))
            + rdata)


def build_response(records: list[bytes], qid: int = 0) -> bytes:
    """An authoritative mDNS response carrying `records` as answers."""
    header = struct.pack("!HHHHHH", qid, 0x8400, 0, len(records), 0, 0)
    return header + b"".join(records)


def parse_questions(data: bytes) -> list[tuple[str, int, bool]]:
    """Return [(name, qtype, unicast_reply_wanted)] from a query packet."""
    if len(data) < 12:
        return []
    qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    if flags & 0x8000:                              # a response, not a question
        return []
    out: list[tuple[str, int, bool]] = []
    off = 12
    for _ in range(qd):
        try:
            name, off = decode_name(data, off)
            qtype, qclass = struct.unpack("!HH", data[off:off + 4])
            off += 4
        except (ValueError, struct.error):
            break
        out.append((name.lower(), qtype, bool(qclass & QU)))
    return out


def _txt(pairs: dict[str, str]) -> bytes:
    out = bytearray()
    for k, v in pairs.items():
        item = f"{k}={v}".encode("utf-8")[:255]
        out.append(len(item))
        out += item
    return bytes(out) or b"\x00"


# ------------------------------------------------------------------- helpers

def lan_address() -> str:
    """This machine's address on the LAN the boards are on.

    Uses a connected UDP socket, which only asks the kernel to pick a source address for
    a route — no packet is sent and the destination need not exist or be reachable.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ----------------------------------------------------------------- responder

class GiniAdvertiser:
    """Announces the running GINI lab on the local link so boards can find it.

    Start it when a topology containing GINI32 boards comes up; stop it on teardown, so
    a board never latches onto a laptop with nothing running.
    """

    def __init__(self, port: int, address: str | None = None,
                 instance: str = INSTANCE, hostname: str = HOSTNAME,
                 txt: dict[str, str] | None = None) -> None:
        self.port = int(port)
        self.address = address or lan_address()
        self.instance = instance
        self.hostname = hostname
        self.txt = txt or {}
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.answered = 0        # queries responded to (visible in tests/diagnostics)
        self.error = ""          # why start() failed, for the Console to relay

    # -- the records we are authoritative for -- #

    def records(self) -> list[bytes]:
        addr = socket.inet_aton(self.address)
        srv = struct.pack("!HHH", 0, 0, self.port) + encode_name(self.hostname)
        return [
            _rr(SERVICE, T_PTR, encode_name(self.instance), flush=False),
            _rr(self.instance, T_SRV, srv),
            _rr(self.instance, T_TXT, _txt(self.txt)),
            _rr(self.hostname, T_A, addr),
        ]

    def _answers_for(self, qname: str, qtype: int) -> list[bytes]:
        """Which of our records answer this question (empty = not ours, stay silent)."""
        addr = socket.inet_aton(self.address)
        srv = struct.pack("!HHH", 0, 0, self.port) + encode_name(self.hostname)
        out: list[bytes] = []
        if qname == SERVICE and qtype in (T_PTR, T_ANY):
            # a browse: answer with the instance, and include what the client will ask
            # for next so one round trip is enough
            out += [_rr(SERVICE, T_PTR, encode_name(self.instance), flush=False),
                    _rr(self.instance, T_SRV, srv),
                    _rr(self.instance, T_TXT, _txt(self.txt)),
                    _rr(self.hostname, T_A, addr)]
        elif qname == self.instance.lower() and qtype in (T_SRV, T_ANY):
            out += [_rr(self.instance, T_SRV, srv), _rr(self.hostname, T_A, addr)]
        elif qname == self.instance.lower() and qtype == T_TXT:
            out.append(_rr(self.instance, T_TXT, _txt(self.txt)))
        elif qname == self.hostname.lower() and qtype in (T_A, T_ANY):
            out.append(_rr(self.hostname, T_A, addr))
        return out

    def handle_query(self, data: bytes) -> bytes | None:
        """Build the response to a query packet, or None if it asks nothing of ours."""
        answers: list[bytes] = []
        for qname, qtype, _unicast in parse_questions(data):
            answers += self._answers_for(qname, qtype)
        if not answers:
            return None
        self.answered += 1
        return build_response(answers)

    # -- socket lifecycle -- #

    def _open(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # macOS runs its own mDNSResponder on 5353; SO_REUSEPORT lets us coexist.
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", MDNS_PORT))
        # Pin multicast to the LAN interface rather than letting the kernel choose.
        # A developer laptop running Docker has several interfaces (bridges, vEth,
        # utun/VPN), and the default route for 224.0.0.251 may be none of the one the
        # boards are on — in which case we announce perfectly, to nobody. Both the
        # group membership and the outgoing interface are pinned to self.address.
        local = socket.inet_aton(self.address)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, local)
            mreq = socket.inet_aton(MDNS_ADDR) + local
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            # Loopback-only or an address that is not on a multicast-capable interface:
            # fall back to the default interface rather than refusing to advertise.
            mreq = socket.inet_aton(MDNS_ADDR) + socket.inet_aton("0.0.0.0")
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)   # link-local only
        s.settimeout(0.5)
        return s

    def announce(self) -> None:
        """Unsolicited announcement, so boards learn about us without asking."""
        if self._sock is None:
            return
        try:
            self._sock.sendto(build_response(self.records()), (MDNS_ADDR, MDNS_PORT))
        except OSError:
            pass

    def _serve(self) -> None:
        # RFC 6762 asks for a couple of spaced announcements when a service appears.
        for delay in (0.0, 1.0):
            if self._stop.wait(delay):
                return
            self.announce()
        last_announce = time.time()
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(9000)
            except (socket.timeout, OSError):
                data = None
            if data:
                try:
                    reply = self.handle_query(data)
                except (ValueError, struct.error):
                    reply = None                     # malformed query: ignore, never crash
                if reply:
                    try:
                        self._sock.sendto(reply, (MDNS_ADDR, MDNS_PORT))
                    except OSError:
                        pass
            # re-announce periodically: cheap, and it heals a board that missed the
            # first announcement or whose cache expired.
            if time.time() - last_announce > TTL / 2:
                self.announce()
                last_announce = time.time()

    def start(self) -> bool:
        if self._thread is not None:
            return True
        try:
            self._sock = self._open()
        except OSError as exc:
            # Not fatal — a board can always be pinned with `set server <ip>`. But it
            # MUST be visible: a silent failure here looks exactly like a board that
            # cannot see the network, and sends you debugging the wrong machine.
            self._sock = None
            self.error = f"{exc.__class__.__name__}: {exc}"
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="gini-mdns", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

"""The tap must never claim a checksum offload it cannot perform.

A tap device advertises NETIF_F_HW_CSUM by default, which tells the kernel not to bother
finishing TCP/UDP checksums because "the hardware will". There is no hardware — the next
thing to touch the frame is a read() in the shuttle, and whatever it reads goes on the
wire verbatim. Every packet whose checksum was still pending therefore left the container
mathematically corrupt, and the far end dropped it without a word.

It only bit FORWARDED traffic, which is why it hid: locally generated packets get their
checksum completed on the way out, while a packet merely passing through keeps the state
it arrived with. On Docker Desktop for macOS the replies from the internet come from a
userspace TCP proxy inside the same Linux VM, so they reached the NAT gateway with a
PARTIAL checksum and were forwarded onward unfinished.

Observed: ping worked at any size, DNS worked, the SYN left correctly, the SYN-ACK came
back correctly AT THE GATEWAY — and the machine behind it never saw a valid one, so every
TCP connection to the internet timed out mid-handshake.
"""
import fcntl
import random
import os
import struct

import pytest

from gini.runtime import shuttle


def test_the_ioctl_number_is_derived_correctly():
    """TUNSETOFFLOAD is a magic constant, so check the arithmetic that produces it
    against TUNSETIFF — a value already proven correct by every working topology."""
    def _IOW(t, nr, size):
        return (1 << 30) | (size << 16) | (ord(t) << 8) | nr

    assert _IOW("T", 202, 4) == shuttle.TUNSETIFF          # known good: validates the formula
    assert _IOW("T", 208, 4) == shuttle.TUNSETOFFLOAD


def test_opening_a_tap_disables_offloads(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "open", lambda *a, **k: 7)
    monkeypatch.setattr(fcntl, "ioctl", lambda fd, req, arg: calls.append((fd, req, arg)))

    assert shuttle.open_tap("gini0") == 7

    reqs = [req for _fd, req, _arg in calls]
    assert shuttle.TUNSETIFF in reqs, "the interface was never created"
    assert shuttle.TUNSETOFFLOAD in reqs, (
        "offloads left on: forwarded TCP will carry unfinished checksums and be dropped")
    # order matters — the device must exist before its features can be changed
    assert reqs.index(shuttle.TUNSETIFF) < reqs.index(shuttle.TUNSETOFFLOAD)


def test_offloads_are_disabled_by_passing_zero(monkeypatch):
    """0 clears checksum offload AND segmentation offload. Both are needed: TSO would
    hand us frames far larger than the MTU, which the UDP transport cannot carry."""
    seen = {}
    monkeypatch.setattr(os, "open", lambda *a, **k: 7)

    def fake_ioctl(fd, req, arg):
        if req == shuttle.TUNSETOFFLOAD:
            seen["arg"] = arg

    monkeypatch.setattr(fcntl, "ioctl", fake_ioctl)
    shuttle.open_tap("gini0")
    assert seen["arg"] == 0


def test_a_kernel_that_refuses_is_reported_not_swallowed(monkeypatch, capsys):
    """If this ever fails it looks exactly like a broken network, so it must say so.

    The whole class of bug here is silent failure — a drop path that keeps no record.
    An unusable offload setting is not worth aborting the container for, but it is
    absolutely worth a line in the log.
    """
    monkeypatch.setattr(os, "open", lambda *a, **k: 7)

    def fake_ioctl(fd, req, arg):
        if req == shuttle.TUNSETOFFLOAD:
            raise OSError(22, "Invalid argument")

    monkeypatch.setattr(fcntl, "ioctl", fake_ioctl)
    assert shuttle.open_tap("gini0") == 7          # still usable
    err = capsys.readouterr().err
    assert "offload" in err.lower() and "gini0" in err
    assert "checksum" in err.lower()               # names the consequence, not just the call


def _ref_cksum(ip_hdr, l4, proto):
    """A deliberately separate, textbook implementation to check the fast one against."""
    seg = bytearray(l4)
    off = 16 if proto == 6 else 6
    seg[off:off + 2] = b"\x00\x00"
    ph = ip_hdr[12:20] + bytes([0, proto]) + struct.pack("!H", len(seg))
    data = ph + bytes(seg)
    if len(data) % 2:
        data += b"\x00"
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def _packet(proto=6, payload=b"", cksum=0xF4A1):
    """A frame carrying the unfinished checksum we actually observed on the wire."""
    import socket
    if proto == 6:
        l4 = bytearray(struct.pack("!HHIIBBHHH", 80, 33066, 1, 2, 5 << 4, 0x10,
                                   65408, 0, 0) + payload)
    else:
        l4 = bytearray(struct.pack("!HHHH", 53, 33066, 8 + len(payload), 0) + payload)
    ip = bytearray(struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(l4), 1, 0, 61, proto, 0)
                   + socket.inet_aton("104.20.23.154") + socket.inet_aton("10.0.3.10"))
    l4[(16 if proto == 6 else 6):(18 if proto == 6 else 8)] = struct.pack("!H", cksum)
    return bytearray(bytes.fromhex("aabbccddeeff112233445566") + b"\x08\x00"
                     + bytes(ip) + bytes(l4))


@pytest.mark.parametrize("proto", [6, 17])
@pytest.mark.parametrize("length", [0, 1, 2, 3, 15, 16, 17, 1340])
def test_repaired_checksums_match_an_independent_implementation(proto, length):
    """The whole fix is worthless if the arithmetic is subtly wrong — an odd tail byte
    is the classic way to get RFC 1071 wrong, so every parity is covered."""
    f = _packet(proto, bytes([7]) * length)
    shuttle.repair_l4_checksum(f)
    off = 34 + (16 if proto == 6 else 6)
    assert struct.unpack("!H", bytes(f[off:off + 2]))[0] == \
        _ref_cksum(bytes(f[14:34]), bytes(f[34:]), proto)


def test_an_already_correct_packet_is_left_untouched():
    """Locally generated traffic is already fine; rewriting it would be pure cost, and a
    second pass must be a no-op or the counter would lie."""
    f = _packet(payload=b"hello")
    assert shuttle.repair_l4_checksum(f) is True         # first pass fixes it
    before = bytes(f)
    assert shuttle.repair_l4_checksum(f) is False        # second does nothing
    assert bytes(f) == before


def test_a_udp_checksum_of_zero_is_left_alone():
    """Zero legally means "not computed" for UDP. Inventing one would change the meaning
    of the packet rather than repair it."""
    f = _packet(proto=17, payload=b"x", cksum=0)
    assert shuttle.repair_l4_checksum(f) is False


@pytest.mark.parametrize("frame", [
    b"", b"\x00" * 13,
    bytes.fromhex("aabbccddeeff112233445566") + b"\x08\x06" + b"\x00" * 40,   # ARP
    bytes.fromhex("aabbccddeeff112233445566") + b"\x86\xdd" + b"\x00" * 40,   # IPv6
])
def test_non_ipv4_and_runt_frames_are_ignored(frame):
    assert shuttle.repair_l4_checksum(bytearray(frame)) is False


def test_fragments_are_left_alone():
    """A non-first fragment has no complete L4 payload, so any checksum computed over it
    would be wrong — and would replace a correct value with a broken one."""
    f = _packet(payload=b"x" * 40)
    f[14 + 6] = 0x00
    f[14 + 7] = 0x25                                      # fragment offset != 0
    assert shuttle.repair_l4_checksum(f) is False


def test_junk_never_raises():
    """This runs on every forwarded frame, so an exception here takes the data plane
    down for the whole topology."""
    random.seed(3)
    for _ in range(2000):
        b = bytearray(random.getrandbits(8) for _ in range(random.randint(0, 160)))
        shuttle.repair_l4_checksum(b)         # must not raise


def test_the_tap_is_still_created_as_a_no_pi_tap(monkeypatch):
    """Guard the flags while we are in here: IFF_NO_PI means no 4-byte prefix, and the
    whole wire format assumes frames start at the Ethernet header."""
    seen = {}
    monkeypatch.setattr(os, "open", lambda *a, **k: 7)

    def fake_ioctl(fd, req, arg):
        if req == shuttle.TUNSETIFF:
            seen["flags"] = struct.unpack("16sH", arg)[1]

    monkeypatch.setattr(fcntl, "ioctl", fake_ioctl)
    shuttle.open_tap("gini0")
    assert seen["flags"] == shuttle.IFF_TAP | shuttle.IFF_NO_PI

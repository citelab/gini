"""Ethernet / ARP / IPv4 / ICMP frame helpers.

Deliberately tiny and dependency-free (stdlib only) so the same code runs inside a
slim container and in the no-Docker loopback test. Addresses are strings at the API
boundary (MAC "aa:bb:..", IP "1.2.3.4"); bytes only inside.
"""
from __future__ import annotations

import socket
import struct

# ethertypes
ETH_IP = 0x0800
ETH_ARP = 0x0806
BROADCAST = "ff:ff:ff:ff:ff:ff"
ZERO_MAC = "00:00:00:00:00:00"

# ip protocols
PROTO_ICMP = 1

# icmp types
ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0


# -- address conversion ----------------------------------------------------- #
def mac_to_bytes(m: str) -> bytes:
    return bytes(int(x, 16) for x in m.split(":"))


def mac_to_str(b: bytes) -> str:
    return ":".join("%02x" % x for x in b)


def ip_to_bytes(s: str) -> bytes:
    return socket.inet_aton(s)


def ip_to_str(b: bytes) -> str:
    return socket.inet_ntoa(b)


def is_multicast_mac(m: str) -> bool:
    """Broadcast and multicast both have the low bit of the first octet set."""
    return bool(int(m.split(":")[0], 16) & 1)


# -- internet checksum ------------------------------------------------------ #
def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2))
    s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


# -- ethernet --------------------------------------------------------------- #
def build_eth(dst: str, src: str, etype: int, payload: bytes) -> bytes:
    return mac_to_bytes(dst) + mac_to_bytes(src) + struct.pack("!H", etype) + payload


def parse_eth(frame: bytes):
    dst = mac_to_str(frame[0:6])
    src = mac_to_str(frame[6:12])
    etype = struct.unpack("!H", frame[12:14])[0]
    return dst, src, etype, frame[14:]


# -- arp -------------------------------------------------------------------- #
def build_arp(op: int, sha: str, spa: str, tha: str, tpa: str) -> bytes:
    return (struct.pack("!HHBBH", 1, ETH_IP, 6, 4, op)
            + mac_to_bytes(sha) + ip_to_bytes(spa)
            + mac_to_bytes(tha) + ip_to_bytes(tpa))


def parse_arp(p: bytes):
    op = struct.unpack("!H", p[6:8])[0]
    sha = mac_to_str(p[8:14])
    spa = ip_to_str(p[14:18])
    tha = mac_to_str(p[18:24])
    tpa = ip_to_str(p[24:28])
    return op, sha, spa, tha, tpa


# -- ipv4 ------------------------------------------------------------------- #
def build_ipv4(src: str, dst: str, proto: int, payload: bytes,
               ttl: int = 64, ident: int = 0) -> bytes:
    total = 20 + len(payload)
    fields = (0x45, 0, total, ident, 0, ttl, proto, 0,
              ip_to_bytes(src), ip_to_bytes(dst))
    hdr = struct.pack("!BBHHHBBH4s4s", *fields)
    csum = checksum(hdr)
    fields = (0x45, 0, total, ident, 0, ttl, proto, csum,
              ip_to_bytes(src), ip_to_bytes(dst))
    hdr = struct.pack("!BBHHHBBH4s4s", *fields)
    return hdr + payload


def parse_ipv4(p: bytes) -> dict:
    ihl = (p[0] & 0x0F) * 4
    total = struct.unpack("!H", p[2:4])[0]
    return {
        "ihl": ihl,
        "ttl": p[8],
        "proto": p[9],
        "src": ip_to_str(p[12:16]),
        "dst": ip_to_str(p[16:20]),
        "payload": p[ihl:total] if total <= len(p) else p[ihl:],
    }


def dec_ttl(ippkt: bytes) -> bytes | None:
    """Decrement TTL and fix the header checksum. None if TTL would hit 0."""
    b = bytearray(ippkt)
    ihl = (b[0] & 0x0F) * 4
    if b[8] <= 1:
        return None
    b[8] -= 1
    b[10] = 0
    b[11] = 0
    c = checksum(bytes(b[:ihl]))
    b[10] = c >> 8
    b[11] = c & 0xFF
    return bytes(b)


# -- icmp ------------------------------------------------------------------- #
def build_icmp(typ: int, ident: int, seq: int, data: bytes = b"gini-r0-spike!!!") -> bytes:
    hdr = struct.pack("!BBHHH", typ, 0, 0, ident, seq)
    csum = checksum(hdr + data)
    hdr = struct.pack("!BBHHH", typ, 0, csum, ident, seq)
    return hdr + data


def parse_icmp(p: bytes):
    typ, _code, _csum, ident, seq = struct.unpack("!BBHHH", p[:8])
    return typ, ident, seq, p[8:]

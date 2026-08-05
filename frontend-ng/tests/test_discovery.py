"""mDNS: gBuilder announcing the lab so GINI32 boards find it without a fixed address.

The point of these tests is that a board on a classroom network gets the *right*
answer and that a laptop running gBuilder never answers for names it does not own.
"""
import socket
import struct
import time

import pytest

from gini.services import discovery as d


def query(name: str, qtype: int, qid: int = 0x1234) -> bytes:
    """A DNS query packet, as a board's resolver would send it."""
    return (struct.pack("!HHHHHH", qid, 0, 1, 0, 0, 0)
            + d.encode_name(name) + struct.pack("!HH", qtype, d.C_IN))


def answers(resp: bytes) -> list[tuple[str, int, bytes]]:
    """Walk a response into [(name, rtype, rdata)]."""
    _, _, _, an, _, _ = struct.unpack("!HHHHHH", resp[:12])
    out, off = [], 12
    for _ in range(an):
        name, off = d.decode_name(resp, off)
        rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", resp[off:off + 10])
        off += 10
        out.append((name, rtype, resp[off:off + rdlen]))
        off += rdlen
    return out


# ------------------------------------------------------------------ wire format

@pytest.mark.parametrize("name", ["gini.local", "_gini._udp.local",
                                  "gbuilder._gini._udp.local", "a.b.c.d.e"])
def test_name_round_trips(name):
    enc = d.encode_name(name)
    got, off = d.decode_name(enc, 0)
    assert got == name and off == len(enc)


def test_compression_pointers_are_followed():
    buf = d.encode_name("gini.local") + b"\xc0\x00"
    got, off = d.decode_name(buf, len(buf) - 2)
    assert got == "gini.local" and off == len(buf)


@pytest.mark.parametrize("bad", [b"\xc0\x00", b"\x05abc", b"\xff" * 8, b""])
def test_malformed_names_raise_rather_than_hang(bad):
    """These come off the network, so a pointer loop must terminate, not spin."""
    with pytest.raises(ValueError):
        d.decode_name(bad, 0)


def test_a_response_is_not_mistaken_for_a_question():
    resp = d.build_response([], qid=1)
    assert d.parse_questions(resp) == []


# -------------------------------------------------------------------- answering

def test_service_browse_gives_a_board_everything_in_one_answer():
    adv = d.GiniAdvertiser(port=5555, address="192.168.1.42")
    resp = adv.handle_query(query(d.SERVICE, d.T_PTR))
    assert resp is not None
    _, flags, _, an, _, _ = struct.unpack("!HHHHHH", resp[:12])
    assert flags == 0x8400 and an == 4        # QR + AA, and PTR/SRV/TXT/A together

    by_type = {t: (n, rd) for n, t, rd in answers(resp)}
    _, srv = by_type[d.T_SRV]
    port = struct.unpack("!HHH", srv[:6])[2]
    target, _ = d.decode_name(srv, 6)
    assert (target, port) == ("gini.local", 5555)
    assert socket.inet_ntoa(by_type[d.T_A][1]) == "192.168.1.42"


def test_plain_hostname_lookup_resolves():
    adv = d.GiniAdvertiser(port=5555, address="10.1.2.3")
    resp = adv.handle_query(query(d.HOSTNAME, d.T_A))
    name, rtype, rdata = answers(resp)[0]
    assert (name, rtype) == ("gini.local", d.T_A)
    assert socket.inet_ntoa(rdata) == "10.1.2.3"


def test_cache_flush_bit_is_set_on_our_records():
    """Without it a board can keep a stale address after the laptop moves."""
    adv = d.GiniAdvertiser(port=5555, address="10.1.2.3")
    resp = adv.handle_query(query(d.HOSTNAME, d.T_A))
    _, off = d.decode_name(resp, 12)
    _rtype, rclass, _ttl, _rdlen = struct.unpack("!HHIH", resp[off:off + 10])
    assert rclass & d.FLUSH


@pytest.mark.parametrize("other", ["_http._tcp.local", "someones-mac.local",
                                   "printer.local", "_gini._tcp.local"])
def test_we_stay_silent_for_names_we_do_not_own(other):
    """A responder that answers for other names would poison the whole network."""
    adv = d.GiniAdvertiser(port=5555, address="10.1.2.3")
    assert adv.handle_query(query(other, d.T_ANY)) is None


@pytest.mark.parametrize("junk", [b"", b"\x00", b"\xff" * 40,
                                  struct.pack("!HHHHHH", 1, 0, 5, 0, 0, 0)])
def test_malformed_packets_do_not_crash_the_responder(junk):
    adv = d.GiniAdvertiser(port=5555, address="10.1.2.3")
    adv.handle_query(junk)          # must not raise


def test_txt_record_carries_the_port_and_board_count():
    adv = d.GiniAdvertiser(port=5555, address="10.1.2.3",
                           txt={"boards": "2", "port": "5555"})
    resp = adv.handle_query(query(d.SERVICE, d.T_PTR))
    txt = [rd for _, t, rd in answers(resp) if t == d.T_TXT][0]
    assert b"boards=2" in txt and b"port=5555" in txt


def test_advertised_port_follows_the_relay_port():
    adv = d.GiniAdvertiser(port=6001, address="10.1.2.3")
    resp = adv.handle_query(query(d.SERVICE, d.T_PTR))
    srv = [rd for _, t, rd in answers(resp) if t == d.T_SRV][0]
    assert struct.unpack("!HHH", srv[:6])[2] == 6001


def test_lan_address_is_a_dotted_quad():
    parts = d.lan_address().split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)


# ------------------------------------------------------------------- live socket

def test_a_board_can_discover_us_over_real_multicast():
    """End-to-end: join the group, browse, and read back host+port as a board would."""
    adv = d.GiniAdvertiser(port=5555)
    if not adv.start():
        pytest.skip("multicast unavailable in this environment")
    try:
        time.sleep(1.2)                      # let the initial announcements go out
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            s.bind(("", d.MDNS_PORT))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         socket.inet_aton(d.MDNS_ADDR) + socket.inet_aton("0.0.0.0"))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            s.settimeout(4.0)
            s.sendto(query(d.SERVICE, d.T_PTR), (d.MDNS_ADDR, d.MDNS_PORT))

            deadline, got = time.time() + 4, None
            while time.time() < deadline:
                try:
                    data, _ = s.recvfrom(9000)
                except socket.timeout:
                    break
                if struct.unpack("!HHHHHH", data[:12])[1] & 0x8000:   # a response
                    got = data
                    break
        finally:
            s.close()

        if got is None:
            pytest.skip("no multicast loopback in this environment")
        by_type = {t: rd for _, t, rd in answers(got)}
        srv = by_type[d.T_SRV]
        assert struct.unpack("!HHH", srv[:6])[2] == 5555
        assert socket.inet_ntoa(by_type[d.T_A]) == adv.address
    finally:
        adv.stop()
    assert not adv.running


def test_start_stop_is_idempotent():
    adv = d.GiniAdvertiser(port=5555)
    if not adv.start():
        pytest.skip("multicast unavailable")
    assert adv.start() is True               # already running: no second socket
    adv.stop()
    adv.stop()                               # must not raise
    assert not adv.running

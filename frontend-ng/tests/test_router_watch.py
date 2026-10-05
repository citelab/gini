"""Parsing what the gRouter says about its own traffic: `watch dump` and `ifstat`.

The fixtures below are VERBATIM output from a real router (gini-grouter built from this tree) on an
H1 — R1 — H2 topology, prompt and banner included: two pings forwarded both ways, a ping to the
router, one to an unrouted subnet, one with TTL 1, one dropped by a `block` module, and a TCP
attempt so ports appear. Parsing the real bytes is the point — a hand-written fixture would only
test that the parser agrees with whoever wrote it.
"""
from __future__ import annotations

from gini.domain import router_watch as RW

WATCH = "Connected to gRouter 'r1'. Type CLI commands (help, ifconfig show, route show, arp show, gpipe list). Up/Down for history, Tab to complete. Ctrl-D to exit.\nGINI-r1 $ WATCH on 10 1249750822\nW 1 1249745199 F 1 2 -1 - 10.0.1.10 10.0.2.10 1 63 84 0 0\nW 2 1249745199 F 2 1 -1 - 10.0.2.10 10.0.1.10 1 63 84 0 0\nW 3 1249746209 F 1 2 -1 - 10.0.1.10 10.0.2.10 1 63 84 0 0\nW 4 1249746210 F 2 1 -1 - 10.0.2.10 10.0.1.10 1 63 84 0 0\nW 5 1249746212 L 1 -1 -1 - 10.0.1.10 10.0.1.1 1 64 84 0 0\nW 6 1249746213 N 1 -1 -1 - 10.0.1.10 10.0.99.5 1 63 84 0 0\nW 7 1249748220 T 1 -1 -1 - 10.0.1.10 10.0.2.10 1 0 84 0 0\nW 8 1249748475 D 1 -1 0 block 10.0.1.10 10.0.2.10 1 63 84 0 0\nW 9 1249750728 F 1 2 -1 - 10.0.1.10 10.0.2.10 6 63 60 56664 8080\nW 10 1249750728 F 2 1 -1 - 10.0.2.10 10.0.1.10 6 63 40 8080 56664\nGINI-r1 $ \n"

IFSTAT = "Connected to gRouter 'r1'. Type CLI commands (help, ifconfig show, route show, arp show, gpipe list). Up/Down for history, Tab to complete. Ctrl-D to exit.\nGINI-r1 $ IFSTAT 1 tun1 10.0.1.1 21544 28 21300 26\nIFSTAT 2 tun2 10.0.2.1 21174 25 21194 25\nGINI-r1 $ \n"


def test_the_header_is_found_behind_the_console_prompt():
    d = RW.parse_watch(WATCH)
    assert d.ok and d.on is True
    assert d.next_seq == 10


def test_every_fate_is_read():
    d = RW.parse_watch(WATCH)
    assert [e.fate for e in d.events] == list("FFFFLNTDFF")


def test_a_forwarded_packet_knows_both_interfaces():
    e = RW.parse_watch(WATCH).events[0]
    assert (e.in_if, e.out_if, e.src, e.dst, e.protocol, e.ttl) == \
        (1, 2, "10.0.1.10", "10.0.2.10", "ICMP", 63)
    assert e.verdict == "forwarded"


def test_a_drop_names_the_module_that_did_it():
    e = next(e for e in RW.parse_watch(WATCH).events if e.fate == "D")
    assert (e.module, e.module_type, e.verdict) == (0, "block", "dropped by block")


def test_the_other_fates_read_as_plain_words():
    words = {e.fate: e.verdict for e in RW.parse_watch(WATCH).events}
    assert words["L"] == "to the router"
    assert words["N"] == "no route"
    assert words["T"] == "TTL expired"


def test_tcp_carries_its_ports():
    e = next(e for e in RW.parse_watch(WATCH).events if e.proto == 6)
    assert (e.sport, e.dport) == (56664, 8080)
    assert "TCP 56664→8080" in e.describe()


def test_an_old_router_without_watch_reads_as_not_ok():
    d = RW.parse_watch("GINI-r1 $ watch: command not found")
    assert d.ok is False and d.events == []


def test_skipped_packets_are_counted_not_hidden():
    d = RW.parse_watch(WATCH)
    assert d.skipped(since=0) == 0
    late = RW.WatchDump(events=[RW.PacketEvent(40, 0, "F", 1, 2, -1, "", "a", "b", 1, 1, 1)])
    assert late.skipped(since=10) == 29


def test_ifstat_reads_every_interface():
    s = RW.parse_ifstat(IFSTAT)
    assert sorted(s) == [1, 2]
    assert (s[1].name, s[1].ip, s[1].rx_bytes, s[1].tx_pkts) == ("tun1", "10.0.1.1", 21544, 26)


def test_bandwidth_is_the_delta_over_time_in_bits():
    a = {1: RW.IfStat(1, "tun1", "x", 1000, 1, 0, 0)}
    b = {1: RW.IfStat(1, "tun1", "x", 126000, 90, 25000, 20)}
    r = RW.bandwidth(a, b, 2.0)[1]
    assert (r.rx_bps, r.tx_bps) == (500_000.0, 100_000.0)


def test_a_restarted_router_is_not_a_negative_rate():
    a = {1: RW.IfStat(1, "tun1", "x", 9_000_000, 1, 9_000_000, 1)}
    b = {1: RW.IfStat(1, "tun1", "x", 500, 1, 500, 1)}
    r = RW.bandwidth(a, b, 1.0)[1]
    assert (r.rx_bps, r.tx_bps) == (0.0, 0.0)


def test_an_interface_new_in_this_reading_has_no_rate_yet():
    b = {2: RW.IfStat(2, "tun2", "x", 5, 1, 5, 1)}
    assert RW.bandwidth({}, b, 1.0) == {}


def test_rates_read_in_link_units():
    assert RW.human_bps(0) == "0 b/s"
    assert RW.human_bps(12_400) == "12.4 kb/s"
    assert RW.human_bps(3_200_000) == "3.20 Mb/s"


# -- the persistent console session: replies split at the prompt ----------------------------------

def test_the_banner_is_not_a_reply_and_the_first_prompt_means_ready():
    f = RW.ConsoleFeed()
    assert f.feed("Connected to gRouter 'r1'. Type CLI commands ...\n") == []
    assert not f.ready
    assert f.feed("GINI-r1 $ ") == []
    assert f.ready


def test_a_reply_arriving_in_pieces_comes_out_whole_at_its_prompt():
    f = RW.ConsoleFeed()
    f.feed("banner\nGINI-r1 $ ")
    assert f.feed("WATCH on 3 100\nW 1 100 F 1 2 -1 - 10.0.1.10 10.") == []
    out = f.feed("0.2.10 1 63 84 0 0\nGINI-r1 $ ")
    assert len(out) == 1
    d = RW.parse_watch(out[0])
    assert d.ok and d.events[0].dst == "10.0.2.10"


def test_two_replies_in_one_chunk_are_two_replies():
    f = RW.ConsoleFeed()
    f.feed("GINI-r1 $ ")
    out = f.feed("IFSTAT 1 tun1 10.0.1.1 5 1 6 1\nGINI-r1 $ WATCH off 0 9\nGINI-r1 $ ")
    assert len(out) == 2
    assert RW.parse_ifstat(out[0])[1].tx_bytes == 6
    assert RW.parse_watch(out[1]).on is False


def test_the_real_captured_session_splits_into_its_replies():
    f = RW.ConsoleFeed()
    out = f.feed(WATCH)            # banner + prompt + dump + trailing prompt, verbatim
    assert len(out) == 1 and RW.parse_watch(out[0]).next_seq == 10


# -- the route form's command -------------------------------------------------------------------

from gini.domain import routetable as RT      # noqa: E402


def test_a_per_host_route_is_a_slash_32_and_says_so():
    r = RT.route_add_command("10.0.3.12", 32, "tun2", "10.0.2.1")
    assert r.ok
    assert r.command == "route add -dev tun2 -net 10.0.3.12 -netmask 255.255.255.255 -gw 10.0.2.1"
    assert "per-host" in r.note


def test_a_direct_route_has_no_gw():
    assert RT.route_add_command("10.0.5.0", 24, "tun1").command == \
        "route add -dev tun1 -net 10.0.5.0 -netmask 255.255.255.0"


def test_the_destination_is_reduced_to_its_network_and_the_note_says_why():
    r = RT.route_add_command("10.0.3.12", 24, "tun2")
    assert "-net 10.0.3.0 " in r.command
    assert "network 10.0.3.0" in r.note


def test_mistakes_are_named_before_the_router_sees_them():
    r = RT.route_add_command("10.0.3", 40, "", "10.0.2")
    assert not r.ok and r.command == ""
    assert len(r.errors) == 4


def test_prefix_to_netmask():
    assert [RT.prefix_to_netmask(p) for p in (0, 8, 24, 30, 32)] == \
        ["0.0.0.0", "255.0.0.0", "255.255.255.0", "255.255.255.252", "255.255.255.255"]


def test_delete_uses_the_route_index():
    e = RT.RouteEntry(3, "10.0.3.12", "255.255.255.255", "10.0.2.1", "tun2", "S")
    assert RT.route_del_command(e) == "route del 3"


def test_the_unit_follows_the_rounded_rate():
    """A rate over a poll is never exactly round: 999,999.6 b/s must read 1.00 Mb/s."""
    assert RW.human_bps(999_999.6) == "1.00 Mb/s"
    assert RW.human_bps(999.6) == "1.0 kb/s"



# -- ARP and packets the router itself sends ------------------------------------------------------
#
# VERBATIM from a real router: a cold ping H1 → H2 (both ARP caches flushed), a ping to the router,
# and a ping with TTL 1. The order is the lesson — the router answers H1's ARP, forwards the echo
# request, then has to ARP for H2 before the reply can come back; and the router's OWN echo reply
# and TTL-exceeded message, which used to be invisible, are there as packets it sent.

COLD_PING = "Connected to gRouter 'r1'. Type CLI commands (help, ifconfig show, route show, arp show, gpipe list). Up/Down for history, Tab to complete. Ctrl-D to exit.\nGINI-r1 $ WATCH on 10 1253054109\nW 1 1253054010 A 1 -1 -1 answered 10.0.1.10 10.0.1.1 2054 0 28 1 0\nW 2 1253054010 S -1 1 -1 - 10.0.1.1 10.0.1.10 2054 0 28 2 0\nW 3 1253054010 F 1 2 -1 - 10.0.1.10 10.0.2.10 1 63 84 8 0\nW 4 1253054010 S -1 2 -1 - 10.0.2.1 10.0.2.10 2054 0 28 1 0\nW 5 1253054010 A 2 -1 -1 learned 10.0.2.10 10.0.2.1 2054 0 28 2 0\nW 6 1253054011 F 2 1 -1 - 10.0.2.10 10.0.1.10 1 63 84 0 0\nW 7 1253054011 L 1 -1 -1 - 10.0.1.10 10.0.1.1 1 64 84 8 0\nW 8 1253054011 S -1 1 -1 - 10.0.1.1 10.0.1.10 1 64 84 0 0\nW 9 1253054012 T 1 -1 -1 - 10.0.1.10 10.0.2.10 1 0 84 8 0\nW 10 1253054012 S -1 1 -1 - 10.0.1.1 10.0.1.10 1 64 56 11 0\nGINI-r1 $ \n"


def test_the_whole_cold_ping_is_in_order():
    d = RW.parse_watch(COLD_PING)
    assert [e.fate for e in d.events] == list("ASFSAFLSTS")


def test_an_arriving_arp_says_what_the_router_did_with_it():
    a = [e for e in RW.parse_watch(COLD_PING).events if e.fate == "A"]
    assert [x.verdict for x in a] == ["ARP answered", "ARP learned"]
    assert a[0].what() == "ARP who-has 10.0.1.1? (asked by 10.0.1.10)"
    assert a[0].is_arp and a[0].protocol == "ARP"


def test_the_router_resolving_the_next_hop_is_visible():
    sent_arp = [e for e in RW.parse_watch(COLD_PING).events if e.fate == "S" and e.is_arp]
    req = next(e for e in sent_arp if e.sport == 1)
    assert (req.out_if, req.dst) == (2, "10.0.2.10")
    assert req.what().startswith("ARP who-has 10.0.2.10?")


def test_the_routers_own_icmp_is_named():
    sent_ip = [e for e in RW.parse_watch(COLD_PING).events if e.fate == "S" and not e.is_arp]
    assert ["echo reply" in e.what() for e in sent_ip] == [True, False]
    assert "TTL exceeded" in sent_ip[1].what()
    assert all(e.in_if == -1 for e in sent_ip), "a packet the router sends did not arrive anywhere"
    assert sent_ip[0].verdict == "sent by the router"


def test_icmp_carries_its_type_on_forwarded_packets_too():
    fwd = [e for e in RW.parse_watch(COLD_PING).events if e.fate == "F"]
    assert "echo request" in fwd[0].what() and "echo reply" in fwd[1].what()

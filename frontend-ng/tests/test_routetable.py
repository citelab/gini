"""Parsing the gRouter route/arp CLI dumps + the OpenFlow table-stats line."""
from gini.domain.routetable import parse_arp, parse_routes
from gini.domain.flowtable import parse_table_stats

ROUTE = """
=================================================================
      R O U T E  T A B L E
-----------------------------------------------------------------
Index\tNetwork\t\tNetmask\t\tNexthop\t\tInterface
[0]\t10.0.1.0\t255.255.255.0\t0.0.0.0\t\ttun1
[1]\t10.0.2.0\t255.255.255.0\t10.0.1.2\t\ttun2
-----------------------------------------------------------------
      2 number of routes found.
"""

ARP = """
IP address        MAC address
10.0.1.2          02:00:fe:00:00:02
10.0.1.3          02:00:fe:00:00:03
"""

TABLE_STATS = """
=========
Table 0
=========
Name: Flow Switch
Maximum number of supported entries: 100
Number of active entries: 3
Number of packets looked up in tables: 128
Number of packets that hit tables: 120
"""


def test_parse_routes():
    rs = parse_routes(ROUTE)
    assert [r.index for r in rs] == [0, 1]
    assert rs[0].network == "10.0.1.0" and rs[0].iface == "tun1"
    assert rs[0].direct and rs[0].nexthop_str() == "direct (on-link)"
    assert not rs[1].direct and rs[1].nexthop == "10.0.1.2"


def test_parse_arp():
    a = parse_arp(ARP)
    assert len(a) == 2
    assert a[0].ip == "10.0.1.2" and a[0].mac == "02:00:fe:00:00:02"


def test_parse_table_stats():
    s = parse_table_stats(TABLE_STATS)
    assert s["active"] == 3 and s["lookups"] == 128 and s["matched"] == 120

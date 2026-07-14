"""Parsing the gRouter OpenFlow CLI dumps into flow-table rows for the SDN dashboard."""
from gini.domain.flowtable import flows, parse

ENTRY_ALL = """
=========
Entry 0
=========

Match:
\tWildcards: 3820FF
\tInput port: 1
\tEthernet frame type: IP
\tIP source address: 10.0.0.1
\tIP destination address: 10.0.0.2
\tIP protocol: TCP
\tTCP destination port: 80
Cookie: 0
Priority: 100
Entry flags: 0
Actions:
\tAction 0:
\t\tType: OFPAT_OUTPUT
\t\tOutput port: 2

=========
Entry 1
=========

Match:
\tWildcards: 3FFFFF
\tEthernet frame type: ARP
Cookie: 0
Priority: 0
Actions:
\tAction 0:
\t\tType: OFPAT_OUTPUT
\t\tOutput port: OFPP_FLOOD

=========
Entry 2
=========

Entry inactive
"""

STATS_ALL = """
=========
Entry 0
=========

Table ID: 0
Match:
\tInput port: 1
\tIP source address: 10.0.0.1
Duration (seconds): 42
Cookie: 0
Packet count: 128
Byte count: 8192
"""


def test_parse_entry_all_active_only():
    fs = parse(ENTRY_ALL)
    assert [f.index for f in fs] == [0, 1]        # inactive entry 2 dropped
    f0 = fs[0]
    assert f0.match["Input port"] == "1"
    assert f0.match["IP destination address"] == "10.0.0.2"
    assert f0.actions == ["output:2"]
    assert f0.priority == 100
    assert fs[1].actions == ["flood"]             # OFPP_FLOOD -> flood


def test_match_and_action_summaries():
    f0 = parse(ENTRY_ALL)[0]
    s = f0.match_summary()
    assert "in:1" in s and "10.0.0.1→10.0.0.2" in s and "tcp" in s and "dport 80" in s
    assert f0.action_summary() == "output:2"
    assert parse(ENTRY_ALL)[1].match_summary().endswith("arp") or "arp" in \
        parse(ENTRY_ALL)[1].match_summary()


def test_flows_merges_counters_from_stats():
    merged = flows(ENTRY_ALL, STATS_ALL)
    f0 = next(f for f in merged if f.index == 0)
    assert f0.actions == ["output:2"]             # from entry dump
    assert f0.packets == 128 and f0.bytes == 8192 # from stats dump
    assert f0.duration == 42


def test_empty_and_no_flows():
    assert parse("") == []
    assert parse("=========\nEntry 0\n=========\nEntry inactive\n") == []

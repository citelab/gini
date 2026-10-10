"""The Multicast HUD reads what mcast_tree.lua publishes, alongside whatever else a router runs.

The forwarder now publishes a per-source line for each tree (`S <src> G <grp> RPF ...`) under its
per-group rows, and a router running RIP too answers `gpipe cp status` with both modules'
snapshots. The HUD must take its rows from the `G` lines only -- an `S` line also contains
"G 239..." and must not be read as a group row.
"""
from gini.domain.mcast import McastTracker, parse_cp_status

STATUS = """RIP v1 2
N 10.0.1.0/24 COST 0 connected
N 10.0.4.0/24 COST 5 via 10.0.3.1
MCAST v1 1
G 239.1.1.1 IF 2,3 CP 2:38,3:40
S 10.0.4.10 G 239.1.1.1 RPF 1 OIF 2,3 PRUNED - DROPS 3 COPIES 78
S 10.0.4.11 G 239.1.1.1 RPF 1 OIF - PRUNED 2 DROPS 0 COPIES 0
"""


def test_only_group_rows_are_read_from_a_mixed_status():
    rows = parse_cp_status(STATUS, "R2")
    assert len(rows) == 1
    r = rows[0]
    assert (r.router, r.group, r.ifaces, r.copies) == ("R2", "239.1.1.1", [2, 3], {2: 38, 3: 40})


def test_a_router_with_sources_but_no_branches_shows_no_group():
    """A transit router whose branches are all pruned publishes S lines and no G line: it is not
    on the tree, and the HUD shows it as such (a leave, if it was)."""
    t = McastTracker()
    t.ingest(parse_cp_status(STATUS, "R2"), tnow=1.0)
    pruned = "MCAST v1 0\nS 10.0.4.10 G 239.1.1.1 RPF 1 OIF - PRUNED 2,3 DROPS 3 COPIES 78\n"
    t.ingest(parse_cp_status(pruned, "R2"), tnow=2.0, polled={"R2"})
    assert t.groups() == []
    assert [e.label() for e in t.recent_events(2)] == ["R2 if2 leave 239.1.1.1",
                                                       "R2 if3 leave 239.1.1.1"]

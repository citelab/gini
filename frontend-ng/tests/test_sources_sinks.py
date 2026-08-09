"""Sources & Sinks — the rider model, attach edges, and the attach grammar.

A rider (Source or Sink) has no container of its own: it runs ON a donor element and hangs off it
by a dotted *attach* edge, never a network cable. This locks the domain foundation: the registry
entries, the Topology attach edge (distinct kind, backward-compatible serialization), and the
grammar that steers riders onto compatible donors and away from links.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import connection_rules as CR
from gini.domain import devices as D
from gini.domain.topology import Link, Topology


# -- registry: riders exist, are typed, and declare their donors ------------- #
def test_riders_are_registered_with_role_and_donors():
    ping = D.get("ping_probe")
    assert ping.rider and ping.role == "source"
    assert ping.category == D.Category.SOURCE
    assert ping.backend_kind is None                 # a rider spawns NO container
    assert "host" in ping.attaches_to and "router" in ping.attaches_to

    pcap = D.get("packet_view")
    assert pcap.rider and pcap.role == "sink"
    assert pcap.category == D.Category.SINK
    assert "ovs" in pcap.attaches_to                 # sniff a switch too


def test_source_and_sink_sections_appear_on_the_palette():
    sections = D.by_category()
    assert D.Category.SOURCE in sections and D.Category.SINK in sections
    src_keys = {d.key for d in sections[D.Category.SOURCE]}
    assert {"ping_probe", "http_probe"} <= src_keys
    assert "packet_view" in {d.key for d in sections[D.Category.SINK]}


# -- topology: attach edges are a distinct, non-network kind ----------------- #
def test_attach_edge_is_distinct_from_a_network_link():
    t = Topology()
    m = t.add_device("host", "M1")
    ping = t.add_device("ping_probe", "PING1")
    net = t.add_device("switch", "S1")

    cable = t.add_link(m.id, net.id)
    mount = t.add_attach(ping.id, m.id)

    assert cable.kind == "link" and mount.kind == "attach"
    assert t.net_links() == [cable]                  # the compiler sees only the cable
    assert t.donor_of(ping.id).id == m.id            # the rider knows its donor
    assert [r.id for r in t.riders_on(m.id)] == [ping.id]


def test_attach_edges_survive_a_save_load_roundtrip_and_legacy_links_default_to_link():
    t = Topology()
    m = t.add_device("host", "M1")
    ping = t.add_device("ping_probe", "PING1")
    t.add_attach(ping.id, m.id)
    back = Topology.from_dict(t.to_dict())
    assert any(l.kind == "attach" for l in back.links.values())

    # a link dict written before `kind` existed must still load, defaulting to a network link
    legacy = Link(**{"id": "link-9", "source_id": m.id, "target_id": m.id, "label": ""})
    assert legacy.kind == "link"


# -- grammar: riders mount on compatible donors, and never wire as links ----- #
def test_attach_grammar_accepts_compatible_donors_and_rejects_the_rest():
    assert CR.is_rider("ping_probe") and not CR.is_rider("host")
    assert "host" in CR.attach_targets("ping_probe")
    assert "ping_probe" in CR.riders_for("host")
    assert "packet_view" in CR.riders_for("ovs")

    assert CR.attach_blocked("ping_probe", "host") is None
    assert CR.attach_blocked("ping_probe", "database") is not None   # not a valid donor
    assert CR.attach_blocked("host", "router") is not None           # not a rider at all
    assert CR.attach_blocked("ping_probe", "packet_view") is not None  # donor can't be a rider


def test_a_rider_is_steered_to_attach_not_to_a_network_link():
    assert CR.link_blocked("ping_probe", "host") is not None     # "attach it, don't wire it"
    assert CR.link_blocked("host", "packet_view") is not None
    assert CR.link_blocked("host", "switch") is None             # ordinary cable still fine


# -- compiler: a rider spawns NO container and its attach edge is never wired - #
def test_compiler_drops_riders_and_their_attach_edges():
    from gini.services.compiler import RIDERS, RuntimeCompiler, _role
    assert _role("ping_probe") == "rider" and _role("host") == "machine"
    assert {"ping_probe", "http_probe", "packet_view"} <= RIDERS

    t = Topology()
    m1 = t.add_device("host", "M1")
    m2 = t.add_device("host", "M2")
    sw = t.add_device("switch", "S1")
    t.add_link(m1.id, sw.id)
    t.add_link(m2.id, sw.id)
    t.add_attach(t.add_device("ping_probe", "PING1").id, m1.id)
    t.add_attach(t.add_device("packet_view", "PCAP1").id, m1.id)

    cfg = RuntimeCompiler().compile(t)
    names = {x["name"] for x in cfg.to_runtime(docker=True).get("machines", [])}
    assert names == {"m1", "m2"}                                  # the two real machines, no riders
    assert sum("rider attach" in n for n in cfg.notes) == 2       # both mounts skipped, with a note

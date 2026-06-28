"""A Hub is a Layer-1 repeater (flood everything, never learn) — the foil to the
learning Switch. These tests pin the behavioural difference and the compiler wiring."""
from gini.domain.topology import Topology
from gini.runtime.frame import build_eth
from gini.runtime.switch import Hub, LearningSwitch, make_switch
from gini.services.compiler import RuntimeCompiler

M1, M2, M3 = "02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:00:03"
FRAME_FROM_M2 = build_eth(M1, M2, 0x0800, b"learn")   # src M2 — lets a switch learn M2
UNICAST_TO_M2 = build_eth(M2, M1, 0x0800, b"req")     # M1 -> M2 directed unicast


class FakePort:
    def __init__(self, name):
        self.name = name
        self.sent = []

    def send(self, frame):
        self.sent.append(frame)


def _node(hub: bool, n=3):
    """Build a node via the factory (empty ports = no sockets, no control thread) and
    swap in recording fake ports so we can inspect what gets forwarded where."""
    node = make_switch({"name": "x", "ports": [], "hub": hub})
    node.ports = [FakePort(f"p{i}") for i in range(n)]
    return node


def test_factory_picks_the_right_node():
    assert isinstance(make_switch({"name": "x", "ports": [], "hub": True}), Hub)
    sw = make_switch({"name": "x", "ports": [], "hub": False})
    assert isinstance(sw, LearningSwitch) and not isinstance(sw, Hub)


def test_hub_floods_every_frame_and_never_learns():
    hub = _node(hub=True)
    hub.handle(hub.ports[0], UNICAST_TO_M2)          # one unicast in on p0
    assert UNICAST_TO_M2 in hub.ports[1].sent        # repeated out EVERY other port
    assert UNICAST_TO_M2 in hub.ports[2].sent
    assert hub.ports[0].sent == []                   # but never back out the ingress port
    assert hub.table == {}                           # a hub has no MAC table


def test_switch_filters_the_same_unicast_a_hub_floods():
    # the teaching contrast: after the destination is learned, a switch delivers the
    # unicast only to that port; a hub still repeats it to everyone.
    sw = _node(hub=False)
    sw.handle(sw.ports[1], FRAME_FROM_M2)            # switch learns M2 is on p1
    sw.handle(sw.ports[0], UNICAST_TO_M2)            # M1 -> M2
    assert UNICAST_TO_M2 in sw.ports[1].sent         # delivered to the learned port only
    assert UNICAST_TO_M2 not in sw.ports[2].sent     # filtered — p2 never sees it

    hub = _node(hub=True)
    hub.handle(hub.ports[1], FRAME_FROM_M2)
    hub.handle(hub.ports[0], UNICAST_TO_M2)
    assert UNICAST_TO_M2 in hub.ports[2].sent        # the hub repeats it to p2 regardless


def test_hub_console_has_no_mac_table():
    hub = _node(hub=True)
    assert "no MAC table" in hub._control("mactable")
    sw = _node(hub=False)
    assert "no MAC table" not in sw._control("help")


def _line(t, a, b):
    t.add_link(a.id, b.id)


def test_compiler_marks_hub_distinct_from_switch():
    t = Topology("net")
    h1 = t.add_device("host"); hub = t.add_device("hub"); h2 = t.add_device("host")
    _line(t, h1, hub); _line(t, hub, h2)
    cfg = RuntimeCompiler().compile(t)
    assert len(cfg.switches) == 1 and cfg.switches[0].hub is True
    # and a plain switch is not a hub
    t2 = Topology("net")
    a = t2.add_device("host"); sw = t2.add_device("switch"); b = t2.add_device("host")
    _line(t2, a, sw); _line(t2, sw, b)
    assert RuntimeCompiler().compile(t2).switches[0].hub is False


def test_runtime_config_carries_the_hub_flag():
    t = Topology("net")
    h1 = t.add_device("host"); hub = t.add_device("hub"); h2 = t.add_device("host")
    _line(t, h1, hub); _line(t, hub, h2)
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    assert rt["switches"][0]["hub"] is True          # the fabric supervisor reads this

"""address_map: per-device IP/MAC/subnet/gateway derived from the compiler."""
from gini.domain.topology import Topology
from gini.services.compiler import address_map


def lab() -> Topology:
    t = Topology("lab")
    r1 = t.add_device("router")
    s1 = t.add_device("switch")
    s2 = t.add_device("switch")
    h1 = t.add_device("host")
    h2 = t.add_device("host")
    for a, b in [(h1.id, s1.id), (r1.id, s1.id), (r1.id, s2.id), (h2.id, s2.id)]:
        t.add_link(a, b)
    return t


def test_machine_addressing():
    addr = address_map(lab())
    m1 = addr["M1"]
    assert m1["role"] == "machine"
    iface = m1["interfaces"][0]
    assert iface["ip"].endswith("/24")
    assert iface["subnet"].endswith(".0/24")
    assert iface["gateway"]                      # the router on its segment
    assert iface["mac"].startswith("02:")


def test_router_has_two_interfaces_on_two_subnets():
    addr = address_map(lab())
    r1 = addr["R1"]
    assert r1["role"] == "router"
    subnets = {i["subnet"] for i in r1["interfaces"]}
    assert len(r1["interfaces"]) == 2 and len(subnets) == 2


def test_switch_has_no_ip():
    addr = address_map(lab())
    s1 = addr["S1"]
    assert s1["role"] == "switch"
    assert s1["interfaces"] == []
    assert s1["ports"] >= 2

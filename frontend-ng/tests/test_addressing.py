"""address_map: per-device IP/MAC/subnet/gateway derived from the compiler, and the
/etc/hosts block built from it."""
from gini.domain.topology import Topology
from gini.services.compiler import address_map, overlay_host_lines, overlay_hosts


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


# -- naming every address ------------------------------------------------------ #
def _lines():
    return overlay_host_lines(address_map(lab()))


def test_no_address_in_the_topology_is_left_without_a_name():
    """THE invariant that was missing. The writer bound one address per DEVICE, and a router has
    one per SUBNET — so on this two-subnet lab, 10.0.2.1 reached /etc/hosts under no name at all.
    Asserted against address_map rather than literal IPs, so it still holds if allocation moves.
    """
    addr = address_map(lab())
    every_ip = {i["ip"].split("/")[0] for d in addr.values() for i in d["interfaces"]}
    named = {ip for ip, _ in _lines()}
    assert every_ip and named == every_ip


def test_a_routers_far_side_is_nameable_not_just_its_first_interface():
    addr = address_map(lab())
    by_ip = dict(_lines())
    for i, itf in enumerate(addr["R1"]["interfaces"]):
        ip = itf["ip"].split("/")[0]
        assert f"R1-eth{i}" in by_ip[ip], f"{ip} does not answer to R1-eth{i}"


def test_every_gateway_a_machine_is_given_has_a_name():
    """The consequence students actually hit: each host's default gateway is a router interface,
    and on every subnet but the first that address used to be anonymous."""
    addr = address_map(lab())
    named = {ip for ip, _ in _lines()}
    gateways = {i["gateway"] for d in addr.values() for i in d["interfaces"] if i.get("gateway")}
    assert len(gateways) >= 2 and gateways <= named


def test_the_device_name_still_resolves_to_its_first_interface():
    """/etc/hosts resolution takes the FIRST matching line, so `ping R1` must answer exactly as it
    did before this change — the new addresses are additions, not a re-pointing."""
    addr = address_map(lab())
    for name in ("R1", "M1", "M2"):
        first = next(ip for ip, names in _lines() if name in names)
        assert first == addr[name]["interfaces"][0]["ip"].split("/")[0]
        assert first == overlay_hosts(addr)[name]      # and agrees with the single-address map


def test_a_switch_contributes_no_lines():
    assert not [ip for ip, names in _lines() if any(n.startswith("S1") for n in names)]


# -- who is asking -------------------------------------------------------------- #
def _resolves(viewer, name):
    """The address `name` resolves to inside `viewer` — first matching line, as /etc/hosts does."""
    return next(ip for ip, names in overlay_host_lines(address_map(lab()), viewer=viewer)
                if name in names)


def test_a_router_answers_with_the_address_on_your_own_segment():
    """A host asking about its own gateway should not be handed the far side of the router.

    Asserted as "the answer IS this machine's gateway" rather than against a literal IP, because
    that is the property that matters — and it is the one a student checks by hand.
    """
    addr = address_map(lab())
    for machine in ("M1", "M2"):
        gateway = addr[machine]["interfaces"][0]["gateway"]
        assert _resolves(machine, "R1") == gateway, machine
    assert _resolves("M1", "R1") != _resolves("M2", "R1")   # and they really are different sides


def test_every_address_is_still_named_whoever_is_asking():
    """Subnet-awareness reorders lines; it must never drop one."""
    addr = address_map(lab())
    every_ip = {i["ip"].split("/")[0] for d in addr.values() for i in d["interfaces"]}
    for viewer in (None, "M1", "M2", "R1", "S1"):
        assert {ip for ip, _ in overlay_host_lines(addr, viewer=viewer)} == every_ip, viewer


def test_a_viewer_sharing_no_subnet_keeps_the_first_interface():
    """Nothing may become unresolvable just because the asker is somewhere unrelated."""
    addr = address_map(lab())
    assert _resolves("nobody-here", "R1") == addr["R1"]["interfaces"][0]["ip"].split("/")[0]


def test_a_single_homed_machine_reads_the_same_from_anywhere():
    for viewer in ("M1", "M2", None):
        assert _resolves(viewer, "M1") == _resolves(None, "M1")

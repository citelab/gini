"""Phase 2 NFV: a VNF compiles to an inline forwarding container running a network function;
neighbours route through it; the rule -> iptables translation is correct."""
from gini.domain.topology import Topology
from gini.runtime.shuttle import vnf_commands
from gini.services.compiler import RuntimeCompiler, _role


def _rt(t):
    return RuntimeCompiler().compile(t).to_runtime(docker=True)


def _chain():
    t = Topology("c")
    h = t.add_device("host", name="M1")
    fw = t.add_device("vnf", name="FW1")
    fw.properties["Kind"] = "firewall"
    fw.properties["Rules"] = "deny 10.0.3.0/24"
    r = t.add_device("router", name="R1")
    t.add_link(h.id, fw.id)
    t.add_link(fw.id, r.id)
    return t


def test_vnf_role():
    assert _role("vnf") == "vnf"


def test_vnf_is_a_two_interface_forwarding_container():
    m = next(m for m in _rt(_chain())["machines"] if m["name"] == "fw1")
    assert m["forward"] and m["nf"] == "firewall" and m["nf_rules"] == "deny 10.0.3.0/24"
    assert len(m["ifaces"]) == 2          # one interface per side (inline in the path)
    assert m["gw"]                        # onward next hop toward the router


def test_host_routes_through_the_vnf():
    rt = _rt(_chain())
    host = next(m for m in rt["machines"] if m["name"] == "m1")
    fwm = next(m for m in rt["machines"] if m["name"] == "fw1")
    fw_ips = {i["ip"].split("/")[0] for i in fwm["ifaces"]}
    assert host["gw"] in fw_ips           # the host default-routes through the VNF


def test_vnf_commands_firewall_and_block():
    fw = vnf_commands("firewall", "deny 10.0.3.0/24\ndeny from 10.0.9.0/24", gw="10.0.2.1")
    assert ["ip", "route", "replace", "10.0.0.0/8", "via", "10.0.2.1"] in fw
    assert ["iptables", "-A", "FORWARD", "-d", "10.0.3.0/24", "-j", "DROP"] in fw
    assert ["iptables", "-A", "FORWARD", "-s", "10.0.9.0/24", "-j", "DROP"] in fw
    blk = vnf_commands("block", "10.0.3.5", gw=None)
    assert ["iptables", "-A", "FORWARD", "-d", "10.0.3.5", "-j", "DROP"] in blk
    assert vnf_commands("ids", "", None) == []     # illustrative: forward-only, no rules yet


def test_vnf_is_not_a_container_role_service():
    # a VNF is a machine-class forwarding container, not a managed 'service'
    m = [x["name"] for x in _rt(_chain())["machines"]]
    assert "fw1" in m

"""The Docker project for an SDN topology: POX controller + gRouter-in-OpenFlow OVS.

Topology:  OFC1 (controller) ── OVS1 ── {M1, M2}
  * controller -> a `gini-pox` container (Python 3 POX) on :6633
  * ovs        -> a `gini-grouter` container launched with --openflow, pointed at OFC1
  * machines   -> their own containers, attached to the OVS data ports
"""
import json
import re

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _svc
from gini.services.orchestrator import GROUTER_IMAGE, POX_IMAGE, _compose


def lab() -> Topology:
    t = Topology("sdn")
    ofc = t.add_device("controller"); ovs = t.add_device("ovs")
    m1 = t.add_device("host"); m2 = t.add_device("host")
    t.add_link(ofc.id, ovs.id)
    t.add_link(ovs.id, m1.id); t.add_link(ovs.id, m2.id)
    return t, ofc, ovs, m1, m2


def test_controller_runs_as_pox_container():
    t, ofc, _ovs, *_ = lab()
    compose = _compose(RuntimeCompiler().compile(t))
    assert f"image: {POX_IMAGE}" in compose
    assert f"\n  {_svc(ofc.name)}:\n" in compose
    assert "POX_APP:" in compose and "POX_PORT:" in compose


def test_ovs_runs_grouter_in_openflow_pointed_at_controller():
    t, ofc, ovs, *_ = lab()
    compose = _compose(RuntimeCompiler().compile(t))
    # OVS uses the gRouter image and declares the controller via env
    assert f"\n  {_svc(ovs.name)}:\n" in compose
    assert f"GINI_OF_CONTROLLER: '{_svc(ofc.name)}:6633'" in compose
    # its ROUTER_CONFIG carries the openflow marker and data ports (no routes)
    m = re.search(rf"{_svc(ovs.name)}:.*?ROUTER_CONFIG: '(\{{.*?\}})'", compose, re.S)
    cfg = json.loads(m.group(1))
    assert cfg["openflow"]["port"] == 6633
    assert len(cfg["ifaces"]) == 2                     # two host-facing ports
    assert "routes" not in cfg                          # an L2 switch has no routes


def test_ovs_depends_on_controller():
    t, ofc, ovs, *_ = lab()
    compose = _compose(RuntimeCompiler().compile(t))
    assert f"depends_on: [{_svc(ofc.name)}]" in compose


def test_machines_attach_to_the_ovs():
    t, _ofc, ovs, m1, m2 = lab()
    compose = _compose(RuntimeCompiler().compile(t))
    nodes = {}
    for mm in re.finditer(r"NODE_CONFIG: '(\{.*?\})'\n", compose):
        c = json.loads(mm.group(1)); nodes[c["name"]] = c
    for h in (m1, m2):
        peers = {i["port"]["peer_host"] for i in nodes[_svc(h.name)]["ifaces"]}
        assert _svc(ovs.name) in peers                  # the host talks to the OVS

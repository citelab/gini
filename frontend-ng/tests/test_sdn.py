"""SDN: controller as control plane, OVS as the gRouter in OpenFlow mode."""
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, validate


def _sdn_topo():
    """OFC → OVS → three hosts, one subnet (the simplest legit SDN)."""
    t = Topology("sdn")
    ofc = t.add_device("controller")
    ovs = t.add_device("ovs")
    hosts = [t.add_device("host") for _ in range(3)]
    t.add_link(ofc.id, ovs.id)                     # control channel (management)
    for h in hosts:
        t.add_link(ovs.id, h.id)                   # data ports
    return t, ofc, ovs, hosts


def test_controller_is_not_a_data_host():
    t, ofc, _ovs, _h = _sdn_topo()
    cfg = RuntimeCompiler().compile(t)
    names = {m.name for m in cfg.machines}
    assert ofc.name not in names                   # controller is control plane, not a host
    assert len(cfg.machines) == 3                  # only the three real hosts


def test_ovs_is_its_own_openflow_container_with_controller():
    t, ofc, ovs, _h = _sdn_topo()
    cfg = RuntimeCompiler().compile(t)
    assert [s.name for s in cfg.switches] == []    # NOT a plain fabric switch
    assert len(cfg.ovs_switches) == 1
    o = cfg.ovs_switches[0]
    assert o.name == ovs.name
    assert o.controller is not None                # knows its controller (a service name)
    assert len(o.eps) == 3                          # three data ports to the hosts


def test_controller_spec_programs_the_ovs():
    t, _ofc, ovs, _h = _sdn_topo()
    cfg = RuntimeCompiler().compile(t)
    assert len(cfg.controllers) == 1
    c = cfg.controllers[0]
    assert c.port == 6633 and "gini.samples.switch" in c.app
    from gini.services.compiler import _svc
    assert _svc(ovs.name) in c.switches            # it programs the OVS


def test_sdn_hosts_share_a_subnet_and_need_no_gateway():
    t, _ofc, _ovs, _h = _sdn_topo()
    cfg = RuntimeCompiler().compile(t)
    subnets = {m.ifaces[0].ip.split(".")[2] for m in cfg.machines}
    assert len(subnets) == 1                        # one L2 domain behind the OVS
    assert all(m.gw is None for m in cfg.machines)  # flat L2, no router
    # and the lint does NOT nag them about the missing gateway
    warns = [i for i in validate(t) if i["level"] == "warn" and "no gateway" in i["message"]]
    assert warns == []


def test_lint_flags_ovs_without_controller_and_controller_without_ovs():
    # OVS alone (no controller) -> warn (fail-secure: it drops everything)
    t1 = Topology("a"); ovs = t1.add_device("ovs"); h = t1.add_device("host")
    t1.add_link(ovs.id, h.id)
    warns1 = [i for i in validate(t1) if i["device"] == ovs.name and i["level"] == "warn"]
    assert any("no controller" in i["message"] for i in warns1)

    # controller alone (no OVS) -> warn advisory
    t2 = Topology("b"); ofc = t2.add_device("controller"); s = t2.add_device("switch")
    t2.add_link(ofc.id, s.id)                       # attached to the wrong thing
    warns = [i for i in validate(t2) if i["device"] == ofc.name and i["level"] == "warn"]
    assert any("isn't programming" in i["message"] for i in warns)


def test_controller_app_choice_flows_through():
    from gini.domain.devices import REGISTRY
    from gini.services.orchestrator import _compose
    # the controller exposes the App personality as a dropdown with sane choices
    ctype = REGISTRY.get("controller")
    assert "App" in ctype.property_choices
    assert "forwarding.hub" in ctype.property_choices["App"]

    t, ofc, _ovs, _h = _sdn_topo()
    ofc.properties["App"] = "forwarding.hub"        # pick a different personality
    cfg = RuntimeCompiler().compile(t)
    assert cfg.controllers[0].app == "forwarding.hub"
    assert "POX_APP: 'forwarding.hub'" in _compose(cfg)


def test_to_runtime_emits_ovs_and_controller_for_docker():
    t, _ofc, ovs, _h = _sdn_topo()
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    assert len(rt["ovs"]) == 1 and len(rt["controllers"]) == 1
    assert rt["ovs"][0]["controller"] == rt["controllers"][0]["name"]   # wired together
    assert rt["ovs"][0]["controller_port"] == 6633
    assert "gini.samples.switch" in rt["controllers"][0]["app"]

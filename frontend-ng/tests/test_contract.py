"""Auto-derived provides/requires — the composition contract computed from the built board, so a
teacher never types capability roles.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import authoring as AU
from gini.domain import capabilities as CAP
from gini.domain.topology import Topology


def test_device_role_map_is_multi_role_and_domain_tagged():
    assert CAP.roles_for("web_app") == ("web-endpoint", "compute-node")   # a device plays several
    assert CAP.roles_for("switch") == ("switched-segment",)
    assert CAP.domain_of("l2-fabric") == CAP.NETWORKING
    assert CAP.domain_of("web-endpoint") == CAP.CLOUD
    assert CAP.domain_of("kernel-host") == CAP.OS


def test_a_lan_provides_the_fabric_and_requires_nothing():
    t = Topology()
    t.add_device("host", "M1"); t.add_device("host", "M2"); t.add_device("switch", "S1")
    provides, requires = AU.derive_contract(t)
    assert "switched-segment" in provides and "host-node" in provides
    assert requires == []                                # a self-contained LAN needs nothing


def test_a_source_requires_a_target_unless_the_board_supplies_one():
    # an http probe on a host, with NO endpoint on the board → requires a traffic-sink
    t = Topology()
    h = t.add_device("host", "M1")
    p = t.add_device("http_probe", "HTTP1")
    t.add_attach(p.id, h.id)
    _, requires = AU.derive_contract(t)
    assert "traffic-sink" in requires

    # add a web_app (a web-endpoint, which IS-A traffic-sink) → requirement is satisfied internally
    t.add_device("web_app", "WA1")
    provides2, requires2 = AU.derive_contract(t)
    assert "web-endpoint" in provides2 and "traffic-sink" not in requires2


def test_derived_contract_uses_real_roles_so_it_certifies():
    from gini.domain import certify as C
    t = Topology()
    t.add_device("web_app", "WA1"); t.add_device("host", "M1")
    provides, requires = AU.derive_contract(t)
    d = AU.build_fragment_dict(frag_id="web", teaches="x", summary="s", spirit="sp",
                               objectives=[{"id": "w", "say": "web", "check": "exists(web_app)",
                                            "level": 1}],
                               provides=provides or None, requires=requires or None)
    assert C.certify(d, library=[]).certified            # derived roles are all valid → no BLOCK

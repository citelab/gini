"""The Docker project runs the REAL C gRouter (its own container), not the Python stub.

Topology:  h1 — s1 — r1 — r2 — s2 — h2
  * 2 switches  -> the single `fabric` container (Python L2)
  * 2 routers   -> two `gini-grouter` containers (the real C router), incl. a router-router link
  * 2 machines  -> their own containers
"""
import json
import re

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _svc
from gini.services.orchestrator import GROUTER_IMAGE, _compose


def lab() -> Topology:
    t = Topology("lab")
    h1 = t.add_device("host"); s1 = t.add_device("switch")
    r1 = t.add_device("router"); r2 = t.add_device("router")
    s2 = t.add_device("switch"); h2 = t.add_device("host")
    for a, b in [(h1.id, s1.id), (s1.id, r1.id), (r1.id, r2.id),
                 (r2.id, s2.id), (s2.id, h2.id)]:
        t.add_link(a, b)
    return t


def _router_configs(compose: str) -> dict:
    """Extract each service's ROUTER_CONFIG json from the compose text."""
    out = {}
    for m in re.finditer(r"ROUTER_CONFIG: '(\{.*?\})'\n", compose):
        cfg = json.loads(m.group(1))
        out[cfg["name"]] = cfg
    return out


def test_routers_run_as_real_grouter_containers():
    cfg = RuntimeCompiler().compile(lab())
    compose = _compose(cfg)

    # one real-gRouter service per router, using the prebuilt image
    assert compose.count(f"image: {GROUTER_IMAGE}") == 2
    assert "\n  r1:\n" in compose and "\n  r2:\n" in compose

    rcfgs = _router_configs(compose)
    assert set(rcfgs) == {"r1", "r2"}
    for r in rcfgs.values():
        for itf in r["ifaces"]:
            p = itf["port"]
            assert isinstance(p["bind_port"], int) and isinstance(p["peer_port"], int)
            assert "peer_host" in p
            assert itf["ip"].endswith("/24") and itf["mac"].startswith("02:")


def test_router_to_router_link_is_cross_container():
    cfg = RuntimeCompiler().compile(lab())
    compose = _compose(cfg)
    rcfgs = _router_configs(compose)
    # r1 must have an interface pointing at r2's container, and vice versa
    r1_peers = {itf["port"]["peer_host"] for itf in rcfgs["r1"]["ifaces"]}
    r2_peers = {itf["port"]["peer_host"] for itf in rcfgs["r2"]["ifaces"]}
    assert "r2" in r1_peers and "r1" in r2_peers


def test_fabric_holds_switches_only_no_python_router():
    cfg = RuntimeCompiler().compile(lab())
    compose = _compose(cfg)
    # the fabric exists for the two switches...
    m = re.search(r"FABRIC_CONFIG: '(\{.*?\})'\n", compose)
    assert m, "fabric service should be present for the switches"
    fabric = json.loads(m.group(1))
    assert len(fabric["switches"]) == 2
    # ...and the Python stub router is gone from the fabric entirely
    assert "routers" not in fabric
    assert "dataplane.grouter" not in compose


def test_machines_unchanged():
    cfg = RuntimeCompiler().compile(lab())
    compose = _compose(cfg)
    for h in ("m1", "m2"):
        assert f"\n  {h}:\n" in compose
    assert compose.count("Dockerfile.machine") == 2

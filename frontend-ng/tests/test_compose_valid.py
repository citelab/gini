"""The generated docker-compose.yml must be valid YAML, for every shape of topology.

This exists because of a real failure: adding a per-element terminal emitted its own `ports:`
block, and a GUI-toolkit host ALREADY publishes noVNC. Two `ports:` keys in one service is a
duplicate mapping key, and compose refuses the whole lab:

    Run failed: yaml: unmarshal errors:
    line 103: mapping key "ports" already defined at line 97

Nothing caught it: the compose is built by string concatenation, so it is syntactically "fine"
right up until Docker parses it, and the existing orchestrator tests assert on substrings
(`"POX_APP:" in compose`) rather than on the document. A substring test cannot see a duplicate
key.

So these tests parse the document, and separately look for duplicate service-level keys — the
duplicate check matters on its own because PyYAML silently accepts duplicates (last wins) while
Docker rejects them, so `yaml.safe_load` alone would have stayed green through the bug.
"""
import pytest

yaml = pytest.importorskip("yaml")

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import _compose


def _duplicate_keys(txt: str) -> list:
    """Service-level keys emitted twice inside one service block."""
    bad, svc, seen = [], None, set()
    for i, ln in enumerate(txt.splitlines(), 1):
        if ln.startswith("  ") and not ln.startswith("    ") and ln.rstrip().endswith(":"):
            svc, seen = ln.strip().rstrip(":"), set()
            continue
        if ln.startswith("    ") and not ln.startswith("      ") and ":" in ln:
            key = ln.strip().split(":")[0]
            if key in seen:
                bad.append(f"{svc}.{key} (line {i})")
            seen.add(key)
    return bad


def _topo(hosts=3, gui=False, gateway=False, switch=False, sdn=False, xv6=False):
    t = Topology("t")
    r = t.add_device("router")
    for i in range(hosts):
        m = t.add_device("host")
        if gui and i == 0:
            m.properties["Toolkit"] = "gui"
        if gateway and i == 1:
            m.properties["Gateway"] = "true"
        t.add_link(r.id, m.id)
    if switch:
        t.add_link(r.id, t.add_device("switch").id)
    if sdn:
        o, c = t.add_device("ovs"), t.add_device("controller")
        t.add_link(o.id, c.id)
        t.add_link(r.id, o.id)
    if xv6:
        t.add_device("xv6")
    return t


SHAPES = {
    "plain": {},
    "gui-host": {"gui": True},                     # publishes noVNC — the case that broke
    "gui+gateway": {"gui": True, "gateway": True},
    "switch": {"switch": True},
    "sdn": {"sdn": True},
    "xv6": {"xv6": True},
    "everything": {"gui": True, "gateway": True, "switch": True, "sdn": True, "xv6": True},
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("internet", [True, False])
def test_compose_parses_and_has_no_duplicate_keys(shape, internet):
    txt = _compose(RuntimeCompiler().compile(_topo(**SHAPES[shape])), auto_internet=internet)
    dups = _duplicate_keys(txt)
    assert not dups, f"{shape}: duplicate service keys -> {dups}"
    yaml.safe_load(txt)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_element_publishes_exactly_one_terminal(shape):
    """One terminal each, on its own host port, never shared."""
    from gini.services.orchestrator import TTYD_PORT
    doc = yaml.safe_load(_compose(RuntimeCompiler().compile(_topo(**SHAPES[shape]))))
    hosts = []
    for svc in doc["services"].values():
        for p in (svc.get("ports") or []):
            if str(p).endswith(f":{TTYD_PORT}"):
                hosts.append(str(p))
    assert hosts, "no element published a terminal"
    assert len(hosts) == len(set(hosts)), f"host ports collide: {hosts}"


def test_terminals_are_loopback_only():
    """ttyd runs writable with no password — it must never be reachable off this machine."""
    from gini.services.orchestrator import TTYD_PORT
    doc = yaml.safe_load(_compose(RuntimeCompiler().compile(_topo(**SHAPES["everything"]))))
    for name, svc in doc["services"].items():
        for p in (svc.get("ports") or []):
            if str(p).endswith(f":{TTYD_PORT}"):
                assert str(p).startswith("127.0.0.1:"), f"{name} exposes its terminal: {p}"


def test_a_router_fronts_its_cli_and_a_host_gets_a_shell():
    """Routers and OVS switches share the gRouter image, so what the terminal FRONTS has to come
    from compose rather than from the image."""
    doc = yaml.safe_load(_compose(RuntimeCompiler().compile(_topo(sdn=True))))
    env = {n: (s.get("environment") or {}) for n, s in doc["services"].items()}
    routers = [n for n, e in env.items() if "grconsole" in str(e.get("TTYD_CMD", ""))]
    assert routers, "no router fronts the gRouter CLI"
    hosts = [n for n, e in env.items() if n.startswith("m") and "TTYD_CMD" not in e]
    assert hosts, "a host should get a plain shell (no TTYD_CMD)"

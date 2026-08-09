"""The Desktop element: a headful ('gui' toolkit) machine on the fabric with a noVNC console port."""
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _role, _toolkit_for


def test_desktop_is_a_fabric_machine():
    assert _role("desktop") == "machine"          # a real fabric host, not an isolated box


def test_desktop_compiles_to_gui_host_with_a_novnc_port():
    t = Topology("z")
    d = t.add_device("desktop"); s = t.add_device("switch"); t.add_link(d.id, s.id)
    assert _toolkit_for(d) == "gui"               # the Desktop is always the gui toolkit
    cfg = RuntimeCompiler().compile(t)
    m = next(x for x in cfg.machines if x.toolkit == "gui")
    assert m.novnc_port >= 38000                  # gets a published host port for its screen


def test_plain_machine_stays_lean_with_no_console_port():
    t = Topology("z")
    h = t.add_device("host"); s = t.add_device("switch"); t.add_link(h.id, s.id)
    cfg = RuntimeCompiler().compile(t)
    m = next(x for x in cfg.machines)
    assert m.toolkit == "lean" and m.novnc_port == 0

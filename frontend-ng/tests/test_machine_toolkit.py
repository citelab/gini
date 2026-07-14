"""Machine TOOLKIT — lean by default, full only when you ask for it.

A Host is the most replicated container in GINI: a ten-node topology is ten of them. It was also
the fattest image (Debian + bind9 + postfix + ettercap + tshark…), so every student carried ten
copies of a mail server they never ran. On a laptop slimmer than the developer's, that is exactly
where "GINI is slow" comes from.

Toolkit is a DIFFERENT axis from the size tier: size = how much CPU it gets and what it costs;
toolkit = what's installed inside it. A lean host with an XL cap must be expressible.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain import devices
from gini.domain.topology import Topology
from gini.services import orchestrator as O
from gini.services.compiler import RuntimeCompiler, _toolkit_for


def _host(t, name, **props):
    d = t.add_device("host", name=name)
    d.properties.update(props)
    return d


def test_a_plain_host_is_lean():
    t = Topology()
    d = _host(t, "H1")
    assert d.properties["Toolkit"] == "lean"          # the palette default
    assert _toolkit_for(d) == "lean"


def test_full_is_opt_in_and_a_typo_never_pulls_the_big_image():
    t = Topology()
    assert _toolkit_for(_host(t, "A", Toolkit="full")) == "full"
    assert _toolkit_for(_host(t, "B", Toolkit="FuLl")) == "full"      # case-insensitive
    assert _toolkit_for(_host(t, "C", Toolkit="lite")) == "lean"      # unrecognised → lean
    assert _toolkit_for(_host(t, "D", Toolkit="")) == "lean"


def test_compose_names_one_image_per_toolkit_so_it_builds_once():
    """Explicit `image:` matters: without it compose tags a separate <project>-<service> image per
    host and walks the build for each. Ten lean hosts must resolve to ONE image."""
    t = Topology()
    sw = t.add_device("switch", name="S1")
    for i in range(3):
        h = _host(t, f"H{i}")
        t.add_link(h.id, sw.id)
    heavy = _host(t, "H9", Toolkit="full")
    t.add_link(heavy.id, sw.id)

    compose = O._compose(RuntimeCompiler().compile(t))
    assert compose.count("image: gini-machine-lean") == 3      # …but all three name the same image
    assert compose.count("image: gini-machine-full") == 1
    assert "dockerfile: docker/Dockerfile.machine-lean" in compose
    assert "dockerfile: docker/Dockerfile.machine }" in compose


def test_the_lean_image_is_alpine_and_carries_no_mail_or_dns_server():
    df = O._DOCKERFILE_MACHINE_LEAN
    assert df.startswith("FROM alpine:")
    for tool in ("tcpdump", "curl", "bind-tools", "iperf3", "nmap", "socat", "iproute2"):
        assert tool in df                              # what a student actually types
    for heavy in ("postfix", "bind9", "ettercap", "haproxy", "dsniff", "tshark"):
        assert heavy not in df                         # what made the old image huge
    assert "python3" in df                             # the dataplane shuttle (stdlib only)


def test_toolkit_and_size_are_independent_axes():
    """A lean host with an XL CPU cap is a perfectly sensible thing to want — the two knobs must
    not be conflated (size = CPU + cost, toolkit = contents)."""
    t = Topology()
    sw = t.add_device("switch", name="S1")
    d = _host(t, "BIG")
    d.size = 4                                          # XL
    t.add_link(d.id, sw.id)
    cfg = RuntimeCompiler().compile(t)
    m = cfg.machines[0]
    assert m.toolkit == "lean" and m.cpus == 4.0        # lean image, XL cpu


def test_the_inspector_tells_the_truth_about_which_toolkit_a_host_has():
    from gini.ui.inspector import Inspector
    t = Topology()
    lean, full = _host(t, "L1"), _host(t, "F1", Toolkit="full")
    assert "LEAN" in Inspector._runtime_note(lean) and "Alpine" in Inspector._runtime_note(lean)
    assert "FULL" in Inspector._runtime_note(full) and "postfix" in Inspector._runtime_note(full)


def test_toolkit_renders_as_a_dropdown_not_a_free_text_box():
    dt = devices.get("host")
    assert dt.property_choices["Toolkit"] == ("lean", "full")

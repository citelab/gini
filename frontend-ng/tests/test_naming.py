"""Auto-name prefixes: sensible, collision-free defaults + user overrides."""
from gini.domain.devices import default_prefix
from gini.domain.topology import Topology


def test_default_prefixes_are_collision_free():
    t = Topology("lab")
    m1 = t.add_device("host")          # Machine -> M1
    p1 = t.add_device("metrics")       # Metrics -> PROM1 (NOT M1!)
    assert m1.name == "M1"
    assert p1.name == "PROM1"
    # router/switch keep their familiar prefixes
    assert t.add_device("router").name == "R1"
    assert t.add_device("switch").name == "S1"
    # second machine continues its own sequence
    assert t.add_device("host").name == "M2"


def test_distinct_types_dont_share_a_counter():
    t = Topology("lab")
    c = t.add_device("container").name       # CT1
    ca = t.add_device("cache").name          # CA1
    assert c == "CT1" and ca == "CA1"        # not both "C1"


def test_prefix_override_changes_naming():
    t = Topology("lab")
    t.prefix_overrides = {"host": "Mach_"}
    assert t.add_device("host").name == "Mach_1"
    assert t.add_device("host").name == "Mach_2"
    assert t.add_device("router").name == "R1"   # others unaffected


def test_default_prefix_helper():
    assert default_prefix("host") == "M"
    assert default_prefix("metrics") == "PROM"
    assert default_prefix("controller") == "OFC"

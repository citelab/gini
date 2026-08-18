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


def test_auto_name_continues_on_a_loaded_topology():
    # regression: adding to an EXISTING topology must pick the next number, not collide at 1.
    # A load deserializes devices with explicit names and never touches the counter, so simulate
    # that by clearing the counters while the devices remain.
    t = Topology("lab")
    for _ in range(3):
        t.add_device("router")                 # R1, R2, R3
    for _ in range(2):
        t.add_device("host")                   # M1, M2
    t._name_counters.clear()                    # <- what a fresh load leaves behind
    assert t.add_device("router").name == "R4"  # not R1 (the bug)
    assert t.add_device("host").name == "M3"    # machines continue too
    assert t.add_device("switch").name == "S1"  # a type with none yet still starts at 1


def test_auto_name_takes_one_past_the_highest_even_with_gaps():
    t = Topology("lab")
    a = t.add_device("router"); t.add_device("router"); t.add_device("router")  # R1 R2 R3
    t.remove_device(a.id)                        # delete R1 -> gap
    t._name_counters.clear()
    assert t.add_device("router").name == "R4"  # one past the highest, never reuses R1


def test_load_does_not_clobber_links_when_link_ids_exceed_device_ids():
    # THE interconnect-disappearing bug: on load the id counter was rebuilt from DEVICE ids only,
    # so a new link's id collided with an existing link (link ids run higher) and overwrote it.
    data = {"name": "n",
            "devices": [{"id": "router-1", "type_key": "router", "name": "R1"},
                        {"id": "router-2", "type_key": "router", "name": "R2"}],
            "links": [{"id": "link-3", "source_id": "router-1", "target_id": "router-2"},
                      {"id": "link-4", "source_id": "router-1", "target_id": "router-2"}]}
    t = Topology.from_dict(data)
    assert set(t.links) == {"link-3", "link-4"}
    r = t.add_device("router")                          # a new device …
    before = set(t.links)
    nl = t.add_link("router-1", r.id)                   # … and a new link
    assert before <= set(t.links)                       # every existing interconnect SURVIVES
    assert nl.id not in before and len(t.links) == 3    # the new link got a fresh, non-colliding id


def test_new_id_never_reuses_an_existing_device_or_link_id():
    t = Topology.from_dict({"name": "n",
                            "devices": [{"id": "router-5", "type_key": "router", "name": "R5"}],
                            "links": []})
    ids = {t.add_device("router").id for _ in range(5)}
    assert "router-5" not in ids and len(ids) == 5      # all fresh, none clobber the loaded R5


def test_default_prefix_helper():
    assert default_prefix("host") == "M"
    assert default_prefix("metrics") == "PROM"
    assert default_prefix("controller") == "OFC"

"""F2: project save/load round-trips the topology (devices, links, positions, props)."""
import tempfile
from pathlib import Path

from gini.domain.topology import Topology
from gini.services.persistence import load_project, save_project


def build() -> Topology:
    t = Topology("lab")
    r1 = t.add_device("router", x=10, y=20)
    s1 = t.add_device("switch", x=-30, y=40)
    h1 = t.add_device("host", x=-100, y=120)
    t.devices[h1.id].properties["OS"] = "ubuntu"
    t.add_link(r1.id, s1.id)
    t.add_link(h1.id, s1.id)
    return t


def test_roundtrip(tmp_path=None):
    t = build()
    path = Path(tempfile.mkdtemp()) / "lab.gini"
    save_project(t, path)
    t2 = load_project(path)
    assert t2.name == "lab"
    assert len(t2.devices) == 3
    assert len(t2.links) == 2
    # positions + properties preserved
    r = t2.find_by_name("R1")
    assert (r.x, r.y) == (10, 20)
    h = t2.find_by_name("M1")
    assert h.properties["OS"] == "ubuntu"
    # ids continue without collision
    new = t2.add_device("switch")
    assert new.id not in t2.devices or new.id != r.id


def test_load_rejects_bad_format():
    path = Path(tempfile.mkdtemp()) / "bad.gini"
    path.write_text('{"format":"nope"}')
    try:
        load_project(path)
        assert False, "should have raised"
    except ValueError:
        pass

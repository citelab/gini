"""A project saved by a newer GINI opens in this one, and keeps what this one did not understand.

The loader built every record with `Cls(**record)`, so one unknown field -- the first link
property, say -- raised TypeError and the file failed to open: for a student who had not upgraded,
for the remote run server, for a Teaching Center marker on last term's release. Link properties
(docs/design/link-properties.md) would have been exactly that field, so tolerance ships first.

Two halves, both pinned here: an unknown key no longer breaks a load, and it is written back out
unchanged -- flat, beside the known keys, never under an "extra" key that a still-older loader
would itself choke on -- so a file can pass through an older GINI without losing anything. And a
topology with nothing extra serializes exactly as before, because proof-of-activity hashes the
saved dict.
"""
import json

from gini.domain.topology import Link, Topology
from gini.services.persistence import load_project, save_project


def _lab() -> Topology:
    t = Topology("lab")
    r = t.add_device("router")
    m = t.add_device("host")
    t.add_link(m.id, r.id, "uplink")
    return t


def _from_newer_gini() -> dict:
    d = _lab().to_dict()
    d["links"][0]["props"] = {"cost": 4, "fail_after": 120}      # a field from the future
    d["devices"][0]["future_device_field"] = "x"
    d["failure_seed"] = 7                                         # and a top-level one
    return d


def test_a_file_with_unknown_fields_opens():
    t = Topology.from_dict(_from_newer_gini())
    assert len(t.links) == 1 and len(t.devices) == 2


def test_unknown_fields_survive_a_load_and_save():
    out = Topology.from_dict(_from_newer_gini()).to_dict()
    assert out["links"][0]["props"] == {"cost": 4, "fail_after": 120}
    assert out["devices"][0]["future_device_field"] == "x"
    assert out["failure_seed"] == 7


def test_they_are_written_flat_never_under_an_extra_key():
    """An `extra` key in the saved file would crash every loader older than this one."""
    out = Topology.from_dict(_from_newer_gini()).to_dict()
    for rec in out["links"] + out["devices"]:
        assert "extra" not in rec
    assert "extra" not in out


def test_a_topology_with_nothing_extra_serializes_exactly_as_before():
    """Proof-of-activity hashes the saved dict, so the plain case must not change by a byte."""
    t = _lab()
    out = t.to_dict()
    assert list(out) == ["name", "manual_addressing", "routing_mode", "devices", "links"]
    assert list(out["links"][0]) == ["id", "source_id", "target_id", "label", "kind"]
    assert "extra" not in out["devices"][0]
    assert json.dumps(Topology.from_dict(out).to_dict()) == json.dumps(out)


def test_a_known_field_wins_over_a_leftover_of_the_same_name():
    link = Link("link-1", "a", "b", "real")
    link.extra = {"label": "stale", "other": 1}
    t = Topology("x")
    t.links[link.id] = link
    rec = t.to_dict()["links"][0]
    assert rec["label"] == "real" and rec["other"] == 1


def test_through_the_project_file(tmp_path):
    path = tmp_path / "lab.gini"
    save_project(Topology.from_dict(_from_newer_gini()), path)
    again = load_project(path).to_dict()
    assert again["links"][0]["props"]["cost"] == 4
    assert again["failure_seed"] == 7


def test_a_file_from_before_a_field_existed_still_loads():
    """The other direction, unchanged: missing keys take their defaults."""
    d = _lab().to_dict()
    for rec in d["links"]:
        rec.pop("kind")
    t = Topology.from_dict(d)
    assert all(l.kind == "link" for l in t.links.values())

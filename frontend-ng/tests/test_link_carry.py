"""Copying a link keeps its attributes, not just its two endpoints.

Several paths rebuild links from their endpoints: loading a fragment's board, composing a lab from
fragments, merging a scaffold, applying a staged spec, applying a Wizard recipe. Each used to call
`add_link(s, t)` and nothing more, so a link's label was already silently lost there -- and link
properties (cost, failure; docs/design/link-properties.md) would have been next. They now copy
everything but identity through `link_attributes` / `apply_link_attributes`.
"""
from gini.domain import staging
from gini.domain.compose import _instantiate_stage
from gini.domain.topology import Link, Topology, apply_link_attributes, link_attributes


def _source() -> Topology:
    t = Topology("src")
    r, m = t.add_device("router"), t.add_device("host")
    link = t.add_link(m.id, r.id, "uplink")
    link.props = {"cost": 3}
    link.extra["future"] = 1                          # and a field this version does not know
    return t


def test_attributes_are_everything_but_identity():
    t = _source()
    attrs = link_attributes(next(iter(t.links.values())))
    assert attrs == {"label": "uplink", "props": {"cost": 3}, "future": 1}


def test_they_read_the_same_from_a_saved_record():
    rec = _source().to_dict()["links"][0]
    assert link_attributes(rec) == {"label": "uplink", "props": {"cost": 3}, "future": 1}


def test_applying_sets_known_fields_and_keeps_the_rest():
    link = Link("link-9", "a", "b")
    apply_link_attributes(link, {"label": "x", "props": {"cost": 5}, "future": 2,
                                 "id": "ignored", "kind": "attach"})
    assert link.label == "x" and link.props == {"cost": 5} and link.extra == {"future": 2}
    assert link.id == "link-9" and link.kind == "link"          # identity is never overwritten


def test_composing_a_lab_keeps_the_stage_links_attributes():
    stage = _source().to_dict()
    topo = Topology("lab")
    _instantiate_stage(topo, stage, label="A")
    (link,) = topo.links.values()
    assert link.label == "uplink" and link.props == {"cost": 3} and link.extra["future"] == 1


def test_a_staged_link_written_as_an_object_keeps_its_attributes():
    spec = staging.normalize({"devices": [{"ref": "a", "type": "router"},
                                          {"ref": "b", "type": "host"}],
                              "links": [{"source": "a", "target": "b", "label": "wan",
                                         "props": {"cost": 7}}, ["a", "b"]]})
    assert spec["links"][0] == ["a", "b", {"label": "wan", "props": {"cost": 7}}]
    assert spec["links"][1] == ["a", "b"]                        # plain pairs stay pairs
    topo = Topology("lab")
    staging.apply(spec, add_device=lambda tk, x=0.0, y=0.0: topo.add_device(tk),
                  add_link=topo.add_link, topology=topo)
    labelled = [l for l in topo.links.values() if l.label == "wan"]
    assert len(labelled) == 1 and labelled[0].props == {"cost": 7}

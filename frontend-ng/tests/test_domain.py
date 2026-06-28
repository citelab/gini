from gini.domain import Category, all_devices, by_category, get
from gini.domain.topology import Topology


def test_registry_spans_networks_and_cloud():
    keys = {d.key for d in all_devices()}
    # classic networking
    assert {"router", "switch", "host", "ovs"} <= keys
    # all four cloud domains represented
    assert "container" in keys and get("container").category is Category.CONTAINERS
    assert "vpc" in keys and get("vpc").category is Category.CLOUD_NETWORK
    assert "instance" in keys and get("instance").category is Category.COMPUTE
    # the Pod Autoscaler (HPA) lives with the Kubernetes elements, not generic compute
    assert get("instance_group").category is Category.CONTAINERS
    assert "object_store" in keys and get("object_store").category is Category.STORAGE
    assert "function" in keys and get("function").category is Category.SERVERLESS


def test_by_category_nonempty():
    cats = by_category()
    assert Category.CONTAINERS in cats
    assert all(items for items in cats.values())


def test_topology_build_and_autoname():
    t = Topology("lab")
    r1 = t.add_device("router")
    r2 = t.add_device("router")
    assert r1.name == "R1" and r2.name == "R2"
    s1 = t.add_device("switch")
    t.add_link(r1.id, s1.id)
    assert t.degree(r1.id) == 1
    assert s1 in t.neighbors(r1.id)


def test_serialization_roundtrip():
    t = Topology("lab")
    a = t.add_device("vpc")
    b = t.add_device("instance")
    t.add_link(a.id, b.id)
    data = t.to_dict()
    t2 = Topology.from_dict(data)
    assert len(t2.devices) == 2
    assert len(t2.links) == 1
    # ids preserved and new ids continue without collision
    c = t2.add_device("router")
    assert c.id not in {a.id, b.id}

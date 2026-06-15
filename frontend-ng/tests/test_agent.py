from gini.app import AppContext
from gini.agent.api import GiniAPI


def make():
    ctx = AppContext()
    return ctx, GiniAPI(ctx)


def test_agent_can_build_and_connect():
    _, api = make()
    r = api.add_device("router")
    s = api.add_device("switch")
    link = api.connect(r["name"], s["name"])
    assert link["source"] == r["name"] and link["target"] == s["name"]
    info = api.inspect(r["name"])
    assert info["degree"] == 1
    assert s["name"] in info["neighbors"]


def test_agent_set_property_renames():
    _, api = make()
    d = api.add_device("instance")
    api.set_property(d["name"], "Name", "web-1")
    assert api.inspect("web-1")["name"] == "web-1"


def test_explain_hybrid_topology():
    _, api = make()
    api.add_device("router")
    api.add_device("vpc")
    text = api.explain_topology()
    assert "hybrid" in text.lower()
    assert "elements" in text


def test_summary_counts_categories():
    _, api = make()
    api.add_device("router")
    api.add_device("container")
    s = api.summary()
    assert s["devices"] == 2
    assert "Networking" in s["by_category"]
    assert "Containers & Kubernetes" in s["by_category"]

"""The connection grammar — valid partners, required links, and why-text."""
from gini.domain import connection_rules as cr
from gini.domain.devices import REGISTRY


def test_pod_requires_cluster_and_offers_autoscaler():
    partners = {p.type_key: p for p in cr.partners_for("pod")}
    assert "k8s_cluster" in partners and partners["k8s_cluster"].required
    assert "instance_group" in partners and not partners["instance_group"].required
    assert "registry" in partners


def test_autoscaler_requires_a_pod():
    req = [p.type_key for p in cr.required_partners("instance_group")]
    assert req == ["pod"]


def test_grammar_is_symmetric():
    # if a connects to b, b connects to a (matching is undirected)
    assert "pod" in cr.partner_types("k8s_cluster")
    assert "k8s_cluster" in cr.partner_types("pod")
    assert "metrics" in cr.partner_types("web_app")
    assert "web_app" in cr.partner_types("metrics")


def test_can_connect_returns_reason_or_none():
    assert cr.can_connect("pod", "k8s_cluster")           # a real reason string
    assert cr.can_connect("k8s_cluster", "pod")           # both directions
    assert cr.can_connect("pod", "router") is None        # not a recommended link
    assert cr.can_connect("dashboard", "metrics")


def test_required_first_in_ordering():
    partners = cr.partners_for("pod")
    assert partners[0].required                            # cluster sorts to the front


def test_missing_required_drives_hints():
    assert [p.type_key for p in cr.missing_required("pod", set())] == ["k8s_cluster"]
    assert cr.missing_required("pod", {"k8s_cluster"}) == []
    # dashboard needs a metrics source
    assert [p.type_key for p in cr.missing_required("dashboard", set())] == ["metrics"]


def test_group_expansion_reaches_every_workload():
    # a load balancer should reach all backends, including a Pod
    lb = cr.partner_types("load_balancer")
    assert {"web_app", "instance", "container", "pod"} <= lb


def test_every_partner_is_a_real_element_type():
    # the grammar must not reference type keys that don't exist in the registry
    for src in cr._ADJ:
        assert src in REGISTRY, src
        for partner in cr.partner_types(src):
            assert partner in REGISTRY, partner

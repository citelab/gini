"""The Wizard's objective layer: a goal -> the set of relevant element types."""
from gini.domain import missions as ms


def test_networking_goal_picks_network_elements():
    m = ms.keyword_mission("build a multi-LAN IP network")
    assert m.first == "router"
    assert {"router", "switch", "host"} <= m.types
    assert "database" not in m.types and "pod" not in m.types


def test_kubernetes_goal_picks_k8s_elements():
    m = ms.keyword_mission("an autoscaling kubernetes deployment")
    assert m.first == "k8s_cluster"
    assert {"k8s_cluster", "pod", "instance_group"} <= m.types


def test_sdn_goal():
    m = ms.keyword_mission("an SDN lab with an OpenFlow controller")
    assert {"ovs", "controller"} <= m.types
    assert not m.allows("database")


def test_unknown_goal_imposes_no_constraint():
    m = ms.keyword_mission("xyzzy frobnicate")
    assert m.allows("router") and m.allows("database")    # everything is on-goal
    assert m.first is None


def test_refine_types_scans_labels_and_keys():
    hits = ms.refine_types("You'll want a Router, a Switch, and a Pod here.")
    assert {"router", "switch", "pod"} <= hits

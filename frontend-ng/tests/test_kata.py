"""Kata Instance — a VM-isolated compute element for VM-vs-container experiments."""
from gini.domain import connection_rules as cr
from gini.domain.devices import REGISTRY
from gini.domain.pricing import DEFAULT_RATES, category_of
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _role
from gini.services.orchestrator import _compose, _startup_ms


def test_kinstance_is_a_compute_element():
    assert "kinstance" in REGISTRY and not REGISTRY["kinstance"].hidden
    assert _role("kinstance") == "compute"


def test_kinstance_grammar_is_restricted():
    assert cr.can_connect("kinstance", "database")
    assert cr.can_connect("load_generator", "kinstance")
    assert cr.can_connect("metrics", "kinstance")
    assert cr.can_connect("kinstance", "kinstance")
    # NOT k8s / networking plane / VPC — it can't run those, stays flat
    for bad in ("k8s_cluster", "pod", "router", "vpc", "cloud_subnet"):
        assert cr.can_connect("kinstance", bad) is None


def test_kinstance_compiles_with_the_kata_runtime():
    t = Topology("vm")
    t.add_device("kinstance")
    s = next(x for x in RuntimeCompiler().compile(t).services if x.type_key == "kinstance")
    assert s.runtime == "kata"
    t2 = Topology("c"); t2.add_device("instance")        # a normal instance: no override
    assert RuntimeCompiler().compile(t2).services[0].runtime == ""


def test_kinstance_stays_out_of_vpcs():
    t = Topology("vm")
    vpc = t.add_device("vpc")
    t.add_device("kinstance", parent_id=vpc.id)          # dropped inside a VPC box
    s = next(x for x in RuntimeCompiler().compile(t).services if x.type_key == "kinstance")
    assert s.network == "gini"                            # forced flat, not the VPC net


def test_compose_emits_kata_runtime_only_for_kinstance():
    t = Topology("mix")
    t.add_device("kinstance"); t.add_device("instance")
    comp = _compose(RuntimeCompiler().compile(t))
    assert comp.count("runtime: kata") == 1              # only the kinstance gets it


def test_kinstance_priced_above_a_plain_instance():
    assert DEFAULT_RATES["kinstance"] > DEFAULT_RATES["instance"]
    assert category_of("kinstance") == "Compute"


def test_startup_ms_parser():
    # StartedAt - Created, RFC3339 with nanoseconds + Z
    assert _startup_ms("2024-06-27T12:00:00.000000000Z",
                       "2024-06-27T12:00:01.500000000Z") == 1500.0
    assert _startup_ms("2024-06-27T12:00:00Z", "2024-06-27T12:00:00.090000Z") == 90.0
    assert _startup_ms("2024-06-27T12:00:00Z", "0001-01-01T00:00:00Z") is None  # never started

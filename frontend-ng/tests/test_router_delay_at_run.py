"""A link delay set in the Router Lab survives the next Run.

The Lab saved `DelayIngress` / `DelayEgress` on the router ("base jitter corr", e.g. "50 5 0.90")
and applied them live over the console, but nothing applied them at Run -- so a delay a student
had tuned vanished on restart. The compiler now carries them into the router's config as numbers,
and run_grouter.py emits the same `delay` command the Lab sends.
"""
import json

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _delay_setting


def _router_cfg(**props):
    t = Topology("x")
    r, m = t.add_device("router"), t.add_device("host")
    t.add_link(m.id, r.id)
    r.properties.update(props)
    return RuntimeCompiler().compile(t).to_runtime(docker=False)["routers"][0]


def test_a_saved_delay_reaches_the_router_config():
    cfg = _router_cfg(DelayEgress="50 5 0.90", DelayIngress="20 0 0.00")
    assert cfg["delay"] == {"egress": [50.0, 5.0, 0.9], "ingress": [20.0, 0.0, 0.0]}


def test_no_delay_means_no_key_so_nothing_else_changes():
    assert "delay" not in _router_cfg()
    assert "delay" not in _router_cfg(DelayEgress="", DelayIngress="0 0 0.00")


def test_only_numbers_reach_the_compose_file():
    """The config is embedded in single-quoted YAML; a quote in a property would break it."""
    assert _delay_setting("50'; rm -rf") is None
    assert "'" not in json.dumps(_router_cfg(DelayEgress="50' x"))


def test_partial_values_fill_with_zeros():
    assert _delay_setting("40") == [40.0, 0.0, 0.0]
    assert _delay_setting("0 8") == [0.0, 8.0, 0.0]          # jitter alone is a real setting

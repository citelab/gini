"""GINI-KB retrieval: always-on index + probe-directed concept/grammar/recipe cards."""
from types import SimpleNamespace

from gini.agent import kb
from gini.agent.api import GiniAPI
from gini.app import AppContext


def _intent(**kw):
    base = dict(anchor="concept", output_form="tell", depth="shallow", topics=[], refs=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_always_on_lists_elements_and_groups():
    ctx = kb.always_on_context()
    assert "function" in ctx and "api_gateway" in ctx        # serverless elements present
    assert "k8s_node" not in ctx                             # hidden element suppressed
    assert "WORKLOAD=" in ctx and "DATASTORE=" in ctx        # grammar groups present


def test_retrieve_serverless_pulls_concept_recipe_and_elements():
    r = kb.retrieve(_intent(topics=["serverless"], output_form="show"))
    kinds = {c.kind for c in r.cards}
    assert "concept" in kinds                                # the how-it-works note
    assert r.recipe is not None and r.recipe.id == "serverless"
    elem_keys = {c.key for c in r.cards if c.kind == "element"}
    assert {"function", "api_gateway"} <= elem_keys          # the pattern's elements


def test_element_card_shows_required_partners():
    r = kb.retrieve(_intent(topics=["kubernetes"], output_form="show"))
    pod = next(c for c in r.cards if c.kind == "element" and c.key == "pod")
    assert "k8s_cluster*" in pod.text                        # a Pod requires a cluster


def test_depth_gates_concept_count():
    shallow = kb.retrieve(_intent(topics=["serverless"], depth="shallow"))
    deep = kb.retrieve(_intent(topics=["serverless"], depth="deep"))
    n_shallow = sum(c.kind == "concept" for c in shallow.cards)
    n_deep = sum(c.kind == "concept" for c in deep.cards)
    assert n_deep >= n_shallow


def test_canvas_anchor_resolves_refs_to_element_cards():
    api = GiniAPI(AppContext())
    d = api.add_device("router", name="R1")
    r = kb.retrieve(_intent(anchor="canvas", refs=["R1"]), topology=api.ctx.topology)
    assert any(c.kind == "element" and c.key == "router" for c in r.cards)


def test_empty_topics_no_recipe():
    r = kb.retrieve(_intent(topics=[]))
    assert r.recipe is None and not r.cards

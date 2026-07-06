"""Phase 0 NFV/SFC: concept notes + a buildable chain recipe + Ask GINI retrieval/routing."""
from gini.agent import ask, kb
from gini.agent.understand import understand
from gini.domain import concepts, recipes


def test_nfv_sfc_concepts_present_and_searchable():
    assert concepts.by_key("nfv") is not None and concepts.by_key("sfc") is not None
    assert concepts.search(["nfv"])[0].key == "nfv"
    assert concepts.search(["service function chain"])[0].key == "sfc"
    assert concepts.search(["steering"])[0].key == "sfc"
    # grounded in GINI's real realizations, not textbook generalities
    assert "Router Lab" in concepts.by_key("sfc").body


def test_nfv_chain_recipe_is_buildable_and_top_ranked():
    assert recipes.get_recipe("nfv_chain") is not None
    assert recipes.suggest_recipes("show me a service function chain")[0].id == "nfv_chain"


def test_agent_explains_sfc_grounded_in_gini():
    it = understand("explain SFC in GINI", canvas_names=[])
    cards = kb.retrieve(it).cards
    assert any(c.kind == "concept" and c.key == "sfc" for c in cards)


def test_agent_autobuilds_the_chain_on_show_me():
    it = understand("show me a service function chain example", canvas_names=[])
    p = ask.plan(it, kb.retrieve(it))
    assert p.action == "build_recipe" and p.recipe_id == "nfv_chain"

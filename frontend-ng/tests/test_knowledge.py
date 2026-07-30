"""GINI knowledge base: concept notes + recipes.

Concept notes are the tier-3 depth layer; recipes are vetted example topologies the agent
builds. Guardrails: every palette element (except the hidden k8s_node) is covered by a
recipe, every recipe link is grammar-valid, every element/concept keyword resolves, and
apply_recipe actually instantiates a blueprint (incl. VPC box containment)."""
from gini.domain import concepts, recipes
from gini.domain import connection_rules as cr
from gini.domain.devices import all_devices
from gini.agent.api import GiniAPI
from gini.app import AppContext


# -- concepts --------------------------------------------------------------- #
def test_every_concept_references_real_elements():
    keys = {d.key for d in all_devices()}
    for c in concepts.CONCEPTS:
        for e in c.elements:
            assert e in keys, f"concept {c.key} references unknown element {e}"
        assert c.body.strip(), f"concept {c.key} has an empty body"


def test_concept_search_and_for_element():
    assert concepts.search(["serverless"])[0].key == "serverless"
    assert concepts.search(["least privilege", "firewall"])[0].key == "security-groups"
    assert any(c.key == "vpc-networking" for c in concepts.for_element("cloud_subnet"))
    assert concepts.by_key("kubernetes") is not None


def test_core_subsystems_have_a_concept():
    have = {c.key for c in concepts.CONCEPTS}
    for k in ("serverless", "vpc-networking", "security-groups", "kubernetes",
              "messaging-queue", "sdn", "cost-model"):
        assert k in have


# -- recipes ---------------------------------------------------------------- #
def test_recipes_cover_every_palette_element():
    palette = {d.key for d in all_devices()} - {"k8s_node"}   # k8s_node is hidden
    missing = palette - recipes.covered_elements()
    assert not missing, f"no recipe covers: {sorted(missing)}"


def test_every_recipe_link_is_grammar_valid():
    for r in recipes.RECIPES:
        types = {el.ref: el.type_key for el in r.elements}
        for a, b in r.links:
            assert a in types and b in types, f"{r.id}: link ref not found"
            ta, tb = types[a], types[b]
            # a recipe edge is valid if it's a grammar-valid network LINK, OR a valid rider→donor
            # ATTACH (either order — ctx.connect figures out which end is the Source/Sink).
            valid = (cr.can_connect(ta, tb) is not None
                     or cr.attach_blocked(ta, tb) is None
                     or cr.attach_blocked(tb, ta) is None)
            assert valid, f"{r.id}: {ta}<->{tb} is not a valid connection or attachment"


def test_recipe_parents_reference_box_elements():
    boxes = {"vpc", "cloud_subnet", "region"}
    for r in recipes.RECIPES:
        types = {el.ref: el.type_key for el in r.elements}
        for el in r.elements:
            if el.parent:
                assert el.parent in types, f"{r.id}: parent {el.parent} missing"
                assert types[el.parent] in boxes, f"{r.id}: parent is not a box"


def test_every_recipe_concept_resolves():
    for r in recipes.RECIPES:
        if r.concept:
            assert concepts.by_key(r.concept) is not None, f"{r.id}: bad concept {r.concept}"


def test_suggest_recipes_matches_intent():
    assert recipes.suggest_recipes("show me a serverless example")[0].id == "serverless"
    assert recipes.suggest_recipes("how to use a message queue")[0].id == "message_queue"
    assert recipes.suggest_recipes("vpc with public and private subnets")[0].id == \
        "vpc_public_private"


# -- build ------------------------------------------------------------------ #
def _api():
    return GiniAPI(AppContext())


def test_apply_recipe_builds_serverless():
    api = _api()
    res = api.apply_recipe("serverless")
    assert len(res["added"]) == 4 and res["links"] == 3
    kinds = {d.type_key for d in api.ctx.topology.devices.values()}
    assert {"api_gateway", "function", "object_store", "queue"} <= kinds


def test_apply_recipe_sets_vpc_containment():
    api = _api()
    api.apply_recipe("vpc_public_private")
    devs = list(api.ctx.topology.devices.values())
    by_type = {d.type_key: d for d in devs}
    # the private database's parent chain leads up to the VPC
    db = by_type["database"]
    subnet = api.ctx.topology.devices[db.parent_id]
    assert subnet.type_key == "cloud_subnet"
    vpc = api.ctx.topology.devices[subnet.parent_id]
    assert vpc.type_key == "vpc"

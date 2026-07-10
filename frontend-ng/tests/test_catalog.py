"""The Game Catalog: archetypes are well-formed (real concepts + element types + parsable
objectives) and instantiate to concrete Objectives by binding refs to canvas names."""
from gini.domain import catalog, objectives as O
from gini.domain.concepts import CONCEPTS
from gini.domain.devices import REGISTRY


def test_archetypes_reference_real_concepts_and_elements():
    ckeys = {c.key for c in CONCEPTS}
    for a in catalog.all_archetypes():
        assert a.teaches in ckeys, f"{a.id} teaches unknown concept {a.teaches}"
        assert a.spirit and a.objectives
        for t in a.objectives:
            assert t.kind in ("structural", "behavioral")
            # a structural template must parse once its {refs} are bound to placeholder names
            if t.kind == "structural":
                bound = t.check
                for ref in a.params:
                    bound = bound.replace("{" + ref + "}", "N1")
                assert O.check_ok(bound), f"{a.id}/{t.id} bad check: {t.check}"
                assert not O.unknown_element_types(bound), f"{a.id}/{t.id} unknown element"


def test_instantiate_produces_concrete_type_based_objectives():
    a = catalog.get("basic-lan")
    objs = catalog.instantiate(a, {})                # type-based archetypes need no bindings
    checks = {o.id: o.check for o in objs}
    assert checks["hosts-on-switch"] == "link(host, switch)"
    assert checks["switch-to-gateway"] == "link(switch, router)"
    assert all("{" not in (o.check + o.probe) for o in objs)     # no placeholders left


def test_type_based_archetypes_need_no_params():
    for a in catalog.all_archetypes():
        assert catalog.unbound_refs(a, {}) == [], f"{a.id} has unbound {{refs}}"


def test_every_element_type_lookup_is_registry_backed():
    # sanity: the types the catalog names really exist (guards typos in archetypes)
    for a in catalog.all_archetypes():
        for t in a.objectives:
            for et in O.element_types_in_check(t.check.replace("{", "").replace("}", "")):
                assert et in REGISTRY or et in a.params

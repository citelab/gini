"""The Ask GINI topic cloud is generated purely from the live KB, so its content is
unit-testable without a GUI: concepts become 'explain X', recipes become build prompts,
and a few marquee elements become 'what is a X?'."""
from gini.domain import concepts as _concepts
from gini.domain import recipes as _recipes
from gini.domain.topic_cloud import CloudItem, topic_cloud


def test_cloud_covers_every_concept_and_recipe():
    items = topic_cloud()
    kinds = {i.kind for i in items}
    assert {"concept", "recipe", "element"} <= kinds

    concepts = [i for i in items if i.kind == "concept"]
    recipes = [i for i in items if i.kind == "recipe"]
    assert len(concepts) == len(_concepts.CONCEPTS)
    assert len(recipes) == len(_recipes.RECIPES)


def test_queries_are_actionable_and_grounded():
    for i in topic_cloud():
        assert isinstance(i, CloudItem)
        assert i.query.strip()                       # every pill sends something
        assert 1 <= i.weight <= 3
        assert i.accent
        if i.kind == "concept":
            assert i.query.lower().startswith("explain ")
        elif i.kind == "recipe":
            assert i.query.lower().startswith("show me a ")
        elif i.kind == "element":
            assert i.query.lower().startswith("what is a ")


def test_recipe_queries_name_real_recipes():
    names = {r.name for r in _recipes.RECIPES}
    for i in topic_cloud():
        if i.kind == "recipe":
            # label is a tidy short form, but the query carries the real recipe name
            assert any(n in i.query for n in names)
            assert len(i.label) <= 22


def test_labels_have_no_dangling_parentheticals():
    for i in topic_cloud():
        assert "(" not in i.label                      # pills stay punchy

"""Recipes — curated, guaranteed-to-work blueprints the Wizard lays out on the canvas.

The Wizard's safety model: the LLM only *selects and explains* recipes (matching the
student's intent against `intent` tags + the summary); the actual building is this
deterministic data + `GiniAPI.apply_recipe`. So even a small local model can't produce a
broken topology — it just picks from known-good blueprints. With no model at all the
recipes are still browsable by tag.

Each recipe is a set of typed elements (with a local `ref` for linking and a grid
position for layout) plus links. Links carry intent ("this load generator targets that
web app", "this Prometheus scrapes that app") that the auto-wiring later reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecipeElement:
    ref: str                       # local key, used by `links`
    type_key: str                  # palette element type
    props: dict = field(default_factory=dict)
    col: int = 0                   # layout grid column
    row: int = 0                   # layout grid row


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    summary: str                   # one line the LLM/UX shows
    intent: tuple[str, ...]        # keywords the LLM/offline matcher scores against
    teaches: str
    elements: tuple[RecipeElement, ...]
    links: tuple[tuple[str, str], ...] = ()


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        id="load_test",
        name="Load-test rig",
        summary="A load generator firing HTTP traffic at a web app — watch throughput "
                "and latency from the generator's own UI.",
        intent=("load", "stress", "benchmark", "performance", "throughput", "latency",
                "test", "experiment", "traffic", "qps"),
        teaches="generating controlled load and reading QPS / latency results",
        elements=(
            RecipeElement("gen", "load_generator", col=0, row=0),
            RecipeElement("app", "web_app", col=1, row=0),
        ),
        links=(("gen", "app"),),
    ),
    Recipe(
        id="observability",
        name="Live observability stack",
        summary="Prometheus + Grafana watching a web app while a load generator drives "
                "it — live dashboards of the running system under load.",
        intent=("observe", "monitor", "visualize", "visualization", "metrics", "dashboard",
                "grafana", "prometheus", "see", "graph", "performance", "telemetry"),
        teaches="scrape metrics → store → dashboard, and watch a system react to load",
        elements=(
            RecipeElement("dash", "dashboard", col=1, row=0),
            RecipeElement("prom", "metrics", col=1, row=1),
            RecipeElement("app", "web_app", col=0, row=2),
            RecipeElement("gen", "load_generator", col=2, row=2),
        ),
        links=(("prom", "dash"), ("prom", "app"), ("gen", "app")),
    ),
)

_BY_ID = {r.id: r for r in RECIPES}


def get_recipe(recipe_id: str) -> Recipe | None:
    return _BY_ID.get(recipe_id)


def suggest_recipes(query: str) -> list[Recipe]:
    """Deterministic intent match (the offline / fallback ranker the LLM mirrors):
    score recipes by how many intent tags or name words appear in the query."""
    q = (query or "").lower()
    scored: list[tuple[int, Recipe]] = []
    for r in RECIPES:
        score = sum(1 for tag in r.intent if tag in q)
        score += sum(1 for w in r.name.lower().split() if len(w) > 3 and w in q)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]

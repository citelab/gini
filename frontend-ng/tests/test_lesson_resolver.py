"""The lesson resolver: a lexical shortlist, an LLM pick that fills params (the reasoning step),
graceful fallback without/with a bad model, and always a VALID proposed lesson to ratify."""
from gini.agent import lesson_resolver as R
from gini.domain import lesson as L


def test_shortlist_ranks_relevant_archetypes():
    assert R.shortlist("keep a database private from the internet")[0].id == "reachability-boundary"
    assert R.shortlist("spread web traffic across replicas")[0].id == "load-balanced-web"
    assert R.shortlist("openflow controller software defined switch")[0].id == "sdn-reactive"


def test_deterministic_resolve_without_model():
    p = R.resolve("teach keeping a database private", lesson_id="lab1")
    assert p.archetype_id == "reachability-boundary"
    assert L.is_valid(p.lesson)
    assert p.candidates                              # the shortlist is surfaced for the UI


def test_llm_pick_fills_params_and_time():
    def llm(prompt):
        return ('{"archetype":"serverless-api","params":{"gw":"GW9","fn":"FN9"},'
                '"time_limit":"30m","title":"FaaS behind a gateway"}')
    p = R.resolve("put a function behind an api gateway", llm=llm, lesson_id="lab2")
    assert p.archetype_id == "serverless-api"
    assert p.lesson.title == "FaaS behind a gateway"
    assert p.lesson.time_limit_s == 1800
    assert L.is_valid(p.lesson)                       # type-based archetypes need no params


def test_bad_model_falls_back_to_a_valid_candidate():
    p = R.resolve("sdn openflow controller lab", llm=lambda _: "no json here")
    assert p.archetype_id in {a for a in [c for c in [x.id for x in R.shortlist("sdn openflow controller lab")]]}
    assert L.is_valid(p.lesson)                      # always a usable proposal


def test_resolve_always_yields_a_valid_lesson():
    # even when the model returns odd params (ignored for type-based archetypes), the proposal
    # is a valid, ratifiable lesson
    def llm(prompt):
        return '{"archetype":"reachability-boundary","params":{"protected":"MYDB"}}'
    p = R.resolve("hide the db from the internet", llm=llm)
    assert p.archetype_id == "reachability-boundary"
    assert L.is_valid(p.lesson)

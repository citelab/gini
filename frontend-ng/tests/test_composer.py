"""The student-facing composer: a free-form wish becomes a PLAYABLE, gradable mission by ASSEMBLING
verified fragments (select a core, close the capability graph, fill exercise/observe layers) — never
by authoring raw objectives. So every composed lesson validates and carries a genre + quest level."""
from gini.agent import lesson_resolver as LR
from gini.domain import catalog, lesson as _lesson


def _scripted(resp):
    return lambda _prompt: resp


def _two_stage(select: str, narrate: str):
    """compose() calls the model twice: once to PICK the fragments, once to DESCRIBE what was
    actually assembled. The pick-call's title is deliberately ignored — at that moment the model is
    only guessing which mission it'll get, so its story can't be checked against anything."""
    return lambda prompt: narrate if '"description"' in prompt else select


def test_compose_stays_lean_no_unasked_enrichment():
    prop = LR.compose(
        "I want to see how a load balancer spreads traffic",
        _two_stage('{"primary":"load-balanced-web","secondary":"","genre":"expedition",'
                   '"title":"LB Lab","brief":"Watch an LB fan traffic out."}',
                   '{"title":"Spread the Load","description":"You will put a load balancer in '
                   'front of two web app replicas. Traffic should fan out across both, not pile '
                   'onto one. Done when the balancer really has two live backends behind it."}'),
    )
    assert prop is not None
    les = prop.lesson
    assert les.title == "Spread the Load"          # the narration call owns the title…
    assert len(les.brief) > len(les.title)         # …and the description is the LONGER of the two
    assert _lesson.is_valid(les)
    # a described mission is LEAN: just the core the student asked for — no auto-added load
    # generator / dashboard / metrics they never requested
    assert les.fragments == ["load-balanced-web"]
    assert not any(t in o.check for o in les.objectives for t in ("metrics", "dashboard"))


def test_compose_carries_the_core_win_conditions():
    prop = LR.compose(
        "switched LAN with a router",
        _scripted('{"primary":"basic-lan","secondary":"","genre":"","title":"","brief":""}'),
    )
    checks = {o.check for o in prop.lesson.objectives}
    for o in catalog.get("basic-lan").objectives:       # the core's gradable checks survive assembly
        assert o.check in checks


def test_compose_experience_pin_stays_a_bare_guided_core():
    prop = LR.compose(
        "just let me build a basic LAN and watch it",
        _scripted('{"primary":"basic-lan","secondary":"","genre":"experience","title":"","brief":""}'),
    )
    les = prop.lesson
    assert les.genre == "experience"
    assert les.fragments == ["basic-lan"]               # experience = core only, no auto-fill
    assert les.help == "full_tutor_logged"
    assert _lesson.is_valid(les)


def test_compose_covers_the_literal_request_even_if_the_model_mispicks():
    # a small model picks basic-lan and writes a firewall-themed title but never selects the firewall
    # core; the deterministic backstop must still put the firewall in the objectives (no drift)
    prop = LR.compose(
        "connect multiple LANs to the internet with a firewall",
        _scripted('{"primary":"basic-lan","secondary":"","genre":"","title":"LANs + FW","brief":""}'),
    )
    assert "service-chain" in prop.lesson.fragments
    assert any("firewall" in o.check for o in prop.lesson.objectives)


def test_compose_honors_a_dont_want():
    prop = LR.compose(
        "a multi-LAN connected to the Internet via a firewall, no metrics and dashboards",
        _scripted('{"primary":"basic-lan","secondary":"","genre":"expedition",'
                  '"exclude":["metrics","dashboard"],"title":"LANs+FW","brief":""}'),
    )
    assert prop.lesson is not None
    assert not any(t in o.check for o in prop.lesson.objectives for t in ("metrics", "dashboard"))
    assert "observe-it" not in prop.lesson.fragments
    assert prop.suppressed                                # a note that something was left out


def test_compose_reports_infeasibility_when_dos_and_donts_conflict():
    prop = LR.compose(
        "give me a dashboard but no metrics",
        _scripted('{"primary":"observe-it","secondary":"","exclude":["metrics"],"title":"","brief":""}'),
    )
    assert prop.lesson is None
    assert prop.infeasible and "metrics" in prop.infeasible


def test_compose_falls_back_when_the_model_is_useless():
    prop = LR.compose("service function chain firewall", _scripted("sorry, no json"))
    assert prop is not None
    assert _lesson.is_valid(prop.lesson)
    assert prop.archetype_id in {a.id for a in catalog.all_archetypes()}


def test_compose_without_a_model_is_deterministic():
    prop = LR.compose("put a database behind a cache", llm=None)
    assert prop is not None
    assert _lesson.is_valid(prop.lesson)
    assert "cache-in-front" in prop.candidates

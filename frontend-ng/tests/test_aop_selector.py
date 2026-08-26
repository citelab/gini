"""The teaching AI drafting an observation plan from a teacher's description.

Every test drives a scripted `llm` callable, so the selector's contract is pinned without a model:
what it accepts, what it repairs, and — most importantly — what it refuses to guess at.
"""
from __future__ import annotations

import json

import pytest

from gini.agent import aop_selector as SEL
from gini.domain import aop_assemble as S
from gini.domain import aop_patterns as P


def _llm(*replies):
    """A scripted model. Returns each reply in turn, repeating the last one forever."""
    seen = []

    def call(prompt):
        seen.append(prompt)
        return replies[min(len(seen) - 1, len(replies) - 1)]

    call.prompts = seen
    return call


def _reply(patterns=(), questions=(), note="") -> str:
    return json.dumps({"patterns": list(patterns), "questions": list(questions), "note": note})


# -- the happy path ----------------------------------------------------------- #
def test_a_description_becomes_an_assemblable_selection():
    llm = _llm(_reply([{"key": "multi-lan", "params": {"lans": 2, "routers": 1}}],
                      note="Watches that the network is routed and that stations talk across it."))
    d = SEL.draft("Connect two LANs with a router and show traffic crossing it.", llm)
    assert d.ok
    assert [p.key for p in d.selection.patterns] == ["multi-lan"]
    plan = S.assemble(d.selection)          # the real gate: gBuilder must be able to actuate it
    assert plan.by_id("multi-segments").check == "count('switch') >= 2"


def test_the_note_is_carried_back_to_the_teacher():
    llm = _llm(_reply([{"key": "single-lan"}], note="Only the LAN itself is watched."))
    assert SEL.draft("build a LAN", llm).note == "Only the LAN itself is watched."


def test_several_patterns_are_kept_in_order():
    llm = _llm(_reply([{"key": "single-lan"}, {"key": "multi-lan"}, {"key": "link-delay"}]))
    d = SEL.draft("do the whole of chapter 16", llm)
    assert [p.key for p in d.selection.patterns] == ["single-lan", "multi-lan", "link-delay"]
    assert S.assemble(d.selection)


# -- what the model is shown -------------------------------------------------- #
def test_the_model_sees_plain_english_and_never_probe_syntax():
    """The bridge only works if the model is choosing from descriptions. Leaking the machine
    format invites it to author expectations, which is the one thing it must not do."""
    llm = _llm(_reply([{"key": "single-lan"}]))
    SEL.draft("build a LAN", llm)
    prompt = llm.prompts[0]
    assert "Observes:" in prompt and "Does NOT observe:" in prompt
    # Syntax, not vocabulary: the prompt may say the word "check" in English, but a probe string
    # or a predicate name would tempt the model into authoring expectations.
    for leak in ("reach(", "count(", "all_linked", "property_type", "through(", "== ok"):
        assert leak not in prompt, f"machine format leaked into the model prompt: {leak}"


def test_the_teachers_own_words_reach_the_model():
    llm = _llm(_reply([{"key": "single-lan"}]))
    SEL.draft("I want them to see ARP happen for the first time", llm)
    assert "ARP happen for the first time" in llm.prompts[0]


def test_the_catalogue_can_be_narrowed():
    """Only the listed patterns get an entry. A narrowed pattern may still be *mentioned* inside
    another's prose ("use multi-lan for that"), which is a cross-reference, not an offer."""
    llm = _llm(_reply([{"key": "single-lan"}]))
    SEL.draft("build a LAN", llm, catalogue_keys=["single-lan"])
    assert "### single-lan" in llm.prompts[0]
    assert "### multi-lan" not in llm.prompts[0]


# -- robustness against how models actually reply ----------------------------- #
def test_json_wrapped_in_prose_is_accepted():
    llm = _llm("Sure! Here's the plan:\n```json\n" + _reply([{"key": "single-lan"}]) + "\n```\nHope that helps.")
    assert SEL.draft("build a LAN", llm).ok


def test_a_bare_pattern_key_is_tolerated():
    llm = _llm(json.dumps({"patterns": ["single-lan"]}))
    d = SEL.draft("build a LAN", llm)
    assert d.ok and d.selection.patterns[0].key == "single-lan"


def test_unparseable_output_is_an_error_not_a_crash():
    d = SEL.draft("build a LAN", _llm("I'm afraid I can't do that."))
    assert not d.ok and "usable plan" in d.error


def test_a_model_that_raises_is_reported_not_propagated():
    def boom(_prompt):
        raise ConnectionError("ollama is not running")

    d = SEL.draft("build a LAN", boom)
    assert not d.ok and "could not be reached" in d.error


# -- self-repair -------------------------------------------------------------- #
def test_an_invented_pattern_triggers_one_repair_round():
    llm = _llm(_reply([{"key": "lan-party"}]),                 # not in the catalogue
               _reply([{"key": "single-lan"}]))                # corrected
    d = SEL.draft("build a LAN", llm)
    assert d.ok and d.selection.patterns[0].key == "single-lan"
    assert len(llm.prompts) == 2


def test_an_invented_parameter_triggers_repair():
    llm = _llm(_reply([{"key": "multi-lan", "params": {"subnets": 4}}]),
               _reply([{"key": "multi-lan", "params": {"lans": 4}}]))
    d = SEL.draft("four LANs", llm)
    assert d.ok and d.selection.patterns[0].params == {"lans": 4}


def test_the_defects_are_shown_to_the_model_when_repairing():
    llm = _llm(_reply([{"key": "lan-party"}]), _reply([{"key": "single-lan"}]))
    SEL.draft("build a LAN", llm)
    assert "lan-party" in llm.prompts[1]


def test_repair_gives_up_rather_than_looping():
    llm = _llm(_reply([{"key": "still-not-real"}]))             # never corrects itself
    d = SEL.draft("build a LAN", llm)
    assert not d.ok
    assert len(llm.prompts) <= SEL.MAX_REPAIRS + 1


# -- questions ---------------------------------------------------------------- #
def test_questions_without_a_selection_are_a_legitimate_first_turn():
    llm = _llm(_reply(questions=["Should the students build one LAN or several?"]))
    d = SEL.draft("set up a networking lab", llm)
    assert not d.ok and d.questions and not d.error


def test_answers_are_fed_back_so_the_loop_converges():
    llm = _llm(_reply([{"key": "multi-lan"}]))
    SEL.draft("set up a networking lab", llm,
              answers=({"q": "One LAN or several?", "a": "several, with routers"},))
    assert "several, with routers" in llm.prompts[0]


def test_questions_are_capped():
    llm = _llm(_reply(questions=[f"q{i}" for i in range(20)]))
    assert len(SEL.draft("vague", llm).questions) == SEL.MAX_QUESTIONS


def test_a_selection_and_questions_can_arrive_together():
    """A draft the teacher can already use, with a refinement offered — not a blocking prompt."""
    llm = _llm(_reply([{"key": "multi-lan"}], questions=["How many LANs?"]))
    d = SEL.draft("routed network", llm)
    assert d.ok and d.questions


def test_choosing_nothing_and_asking_nothing_is_an_error():
    d = SEL.draft("bake a cake", _llm(_reply(note="Nothing here observes baking.")))
    assert not d.ok and "no observation patterns" in d.error


# -- no model, no plan -------------------------------------------------------- #
def test_without_a_backend_it_refuses_rather_than_guessing():
    """A keyword fallback would produce a plausible plan that watches the wrong things, and the
    teacher would have no way to tell."""
    with pytest.raises(SEL.SelectorUnavailable):
        SEL.draft("build a LAN", None)


# -- back-translation --------------------------------------------------------- #
def test_back_translation_shows_the_model_the_gaps_not_just_the_plan():
    """The teacher most needs to hear what will NOT be observed, so the prompt carries each
    pattern's stated limits."""
    plan = S.assemble(S.Selection(intent="add delay",
                                  patterns=(S.PatternRef("link-delay"),)))
    llm = _llm("Your students' delay setting is watched; the timing they measure is not.")
    text = SEL.back_translate(plan, llm)
    assert "watched" in text
    assert "NO measurement" in llm.prompts[0]


def test_back_translation_is_optional():
    plan = S.assemble(S.Selection(patterns=(S.PatternRef("single-lan"),)))
    assert SEL.back_translate(plan, None) == ""


def test_back_translation_survives_a_failing_model():
    def boom(_p):
        raise TimeoutError()

    plan = S.assemble(S.Selection(patterns=(S.PatternRef("single-lan"),)))
    assert SEL.back_translate(plan, boom) == ""


# -- the catalogue brief ------------------------------------------------------ #
def test_every_pattern_explains_itself_to_the_reasoning_engine():
    for key, pattern in P.CATALOGUE.items():
        assert pattern.observes, f"{key} has no 'observes' description"
        assert pattern.choose_when, f"{key} has no 'choose_when' description"
        assert pattern.not_covered, f"{key} has no 'not_covered' description"


def test_every_parameter_is_explained():
    for key, pattern in P.CATALOGUE.items():
        for p in pattern.params:
            assert pattern.param_help.get(p), f"{key}.{p} has no plain-English meaning"


def test_the_brief_names_every_pattern():
    brief = P.catalogue_brief()
    for key in P.CATALOGUE:
        assert f"### {key}" in brief

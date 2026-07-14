"""Understanding front-door: raw NL -> structured Intent under the different conditions."""
from gini.agent.understand import understand


def test_empty_canvas_construct_serverless_is_build_show_concept():
    it = understand("how to construct a serverless cloud computing setup", canvas_names=[])
    assert it.type == "how_to"
    assert it.anchor == "concept"          # nothing on the canvas
    assert it.output_form == "show"        # "construct" -> materialise it
    assert "serverless" in it.topics


def test_empty_canvas_show_me_a_message_queue():
    it = understand("how to use the message queue - show me with an example", canvas_names=[])
    assert it.output_form == "show"        # explicit "show me ... example"
    assert it.anchor == "concept"
    assert any("queue" in t for t in it.topics)


def test_explain_vpc_with_examples_is_tell_not_autobuild():
    it = understand("explain the use of VPC with examples", canvas_names=[])
    assert it.type == "explain"
    assert it.output_form == "tell"        # describe + offer, do NOT auto-build
    assert "vpc" in it.topics


def test_canvas_diagnose_resolves_ref_and_depth():
    it = understand("why is M3 unreachable?", canvas_names=["M1", "M3", "R1"])
    assert it.type == "diagnose"
    assert it.anchor == "canvas"
    assert "M3" in it.refs
    assert it.depth == "deep"              # "why ..." is a probing question


def test_populated_canvas_generative_is_hybrid():
    it = understand("now how do I add a queue here?", canvas_names=["W1", "DB"])
    assert it.type == "how_to"
    assert it.anchor == "hybrid"           # build onto the existing canvas


def test_build_command_is_build_type():
    it = understand("add a router", canvas_names=[])
    assert it.type == "build"
    assert "router" in it.topics


def test_coach_mode_forces_diagnose():
    it = understand("is this ok", canvas_names=["W1"], mode="coach")
    assert it.type == "diagnose" and it.anchor == "canvas"


def test_wizard_mode_forces_guide():
    it = understand("a serverless api", canvas_names=[], mode="wizard")
    assert it.output_form == "guide" and it.type == "build"


def test_low_confidence_fragment_triggers_llm_refine():
    calls = []

    def stub(prompt):
        calls.append(prompt)
        return 'sure: {"type":"explain","anchor":"concept","output_form":"tell",' \
               '"topics":["serverless"],"depth":"shallow"}'

    it = understand("it", canvas_names=[], llm=stub)
    assert calls, "LLM should be consulted on a low-confidence fragment"
    assert "serverless" in it.topics and it.confidence >= 0.6


def test_confident_input_skips_llm():
    calls = []
    understand("explain serverless", canvas_names=[], llm=lambda p: calls.append(p) or "{}")
    assert not calls          # clear input -> no LLM round-trip

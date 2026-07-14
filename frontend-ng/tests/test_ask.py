"""Ask GINI routing: Intent + retrieval -> Plan (the design's routing matrix)."""
from gini.agent import ask, kb
from gini.agent.understand import understand


def _route(text, canvas_names=None, mode="chat"):
    it = understand(text, canvas_names=canvas_names or [], mode=mode)
    r = kb.retrieve(it)
    return it, ask.plan(it, r)


def test_empty_canvas_construct_autobuilds_recipe():
    it, p = _route("construct a serverless setup", canvas_names=[])
    assert p.action == "build_recipe" and p.recipe_id == "serverless"


def test_show_me_message_queue_autobuilds():
    _, p = _route("show me a message queue example", canvas_names=[])
    assert p.action == "build_recipe" and p.recipe_id == "message_queue"


def test_show_how_to_create_sdn_autobuilds_not_reason():
    # regression: "show how we can create SDN" must build the vetted recipe, not free-reason
    # (which had invented a non-existent 'sdn dashboard' element)
    _, p = _route("Can you show how we can create SDN in GINI?", canvas_names=[])
    assert p.action == "build_recipe" and p.recipe_id == "sdn"


def test_how_to_use_stays_explanatory():
    # "use" (not create/build) stays an explain-with-offer, not an auto-build
    _, p = _route("how to use a message queue", canvas_names=[])
    assert p.action == "reason" and p.offer_build


def test_explain_with_example_reasons_and_offers_build():
    _, p = _route("explain the use of VPC with examples", canvas_names=[])
    assert p.action == "reason" and p.offer_build and p.recipe_id == "vpc_public_private"


def test_populated_canvas_show_offers_before_modifying():
    _, p = _route("show me a serverless setup", canvas_names=["R1", "S1"])
    assert p.action == "reason" and p.offer_build          # don't clobber existing work


def test_build_command_executes():
    _, p = _route("add a router", canvas_names=[])
    assert p.action == "execute"


def test_diagnose_routes_to_diagnose():
    _, p = _route("why is M3 unreachable", canvas_names=["M3"])
    assert p.action == "diagnose"


def test_meta_and_chitchat():
    _, p1 = _route("what can you do", canvas_names=[])
    assert p1.action == "meta"
    _, p2 = _route("hi", canvas_names=[])
    assert p2.action == "chitchat"


def test_grounded_context_includes_all_slots():
    it = understand("explain serverless", canvas_names=[])
    r = kb.retrieve(it)
    ctx = ask.grounded_context(kb.always_on_context(), "Session knowledge so far: earlier",
                               r, "Topology: 0 devices.", it)
    assert "GINI elements" in ctx                       # always-on
    assert "Current canvas" in ctx                      # canvas digest
    assert "Session knowledge so far" in ctx            # accumulator
    assert "Relevant GINI knowledge" in ctx             # this turn's cards

"""The Reasoning Twin auditing a drafted observation plan.

The Twin is a challenger, not a judge: it can raise questions about a plan and can never change it.
So the tests split cleanly. Some assert it SPEAKS when a teacher would want to be asked; the rest —
the more important half — assert it stays QUIET, because a challenger that fires on a good plan
teaches people to dismiss it.

Deterministic throughout: concerns come from the teacher's words, the catalogue's own declared
descriptions, and the assembled plan. No model is consulted to decide what matters.
"""
from __future__ import annotations

from gini.agent import aop_selector as SEL
from gini.agent.twin import Twin, aop_concerns
from gini.agent.twin.contracts import Coverage
from gini.domain import aop as A
from gini.domain import aop_assemble as S

SENSIBLE = "Build a few LANs joined by routers and show cross-LAN reachability."


def concerns(intent, *patterns):
    sel = S.Selection(intent=intent, patterns=tuple(S.PatternRef(k) for k in patterns),
                      params={"starting_point": A.BLANK})
    return aop_concerns(intent, S.assemble(sel, validate_plan=False), sel)


def ids(cs):
    return {c.id for c in cs}


# -- staying quiet ------------------------------------------------------------ #
def test_a_good_plan_raises_nothing():
    """The most important test here. A challenger that fires on a correct plan trains teachers to
    ignore it, and then it is worse than absent."""
    assert concerns(SENSIBLE, "multi-lan") == []


def test_a_correctly_paired_plan_raises_nothing():
    assert concerns("Add delay to a router in a multi-LAN network and see what changes.",
                    "multi-lan", "link-delay") == []


def test_a_near_tie_is_not_worth_asking_about():
    """One extra matching word is not evidence of a mistake."""
    assert not any(c.id.startswith("aop:unchosen") for c in concerns(SENSIBLE, "multi-lan"))


def test_enumeration_never_raises_on_a_broken_plan():
    """An audit that could take the draft down would make the safety feature the least safe part
    of the pipeline."""
    assert aop_concerns("anything", object(), None) == []


# -- speaking up -------------------------------------------------------------- #
def test_it_asks_why_not_when_the_words_point_elsewhere():
    cs = concerns("Have them add delay to a router on the path and think about the round trip.",
                  "single-lan")
    assert "aop:unchosen:link-delay" in ids(cs)
    assert "matching terms" in next(c for c in cs if c.id.endswith("link-delay")).evidence


def test_it_names_what_the_chosen_pattern_will_not_watch():
    """The concern with no analogue in the Twin's other surfaces. Every pattern declares its own
    limits, so a teacher can be told at ratify time rather than by a student's silent report."""
    cs = concerns("They should capture packets with tcpdump and read the TTL and MAC "
                  "rewriting at each hop.", "multi-lan")
    blind = next(c for c in cs if c.id == "aop:blind-spot:multi-lan")
    assert "ttl" in blind.evidence and "mac" in blind.evidence


def test_a_pattern_being_right_does_not_hide_its_gaps():
    """multi-lan says BOTH 'pick me when they mention TTL' and 'I do not observe TTL'. Filtering
    that overlap away looks tidier and silences the most useful thing the Twin can say."""
    cs = concerns("Show the TTL falling and the MAC rewriting at each hop.", "multi-lan")
    assert "aop:blind-spot:multi-lan" in ids(cs)


def test_a_plan_with_nothing_live_is_urgent():
    cs = concerns("Build a multi-LAN network.", "link-delay")
    live = next(c for c in cs if c.id == "aop:no-behavioural")
    assert live.salience == 3          # objected to even when the model reports no coverage


def test_a_plan_with_nothing_structural_is_raised():
    """Behaviour alone cannot tell a built network from a borrowed one."""
    plan = A.Aop(header=A.Header(patterns=("multi-lan",)),
                 expectations=(A.Expectation(id="b", say="all reach", layer="L3",
                                             probe="reach(host -> host, all) == ok"),))
    assert "aop:no-structural" in ids(aop_concerns("reachability", plan))


def test_advisory_text_is_not_read_as_an_exclusion():
    """`not_covered` often ends 'Pair this with multi-lan…'. Those words describe a REMEDY; reading
    them as a gap made link-delay look blind to the very thing its companion covers."""
    cs = concerns("Build a multi-LAN network.", "link-delay")
    assert "aop:blind-spot:link-delay" not in ids(cs)


# -- salience and caps -------------------------------------------------------- #
def test_concerns_are_capped_so_the_twin_whispers():
    from gini.agent.twin.salience import MAX_CONCERNS
    cs = concerns("routers switches hosts delay latency captures ttl mac arp subnets "
                  "reachability multicast firewalls", "link-delay")
    assert len(cs) <= MAX_CONCERNS


def test_every_concern_carries_deterministic_evidence():
    """The Twin may only cite what GINI can prove."""
    for c in concerns("Have them add delay to a router.", "single-lan"):
        assert c.evidence and c.source


# -- the audit ---------------------------------------------------------------- #
def test_a_silent_miss_becomes_an_objection():
    cs = concerns("Have them add delay to a router on the path.", "single-lan")
    objections = Twin().audit(cs, Coverage(addressed=frozenset(), omitted={}), None)
    assert [o.concern.id for o in objections] == [c.id for c in cs if c.salience >= 2]


def test_a_reported_concern_is_not_objected_to():
    cs = concerns("Have them add delay to a router on the path.", "single-lan")
    covered = Coverage(addressed=frozenset(c.id for c in cs), omitted={})
    assert Twin().audit(cs, covered, None) == []


def test_coverage_silence_objects_only_about_the_urgent_tier():
    """A model that returns no coverage report at all must not be nagged about everything — the
    Twin objects only where a rule is actually being broken."""
    cs = concerns("Build a multi-LAN network.", "link-delay")
    objections = Twin().audit(cs, None, None)
    assert {o.concern.salience for o in objections} == {3}


# -- the selector's integration ----------------------------------------------- #
def _llm(*replies):
    seen = []

    def call(prompt):
        seen.append(prompt)
        return replies[min(len(seen) - 1, len(replies) - 1)]

    call.prompts = seen
    return call


def _reply(patterns, coverage=None, note=""):
    import json
    body = {"patterns": [{"key": k} for k in patterns], "note": note}
    if coverage is not None:
        body["coverage"] = coverage
    return json.dumps(body)


def test_the_twin_can_be_switched_off_entirely():
    """With it off the draft must be exactly what it was before the Twin existed."""
    d = SEL.draft("add delay to a router", _llm(_reply(["single-lan"])), twin=False)
    assert d.ok and d.objections == []


def test_a_silent_draft_gets_one_revision_round():
    """No concerns exist until there IS a plan, so the enumeration can only reach the model on a
    second turn. That is the Twin's designed shape, not a workaround."""
    llm = _llm(_reply(["single-lan"]), _reply(["single-lan", "link-delay"]))
    d = SEL.draft("Have them add delay to a router on the path.", llm)
    assert len(llm.prompts) == 2
    assert "aop:unchosen:link-delay" in llm.prompts[1]


def test_a_revision_that_fixes_the_gap_clears_the_objection():
    llm = _llm(_reply(["single-lan"]), _reply(["single-lan", "link-delay"]))
    d = SEL.draft("Have them add delay to a router on the path.", llm)
    assert d.ok and d.objections == []
    assert [p.key for p in d.selection.patterns] == ["single-lan", "link-delay"]


def test_an_objection_the_model_ignores_twice_reaches_the_teacher():
    llm = _llm(_reply(["single-lan"]))            # never changes its mind
    d = SEL.draft("Have them add delay to a router on the path.", llm)
    assert d.ok                                    # still a usable plan — a challenger, not a gate
    assert [o.concern.id for o in d.objections] == ["aop:unchosen:link-delay"]


def test_a_justified_omission_is_adjudicated_not_ignored():
    llm = _llm(_reply(["single-lan"],
                      coverage={"addressed": [],
                                "omitted": [{"id": "aop:unchosen:link-delay",
                                             "why": "the delay work is next week's lab"}]}))
    d = SEL.draft("Have them add delay to a router on the path.", llm)
    assert d.ok and not d.coverage_silent


def test_coverage_silence_is_reported_not_hidden():
    d = SEL.draft("Have them add delay to a router on the path.", _llm(_reply(["single-lan"])))
    assert d.coverage_silent


def test_the_twin_never_changes_a_good_plan():
    llm = _llm(_reply(["multi-lan"]))
    d = SEL.draft(SENSIBLE, llm)
    assert [p.key for p in d.selection.patterns] == ["multi-lan"]
    assert len(llm.prompts) == 1                   # nothing to raise, so no revision round


def test_concerns_for_exposes_what_was_weighed():
    """Shown beside the objections so a teacher sees what was considered, not only what went
    unanswered — the difference between a conversation and a checklist."""
    sel = S.Selection(intent="add delay to a router",
                      patterns=(S.PatternRef("single-lan"),),
                      params={"starting_point": A.BLANK})
    assert any(c.id.startswith("aop:") for c in SEL.concerns_for("add delay to a router", sel))


# -- the ratify conversation -------------------------------------------------- #
# The teacher never edits the plan; they talk about it. So the whole conversation is replayed on
# every turn rather than the plan being patched — which is also what lets a later remark undo an
# earlier one, the way people actually revise.
def test_feedback_reaches_the_model():
    llm = _llm(_reply(["multi-lan"]))
    SEL.draft("build LANs", llm, feedback=["also watch what happens when a link fails"])
    assert "THE TEACHER HAS SINCE SAID" in llm.prompts[0]
    assert "link fails" in llm.prompts[0]


def test_the_whole_conversation_is_replayed_not_just_the_last_remark():
    llm = _llm(_reply(["multi-lan"]))
    SEL.draft("build LANs", llm, feedback=["watch the delay", "and the multicast"])
    assert "watch the delay" in llm.prompts[0] and "multicast" in llm.prompts[0]


def test_the_conversation_is_numbered_oldest_first():
    """The model is told a later remark wins; the ordering has to be visible for that to mean
    anything, and the console shows the teacher the same order."""
    llm = _llm(_reply(["multi-lan"]))
    SEL.draft("build LANs", llm, feedback=["first thing", "second thing"])
    p = llm.prompts[0]
    assert p.index("1. first thing") < p.index("2. second thing")
    assert "the later one wins" in p


def test_blank_remarks_are_dropped():
    llm = _llm(_reply(["multi-lan"]))
    SEL.draft("build LANs", llm, feedback=["", "   ", "real one"])
    assert "1. real one" in llm.prompts[0]


def test_no_feedback_means_no_conversation_block():
    """A first draft must look exactly as it did before the loop existed."""
    llm = _llm(_reply(["multi-lan"]))
    SEL.draft("build LANs", llm)
    assert "THE TEACHER HAS SINCE SAID" not in llm.prompts[0]


def test_feedback_and_answers_coexist():
    """Clarifying answers and ratify remarks are different things and must both survive."""
    llm = _llm(_reply(["multi-lan"]))
    SEL.draft("build LANs", llm, answers=({"q": "how many?", "a": "three"},),
              feedback=["also the delay"])
    assert "how many?" in llm.prompts[0] and "also the delay" in llm.prompts[0]

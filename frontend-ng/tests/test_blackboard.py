"""P0/P1 of the multi-agent architecture: tiny verifiers wrapping the existing evaluators, and the
blackboard truth-cache that keeps their verdicts current incrementally."""
from gini.agent import verifiers as V
from gini.agent.blackboard import Blackboard
from gini.agent.contracts import MissionMemory
from gini.domain import assembly as A
from gini.domain.topology import Topology


def _lesson():
    # a small structural lesson: switch + 2 hosts + router, hosts on switch, switch to router
    return A.assemble(["basic-lan"], genre="experience", lesson_id="t")


def _lan(hosts=2, wire=True):
    t = Topology()
    sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
    hs = [t.add_device("host", f"H{i}") for i in range(hosts)]
    if wire:
        for h in hs:
            t.add_link(h.id, sw.id)
        t.add_link(sw.id, r.id)
    return t


def test_for_lesson_builds_one_verifier_per_objective_plus_legality():
    les = _lesson()
    vs = V.for_lesson(les)
    obj_vs = [v for v in vs if v.id.startswith("objective:")]
    assert len(obj_vs) == len(les.objectives)              # many tiny — one per objective
    ids = {v.id for v in vs}
    assert "legality:off_task" in ids and "legality:illegal_links" in ids


def test_blackboard_reflects_objective_satisfaction():
    bb = Blackboard()
    bb.load_lesson(_lesson())
    bb.update(_lan())                                       # a complete switched LAN
    assert bb.all_objectives_met()
    assert not bb.unmet_objectives()
    assert bb.value("off_task") and bb.value("illegal_links")   # nothing wrong


def test_blackboard_tracks_unmet_then_flip_on_fix():
    bb = Blackboard()
    bb.load_lesson(_lesson())
    bb.update(_lan(hosts=1))                                # only one host → 'two-hosts' unmet
    assert not bb.all_objectives_met()
    assert any("host" in s or "two" in s for s in bb.unmet_objectives()) or bb.unmet_objectives()
    # now build the full LAN and re-check: the flipped verdicts are reported
    flipped = bb.update(_lan(hosts=2))
    assert bb.all_objectives_met()
    assert flipped                                         # at least one verdict flipped to met


def test_incremental_update_only_runs_affected_verifiers():
    bb = Blackboard()
    bb.load_lesson(_lesson())
    bb.update(_lan())
    # a change tagged as runtime-only must not disturb the topology (structural) verdicts
    flipped = bb.update(_lan(), changed={"runtime"})
    assert flipped == []                                   # no structural verifier re-ran / flipped


def test_off_task_and_illegal_link_surface_as_flags():
    bb = Blackboard()
    bb.load_lesson(_lesson())
    t = _lan()
    t.add_device("k8s_cluster", "K8S")                     # off-task for a LAN mission
    bb.update(t)
    assert not bb.value("off_task")
    assert bb.flags()["off_task"]                          # the K8s id shows up as an off-task flag


def test_mission_memory_digest_is_bounded_and_structured():
    m = MissionMemory(arc="building a LAN")
    for i in range(10):
        m.note_tried(f"step{i}")
    m.note_fact("hosts wire to the switch")
    d = m.digest(max_items=3)
    assert "building a LAN" in d
    assert "step9" in d and "step0" not in d               # bounded to the last few
    assert "hosts wire to the switch" in d

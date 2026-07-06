"""Multi-turn (guided) missions: a lesson's `steps` are presented one beat at a time, each
advancing on the right kind of student action (drop/connect → structural, run → behavioral,
read/reflect → reply), and objective completion does NOT end a guided mission early — the whole
beat path runs. Objectives remain the win conditions / band source."""
from gini.agent.mission import Mission
from gini.agent.mission_controller import MissionController
from gini.domain import lesson as L, objectives as O, probes as P
from gini.domain.topology import Topology

_GUIDED = {
    "id": "lab01g", "title": "A basic switched LAN", "time_limit": "20m",
    "intent": {"concept": "networking-basics", "spirit": "a switched LAN with a gateway"},
    "objectives": [
        {"id": "has-switch", "say": "switch", "kind": "structural", "check": "exists(switch)"},
        {"id": "two-hosts", "say": "two hosts", "kind": "structural", "check": "count(host) >= 2"},
        {"id": "has-gw", "say": "router", "kind": "structural", "check": "exists(router)"},
        {"id": "reach", "say": "connected", "kind": "structural", "check": "connected(M1, M2)"},
    ],
    "steps": [
        {"say": "Drop a switch.", "advance": "exists(switch)"},
        {"say": "Add two hosts wired to the switch.", "advance": "count(host) >= 2 and connected(M1, S1)"},
        {"say": "Add a router gateway.", "advance": "exists(router) and connected(S1, R1)"},
        {"say": "Why did the switch matter?", "advance": "reply"},
    ],
    "complete_when": "all",
}


def _guided():
    return L.from_dict(_GUIDED)


# -- lesson-level --------------------------------------------------------- #
def test_steps_parse_and_classify():
    les = _guided()
    assert les.guided and len(les.steps) == 4
    kinds = [s.kind() for s in les.steps]
    assert kinds == ["structural", "structural", "structural", "reply"]
    assert L.is_valid(les)


def test_step_advance_predicate_is_validated():
    bad = L.from_dict({"id": "x", "objectives": [{"id": "o", "kind": "structural", "check": "exists(switch)"}],
                       "steps": [{"say": "do", "advance": "notvalid("}]})
    assert any("advance predicate does not parse" in p for p in L.validate(bad))


def test_lesson_without_steps_is_free_form():
    les = L.from_archetype("basic-lan", {"h1": "M1", "h2": "M2", "sw": "S1", "gw": "R1"}, id="f")
    assert not les.guided


# -- mission-level -------------------------------------------------------- #
def test_step_satisfied_and_advance():
    m = Mission(_guided(), now=lambda: 0.0); m.start()
    t = Topology()
    assert not m.step_satisfied(O.TopologyWorld(t))          # nothing dropped yet
    t.add_device("switch", "S1")
    assert m.step_satisfied(O.TopologyWorld(t))              # switch dropped
    m.advance_step()
    assert m.step_number() == (2, 4)


def test_guided_mission_not_ended_by_early_objective_completion():
    m = Mission(_guided(), now=lambda: 0.0); m.start()
    t = Topology()
    m1 = t.add_device("host", "M1"); m2 = t.add_device("host", "M2")
    s1 = t.add_device("switch", "S1"); r1 = t.add_device("router", "R1")
    t.add_link(m1.id, s1.id); t.add_link(m2.id, s1.id); t.add_link(s1.id, r1.id)
    m.evaluate(O.TopologyWorld(t))                            # all OBJECTIVES met…
    assert m.complete                                        # …win conditions true…
    assert m.state == "playing" and not m.steps_done         # …but guided path isn't done, so still playing


# -- controller (the multi-turn experience) ------------------------------- #
class _LLM:
    def __call__(self, prompt):
        return "a phrased line."


def _run_guided():
    topo = Topology(); posts = []
    c = MissionController(get_topology=lambda: topo, llm=_LLM(),
                          post=lambda r, t: posts.append((r, t)), now=lambda: 0.0)
    c.start(_guided())
    return c, topo, posts


def test_presents_one_beat_at_a_time_then_walks_the_path():
    c, topo, posts = _run_guided()
    assert c.mission.step_number() == (1, 4)                  # only the first beat presented
    gini_posts_after_start = sum(1 for r, _ in posts if r == "GINI")
    assert gini_posts_after_start == 1                        # not a wall of instructions

    s1 = topo.add_device("switch", "S1"); c.on_canvas_changed()
    assert c.mission.step_number() == (2, 4)
    m1 = topo.add_device("host", "M1"); m2 = topo.add_device("host", "M2")
    topo.add_link(m1.id, s1.id); topo.add_link(m2.id, s1.id); c.on_canvas_changed()
    assert c.mission.step_number() == (3, 4)
    r1 = topo.add_device("router", "R1"); topo.add_link(s1.id, r1.id); c.on_canvas_changed()
    assert c.mission.step_number() == (4, 4) and not c.mission.steps_done   # reached the reflect beat
    assert c.mission.state == "playing"                      # not ended despite objectives complete

    c.ask("a switch learns MAC addresses and forwards selectively")
    assert c.mission.steps_done and c.mission.state == "done" and c.mission.last_band == "gold"


def test_behavioral_beat_advances_on_run():
    lesson = L.from_dict({
        "id": "labb", "time_limit": "20m",
        "intent": {"concept": "vpc-networking", "spirit": "reachability"},
        "objectives": [{"id": "reach", "say": "reaches", "kind": "behavioral",
                        "probe": "reach(WEB1 -> DB1) == ok"}],
        "steps": [{"say": "Wire the app to the db, then press Run.",
                   "advance": "reach(WEB1 -> DB1) == ok"}],
        "complete_when": "all"})
    topo = Topology(); topo.add_device("web_app", "WEB1"); topo.add_device("database", "DB1")
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True})
    posts = []
    c = MissionController(get_topology=lambda: topo, llm=_LLM(),
                          post=lambda r, t: posts.append((r, t)),
                          make_runner=lambda: runner, now=lambda: 0.0)
    c.start(lesson)
    c.on_canvas_changed()                                    # canvas change alone doesn't advance a run-beat
    assert not c.mission.steps_done
    c.run_check()                                            # Run → behavioral beat advances → done
    assert c.mission.steps_done and c.mission.state == "done"

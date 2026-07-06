"""The MissionController coordinates the loop UI-agnostically: it gates on a model, briefs,
reacts to canvas changes (live + game-master lines), runs behavioral checks, records to the
profile on completion, and routes student questions to the game master."""
from gini.agent.mission_controller import MissionController
from gini.domain import lesson as L, probes as P, profile as PR
from gini.domain.topology import Topology


class ScriptedLLM:
    def __init__(self, interpret='{"progress":"asking_hint","objective_ref":"","is_question":true}'):
        self.interpret = interpret

    def __call__(self, prompt):
        return self.interpret if "ONLY as JSON" in prompt else "a line."


def _lesson(**o):
    return L.from_archetype("reachability-boundary",
                            {"inside": "WEB1", "protected": "DB1", "outsider": "NET", "box": "VPC1"},
                            id="lab03", time_limit="25m", **o)


def _topo():
    t = Topology(); v = t.add_device("vpc", "VPC1")
    t.add_device("web_app", "WEB1", parent_id=v.id)
    t.add_device("database", "DB1", parent_id=v.id)
    return t


def _controller(topo, llm, profile=None, runner=None):
    posts = []
    c = MissionController(
        get_topology=lambda: topo, llm=llm,
        post=lambda role, text: posts.append((role, text)),
        make_runner=(lambda: runner), profile=profile, now=lambda: 0.0)
    return c, posts


def test_requires_a_model():
    c, posts = _controller(Topology(), llm=None)
    assert not c.available()
    assert c.start(_lesson()) is False
    assert any("need a local model" in t for _, t in posts)


def test_start_briefs_and_begins():
    c, posts = _controller(_topo(), ScriptedLLM())
    assert c.start(_lesson()) is True
    assert c.active and c.mission.state == "playing"
    assert posts and posts[0][0] == "GINI"           # briefed


def test_run_check_completes_and_records_to_profile():
    prof = PR.Profile("s1")
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    c, posts = _controller(_topo(), ScriptedLLM(), profile=prof, runner=runner)
    c.start(_lesson())
    score = c.run_check()
    assert score.band == "gold"
    assert c.mission.state == "done"
    assert prof.lessons["lab03"].best_band == "gold"    # recorded on finish
    assert prof.lessons["lab03"].completed


def test_records_only_once():
    prof = PR.Profile("s1")
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    c, _ = _controller(_topo(), ScriptedLLM(), profile=prof, runner=runner)
    c.start(_lesson())
    c.run_check()
    attempts_after_first = prof.lessons["lab03"].attempts_used
    c.on_canvas_changed()                                # further events must not double-record
    assert prof.lessons["lab03"].attempts_used == attempts_after_first


def test_student_question_routes_to_gamemaster():
    # help=full_tutor_logged so a question yields an ANSWER line
    c, posts = _controller(_topo(), ScriptedLLM(), )
    c.start(_lesson(help="full_tutor_logged"))
    before = len(posts)
    c.ask("how do I make the db reachable?")
    assert len(posts) > before and posts[-1][0] == "GINI"

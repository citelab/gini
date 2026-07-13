"""MissionController — the coordinator that runs a live Mission inside GINI.

Ties together the pieces built in Phases 1–4: the Mission state machine, the objective engine
(with a Runner for behavioral probes), the GameMaster reasoning loop, the MissionPanel HUD, and
the student Profile. It's the "brain" the UI drives.

Kept UI-agnostic and fully testable by taking its couplings as callables:
  • get_topology()          → the live topology (source of the world); a `get_world` override may
                              be passed instead (e.g. a snapshot world) so the game master's
                              reasoning can run off the UI thread without touching the live canvas
  • make_runner()           → a probes.Runner for behavioral eval on Run/Check (or None)
  • post(role, text)        → deliver a game-master line to the chat
  • llm(prompt)->str        → the reasoning model (required; Missions are LLM-gated)
  • on_change(cb)           → subscribe to canvas changes (returns unsubscribe or None)
  • panel                   → an optional MissionPanel to refresh (any object with refresh/…)

Flow: `start(lesson)` → brief. Canvas changes → live structural refresh + a game-master
observation (warmer/colder, nudges). `run_check()` → behavioral probes via the runner, witness,
record to the profile, and a celebrate/defeat line. `ask(text)` → a game-master reply.
"""
from __future__ import annotations

from ..domain import objectives as _obj
from .gamemaster import GameMaster, QUIET
from .mission import Mission


class MissionController:
    def __init__(self, *, llm, post, get_topology=None, get_world=None, make_runner=None,
                 panel=None, profile=None, now=None, gm_factory=None) -> None:
        self.get_topology = get_topology
        self._get_world = get_world             # optional: a snapshot world (thread-safe reasoning)
        self.llm = llm
        self.post = post
        self.make_runner = make_runner or (lambda: None)
        self.panel = panel
        self.profile = profile
        self._now = now
        self._gm_factory = gm_factory or GameMaster   # swappable brain (AgentGameMaster in prod)
        self.mission: Mission | None = None
        self.gm: GameMaster | None = None
        self._recorded = False

    @property
    def active(self) -> bool:
        return self.mission is not None and self.mission.state != "done"

    def available(self) -> bool:
        """Missions require a reasoning LLM (hard gate)."""
        return self.llm is not None

    # -- lifecycle ---------------------------------------------------------- #
    def start(self, lesson) -> bool:
        if not self.available():
            self.post("GINI", "Missions need a local model — enable one in Settings.")
            return False
        self.mission = Mission(lesson) if self._now is None else Mission(lesson, now=self._now)
        self.gm = self._gm_factory(lesson, llm=self.llm)
        if hasattr(self.gm, "bind_world"):              # let the game master ground diagnosis (explainer)
            self.gm.bind_world(self._world)
        self._recorded = False
        self.mission.brief()
        self.mission.start()
        if self.panel is not None:
            self.panel.set_mission(self.mission)
        if self.mission.guided:
            # multi-turn: present just the first beat, not the whole wall of objectives
            self._advance_guided(self._world())     # skip any beats already satisfied
            self._present_current_step()
        else:
            self.post("GINI", self.gm.brief_line() or lesson.brief)   # free-form: the brief
        self._refresh_live()
        return True

    # -- guided-beat driving ------------------------------------------------ #
    def _present_current_step(self, acked: str = "") -> None:
        step = self.mission.current_step()
        if step is None:
            return
        idx, total = self.mission.step_number()
        self.post("GINI", self.gm.present_step(step, idx, total, acked=acked))
        if self.panel is not None and hasattr(self.panel, "set_step"):
            self.panel.set_step(step.say, idx, total)

    def _advance_guided(self, world, *, replied: bool = False, runner=None) -> bool:
        """Advance through every currently-satisfied beat, presenting each one we land on. Returns
        True if we moved. A `replied`/`runner` lets reply/behavioral beats advance."""
        moved = False
        while not self.mission.steps_done and self.mission.step_satisfied(world, runner, replied):
            prev = self.mission.current_step()
            self.mission.advance_step()
            replied = False                         # a reply advances only the current beat
            moved = True
            if self.mission.steps_done:
                if self.panel is not None and hasattr(self.panel, "clear_step"):
                    self.panel.clear_step()
                self.mission.evaluate(world, runner)     # guided path done → witness → band
                break
            self._present_current_step(acked=prev.say)
        return moved

    def _maybe_finish(self) -> bool:
        """If the attempt has been witnessed, deliver the game master's closing line and record it."""
        if self.mission is not None and self.mission.state == "done":
            self._speak(self.gm.decide(self.mission, self.mission.last_results))
            self._finish()
            return True
        return False

    def _world(self):
        if self._get_world is not None:
            return self._get_world()
        return _obj.TopologyWorld(self.get_topology())

    # -- canvas changed → live structural eval + a game-master observation --- #
    def on_canvas_changed(self) -> None:
        if not self.active:
            return
        world = self._world()
        results = self.mission.evaluate(world)                # structural live (no runner)
        if self.panel is not None:
            self.panel.render_current()
        if self._maybe_finish():                              # free-form win / expiry
            return
        if self.mission.guided:
            self._advance_guided(world)                       # a drop/connect advanced a beat?
            self._maybe_finish()                              # ...maybe that finished the path
        else:
            self._speak(self.gm.decide(self.mission, results))  # free-form warmer/colder/nudge

    def _refresh_live(self) -> None:
        if self.mission is None:
            return
        self.mission.evaluate(self._world())
        if self.panel is not None:
            self.panel.render_current()

    # -- Run/Check → behavioral probes, witness, record --------------------- #
    def run_check(self):
        if self.mission is None:
            return None
        world = self._world()
        runner = self.make_runner()
        # tell the student WHY nothing changed when the live checks can't run (no running stack)
        if self.mission.lesson.behavioral_ids() and (
                runner is None or not getattr(runner, "available", lambda: False)()):
            self.post("GINI", "The live checks (⏳) need the running system — start the topology with "
                              "the ▶ Run button on the toolbar first, then press Run / Check again. "
                              "Your build already meets the objectives to complete this mission.")
        score = self.mission.check(world, runner)
        if self.panel is not None:
            self.panel.render_current()
        if self.mission.guided and not self.mission.steps_done:
            self._advance_guided(world, runner=runner)        # a run advanced a behavioral beat?
        # A DETERMINISTIC summary of the live checks, straight from the probe verdicts — the oracle,
        # not the model, so it can NEVER contradict the objective panel (kills the 'connection test
        # successful' hallucination when a live check actually failed).
        live = [r for r in self.mission.last_results if r.kind == "behavioral"]
        if live:
            mark = {"met": "✓", "unmet": "✗", "pending": "⏳"}
            self.post("GINI", "Live checks — "
                      + "; ".join(f"{mark.get(r.status, '?')} {r.say}" for r in live))
        if not self._maybe_finish():
            failed = [r.say for r in live if not r.met]
            if failed and hasattr(self.gm, "run_note"):
                self.post("GINI", self.gm.run_note(failed))   # grounded: names what FAILED, no false wins
            else:
                self._speak(self.gm.decide(self.mission, self.mission.last_results))
        return score

    # -- a student message during a mission --------------------------------- #
    def ask(self, text: str) -> None:
        if not self.active:
            return
        world = self._world()
        results = self.mission.evaluate(world)
        if self.panel is not None:
            self.panel.render_current()
        step = self.mission.current_step() if self.mission.guided else None
        if step is not None and step.kind() == "reply":
            # this beat wanted the student to reply (read/reflect) → respond + advance
            self.post("GINI", self.gm.react_reply(step, text, results))
            self._advance_guided(world, replied=True)
            self._maybe_finish()
            return
        # otherwise it's a question/hint mid-beat (or free-form) → the game master reasons
        self._speak(self.gm.decide(self.mission, results, utterance=text))
        if self.mission.guided and not self.mission.steps_done:
            self._advance_guided(world)                        # maybe they described an action
            self._maybe_finish()

    # -- helpers ------------------------------------------------------------ #
    def _speak(self, move) -> None:
        if move is not None and move.kind != QUIET and move.text:
            self.post("GINI", move.text)

    def _finish(self) -> None:
        if self._recorded or self.profile is None or self.mission is None:
            return
        self.profile.record(self.mission.lesson, self.mission)
        self._recorded = True

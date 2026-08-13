"""The reasoning eval harness (REASONING_2.0_DESIGN.md phase E).

Replays a bank of GOLDEN TURNS — (world setup, a scripted model, a trigger, expectations) —
through the real MissionAgent+Twin and aggregates the metrics the design names: addressed-rate
of must-address concerns, coverage-silence rate, false-objection rate, flag rate. The harness
tests the TWIN deterministically (scripted models make every run reproducible) and gives prompt/
model changes a regression gate: run the same bank, compare the report.

A `false objection` is an objection raised on a turn whose golden expectation says the model's
coverage was complete/justified — i.e. the Twin nagged when it shouldn't have. With scripted
models this is fully deterministic, so the false-objection rate here is a check on the TWIN's
rules; against a live model (Mac-side) the same report measures the MODEL instead."""
from __future__ import annotations

from dataclasses import dataclass, field

from .salience import MUST_ADDRESS


@dataclass
class GoldenTurn:
    """One replayable turn: `make_agent(llm)` builds the MissionAgent (world + twin inside);
    `llm` is the scripted model; `trigger`/`utterance` wake it. Expectations are about the
    TWIN's behavior, not prose."""
    name: str
    make_agent: object                  # callable(llm) -> MissionAgent (with a Twin)
    llm: object                         # scripted callable(prompt) -> str
    trigger: object = None
    utterance: str = ""
    expect_flags: bool = False          # should the turn end with a visible flag?
    expect_objections: bool = False     # should the FIRST audit raise objections?
    expect_clean: bool = False          # golden says coverage was complete/justified


@dataclass
class TurnOutcome:
    name: str
    ok: bool
    flags: bool
    objections: int
    surviving: int
    rounds: int
    coverage_silent: bool
    false_objection: bool
    notes: str = ""


@dataclass
class HarnessReport:
    outcomes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(o.ok for o in self.outcomes)

    def metrics(self) -> dict:
        n = len(self.outcomes) or 1
        addressed = [o for o in self.outcomes if not o.surviving]
        return {
            "turns": len(self.outcomes),
            "pass_rate": sum(o.ok for o in self.outcomes) / n,
            "addressed_rate": len(addressed) / n,      # must-address fully covered by turn end
            "silence_rate": sum(o.coverage_silent for o in self.outcomes) / n,
            "false_objection_rate": sum(o.false_objection for o in self.outcomes) / n,
            "flag_rate": sum(o.flags for o in self.outcomes) / n,
            "mean_rounds": sum(o.rounds for o in self.outcomes) / n,
        }


def replay(turns: list[GoldenTurn]) -> HarnessReport:
    """Run every golden turn through the real orchestration; never raises — a broken turn is a
    failed outcome with a note, so the CI report always renders."""
    report = HarnessReport()
    for t in turns:
        try:
            agent = t.make_agent(t.llm)
            move = agent.turn(t.trigger, utterance=t.utterance)
            res = agent.last_twin_result
            flags = "Also worth a look" in (move.text or "")
            objections = len(res.objections) if res else 0
            surviving = len(res.surviving) if res else 0
            silent = bool(res and res.coverage_silent)
            rounds = res.rounds if res else 0
            false_obj = bool(t.expect_clean and objections)
            ok = (flags == t.expect_flags
                  and (objections > 0) == t.expect_objections
                  and not false_obj)
            report.outcomes.append(TurnOutcome(
                t.name, ok, flags, objections, surviving, rounds, silent, false_obj))
        except Exception as e:                          # noqa: BLE001 — report, don't die
            report.outcomes.append(TurnOutcome(
                t.name, False, False, 0, 0, 0, False, False, notes=f"error: {e}"))
    return report

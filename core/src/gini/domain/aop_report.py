"""What the teacher reads — findings, with every negative attributed to whoever earned it.

The AOP is a hidden, fixed instrument applied to free-form work. That combination has a specific
failure mode: a student solves the problem in a way the plan did not anticipate, the plan observes
nothing, and the report reads as if they did nothing. So a single "unmet" column is not enough. A
negative has to say *whose* fault it is:

    met            observed satisfied                                   the student
    unmet          observable, and not satisfied                        the student
    blocked        a prerequisite failed, so this was never evaluated   a consequence, not a finding
    unobservable   the preconditions never held                         the lab, or the plan
    defective      the expectation itself is broken                     the PLAN's author

Only the first two are ever about the student. `blocked` exists so a broken L2 link produces one
finding and N consequences rather than N+1 failures.

**`unexplained` is NOT a verdict** — it is `Report.unexplained`, a separate list of (type, count)
pairs for work the student did that no expectation mentions. It cannot be a verdict because it
belongs to no expectation; that is the whole point of it. Treating it as the fifth verdict is an
easy mistake and an expensive one: a UI built from a five-item list that includes it drops
`defective`, which is the verdict that says the plan itself is broken. It is the fairness backstop,
and without it a hidden fixed plan systematically penalises unusual-but-correct solutions — becoming
the bias it was built to remove.

Pure: no Qt, no Docker, no model. An LLM may render this into friendlier prose, but it never
produces a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import objectives as _obj
from .aop import evaluation_order

MET = "met"
UNMET = "unmet"
BLOCKED = "blocked"
UNOBSERVABLE = "unobservable"
DEFECTIVE = "defective"

#: Verdicts that mean the expectation was satisfied, for dependency purposes.
_SATISFIED = frozenset({MET})

#: Element types that are drawn organisation rather than network function. A student who groups
#: their work in a VPC box has not built an unobserved *thing*, so flagging it as unexplained work
#: would be noise — and a report that cries wolf gets skimmed.
COSMETIC_TYPES = frozenset({"vpc", "cloud_subnet", "region", "security_group", "instance_group"})


@dataclass(frozen=True)
class Finding:
    """One expectation's outcome, with enough context to act on it."""
    id: str
    say: str
    layer: str
    verdict: str
    detail: str = ""
    blocked_by: tuple = ()
    pattern: str = ""

    @property
    def about_the_student(self) -> bool:
        """Whether this finding says anything about the person's work. `blocked`, `unobservable`
        and `defective` do not, and must never be totted up as failures."""
        return self.verdict in (MET, UNMET)


@dataclass(frozen=True)
class Report:
    plan_hash: str
    findings: tuple = ()
    unexplained: tuple = ()          # (type_key, count) the plan never mentions
    within_deadline: bool = True

    def count(self, verdict: str) -> int:
        return sum(1 for f in self.findings if f.verdict == verdict)

    @property
    def counts(self) -> dict:
        return {v: self.count(v)
                for v in (MET, UNMET, BLOCKED, UNOBSERVABLE, DEFECTIVE)}

    @property
    def plan_is_sound(self) -> bool:
        """No expectation was broken and nothing substantial went unobserved. A report that fails
        this is telling the teacher about their plan, not about their student."""
        return self.count(DEFECTIVE) == 0 and not self.unexplained

    def headline(self) -> str:
        c = self.counts
        bits = [f"{c[MET]} met", f"{c[UNMET]} unmet"]
        for v, label in ((BLOCKED, "blocked"), (UNOBSERVABLE, "unobservable"),
                         (DEFECTIVE, "defective")):
            if c[v]:
                bits.append(f"{c[v]} {label}")
        if self.unexplained:
            bits.append(f"{len(self.unexplained)} unexplained")
        if not self.within_deadline:
            bits.append("work continued past the deadline")
        return " · ".join(bits)

    def render(self) -> str:
        """Plain text a teacher can skim. Deliberately model-free — the literal truth an LLM's
        friendlier rendering must be checkable against."""
        lines = [self.headline(), ""]
        for f in self.findings:
            mark = {MET: "OK  ", UNMET: "MISS", BLOCKED: "----", UNOBSERVABLE: "??  ",
                    DEFECTIVE: "!!  "}.get(f.verdict, "?   ")
            lines.append(f"{mark} [{f.layer:6}] {f.say}")
            if f.blocked_by:
                lines.append(f"          after: {', '.join(f.blocked_by)}")
            if f.detail:
                lines.append(f"          {f.detail}")
        if self.unexplained:
            lines += ["", "Work this plan does not observe (teacher review):"]
            lines += [f"  - {n} x {t}" for t, n in self.unexplained]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
def _evaluate_one(exp, world, runner, observability):
    """(verdict, detail) for a single expectation, ignoring dependencies."""
    if observability is not None:
        try:
            if not observability.possible(exp):
                return UNOBSERVABLE, "nothing in this construction could satisfy it"
        except Exception:                       # noqa: BLE001 — an oracle fault is not a verdict
            pass

    obj = _obj.Objective(id=exp.id, say=exp.say,
                         kind="behavioral" if exp.is_behavioural else "structural",
                         check=exp.check, probe=exp.probe)
    result = _obj.evaluate(obj, world, runner)

    if result.status == _obj.DEFECTIVE:
        # The validator should have caught this at authoring time. Reaching here means a plan got
        # past the gate, so say so loudly rather than letting it read as the student's failure.
        return DEFECTIVE, "this expectation does not parse — a plan defect, not the student's work"
    if result.status == _obj.PENDING:
        return UNOBSERVABLE, "the lab was not running when this was evaluated"
    met = (result.status == _obj.MET)
    # A negative expectation asserts the opposite: satisfying the probe means the traffic got
    # through, which is the failure. Inverting here keeps every pattern's probe written in the
    # positive, readable form.
    if exp.sense == "negative":
        met = not met
    return (MET if met else UNMET), ""


def unexplained_work(plan, topology) -> tuple:
    """Element types the student built that no expectation in the plan mentions.

    The fairness backstop. A hidden plan applied to free-form work will sometimes meet a correct
    solution it was not written for, and the student cannot know why their report is thin — so the
    gap is surfaced as a finding *about the plan* instead of silence.

    Deliberately coarse: it reports "2 firewalls, and nothing here observes firewalls" rather than
    trying to judge whether the firewalls were any good. Judging is the teacher's job; this only
    guarantees they are told there is something to judge.
    """
    from .aop import check_tokens, probe_tokens

    mentioned: set[str] = set()
    for e in plan.expectations:
        for token, _fn in (probe_tokens(e.probe) + check_tokens(e.check)):
            mentioned.add(str(token).partition("@")[0])

    counts: dict = {}
    for d in getattr(topology, "devices", {}).values():
        t = getattr(d, "type_key", "")
        if t and t not in mentioned and t not in COSMETIC_TYPES:
            counts[t] = counts.get(t, 0) + 1
    return tuple(sorted(counts.items()))


def build(plan, world, runner=None, *, topology=None, observability=None,
          within_deadline: bool = True) -> Report:
    """Evaluate a plan into a report.

    Expectations are walked in `evaluation_order` — dependencies first, lowest layer first — which
    is what makes `blocked` possible: by the time an expectation is reached, everything it rests on
    already has a verdict. That ordering is also why a broken foundation yields one finding and a
    list of consequences rather than a wall of unrelated failures.
    """
    from .aop import plan_hash

    verdicts: dict = {}
    findings: list[Finding] = []
    for exp in evaluation_order(plan):
        unmet_deps = tuple(d for d in exp.requires if verdicts.get(d) not in _SATISFIED)
        if unmet_deps:
            verdict, detail = BLOCKED, ""
        else:
            verdict, detail = _evaluate_one(exp, world, runner, observability)
        verdicts[exp.id] = verdict
        findings.append(Finding(id=exp.id, say=exp.say, layer=exp.layer, verdict=verdict,
                                detail=detail, blocked_by=unmet_deps, pattern=exp.pattern))

    return Report(plan_hash=plan_hash(plan), findings=tuple(findings),
                  unexplained=unexplained_work(plan, topology) if topology is not None else (),
                  within_deadline=within_deadline)

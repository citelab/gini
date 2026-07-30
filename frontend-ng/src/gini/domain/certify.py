"""Fragment certification — the client-side quality gate before a block goes to the Teaching Center.

Certification has two organs, matching the generate⇄verify spine:

  * **the compiler (deterministic, HARD gate)** — proves ground truth: the fragment validates, and
    (once a live runner is wired) that a reference solution grades to complete. The AI never decides
    correctness — a hallucinated "looks fine" on a broken block would poison every mission composed
    from it.
  * **the AI certifier (advisory, SOFT gate)** — reviews *composability*: the issues the composer
    will hit when it recombines this block. Its strongest form is a **dry-run of the composition
    engine** — actually attempt to compose the fragment against the library and report what fit,
    what's orphaned, and what it can feed. The reasoning here is deterministic today (contract
    matching via `assembly`); an LLM advisor can layer richer judgement on top later.

`certify()` returns a report of tiered issues. `blocked` (any BLOCK issue) means "not certified".
WARN issues are soft — the teacher may upload over them, but they travel with the fragment so the
composer knows the risk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import capabilities as _caps
from . import fragment_yaml as _fy
from . import fragments as _frag

BLOCK, WARN, INFO = "block", "warn", "info"


@dataclass
class Issue:
    level: str          # BLOCK | WARN | INFO
    code: str
    message: str


@dataclass
class CertReport:
    fragment_id: str
    issues: list[Issue] = field(default_factory=list)
    composes_into: list[str] = field(default_factory=list)   # fragments this one can feed (forward)
    unmet_requires: list[str] = field(default_factory=list)   # requires nothing in the library gives

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(Issue(level, code, message))

    @property
    def blocked(self) -> bool:
        return any(i.level == BLOCK for i in self.issues)

    @property
    def certified(self) -> bool:
        return not self.blocked

    def of(self, level: str) -> list[Issue]:
        return [i for i in self.issues if i.level == level]


@dataclass
class RuntimeGrade:
    """The outcome of grading the fragment's AUTHORING BOARD (the reference solution) on a live
    stack. The caller runs `objectives.evaluate_all` with the fragment's sources auto-started, then
    hands the summary here. `available=False` means the stack wasn't running, so nothing was proven.
    """
    available: bool = False
    unmet: list = field(default_factory=list)     # objective ids that graded UNMET (a real failure)
    pending: list = field(default_factory=list)   # behavioral ids the runner couldn't witness

    @property
    def all_met(self) -> bool:
        return self.available and not self.unmet and not self.pending


_COUNT_RE = re.compile(r"^\s*count\(\s*(\w+)\s*\)\s*>=\s*(\d+)")
_PROBE_ENDS_RE = re.compile(r"(?:reach|ping|http)\(\s*(\w+)\s*->\s*(\w+)")
_MEASURE_RE = re.compile(r"measure\(\s*(\w+)\s*,")


def _multi_types(objectives) -> set[str]:
    """Types the fragment asks for MORE THAN ONE of — the ones a type-based probe can be ambiguous
    about in a merged board."""
    out = set()
    for o in objectives:
        m = _COUNT_RE.match(o.get("check") or "")
        if m and int(m.group(2)) >= 2:
            out.add(m.group(1))
    return out


def _probe_types(objectives) -> set[str]:
    out = set()
    for o in objectives:
        p = o.get("probe") or ""
        m = _PROBE_ENDS_RE.search(p)
        if m:
            out.update((m.group(1), m.group(2)))
        m = _MEASURE_RE.search(p)
        if m:
            out.add(m.group(1))
    return out


def _dry_run_compose(frag, rep: CertReport, library) -> None:
    """A deterministic dry-run of the composition engine: can the fragment's requirements be met by
    the library (backward), and what can consume what it provides (forward)? This is exactly the
    contract-matching the assembler does when it chains fragments."""
    lib = library if library is not None else _frag.all_fragments()
    others = [f for f in lib if f.id != frag.id]

    for req in frag.requires:                        # backward: who can satisfy what I need?
        if any(_caps.any_satisfies(f.provides, req) for f in others):
            continue
        rep.unmet_requires.append(req)
        rep.add(WARN, "orphan-requires",
                f"Nothing in the library provides '{req}', so I can only ever use this block to "
                f"START a chain, never in the middle.")

    for f in others:                                 # forward: who can consume what I provide?
        if any(_caps.any_satisfies(frag.provides, r) for r in f.requires):
            rep.composes_into.append(f.id)
    if frag.provides and rep.composes_into:
        rep.add(INFO, "composes-into",
                f"Composes into: {', '.join(sorted(set(rep.composes_into)))}.")


def certify(frag_dict: dict, *, library=None, runtime: "RuntimeGrade | None" = None) -> CertReport:
    """Certify an authored fragment dict. `runtime` is the result of grading the fragment's authoring
    board on a live stack (None = not graded). A fragment with live/output checks is only certified
    once that grade is clean — the HARD compiler gate; pure-structural fragments need no runtime."""
    rep = CertReport(fragment_id=frag_dict.get("id", ""))

    # -- compiler: gradable (BLOCK) ---------------------------------------- #
    problems = _fy.validate(_fy.fragment_from_dict(frag_dict))
    for p in problems:
        rep.add(BLOCK, "invalid", p)
    if rep.blocked:
        return rep                                   # a broken block: don't reason further

    frag = _fy.fragment_from_dict(frag_dict)
    objectives = frag_dict.get("objectives", [])
    has_output = any((o.get("probe") or "").startswith("measure(") for o in objectives)
    has_live = any((o.get("probe") or "").startswith(("reach", "ping", "http")) for o in objectives)
    has_behavioral = has_output or has_live

    # -- compiler: winnable / runtime-sound (HARD gate) -------------------- #
    # The authoring board IS the reference solution: grade it live and it must pass. Behavioral /
    # output checks need a running stack (with the Sources driving traffic); pure-structural doesn't.
    if not has_behavioral:
        rep.add(INFO, "structural-only",
                "Structural fragment — no live checks, so no runtime playtest is needed.")
    elif runtime is None or not runtime.available:
        rep.add(BLOCK, "runtime-required",
                "Run the topology and Certify to prove the live/output checks — required before "
                "upload (its Sources drive traffic while the board is graded).")
    elif runtime.unmet:
        rep.add(BLOCK, "not-winnable",
                f"The reference board doesn't satisfy: {', '.join(runtime.unmet)} — is it winnable?")
    elif runtime.pending:
        rep.add(BLOCK, "runtime-incomplete",
                f"Couldn't witness: {', '.join(runtime.pending)} — is the whole stack running?")
    else:
        rep.add(INFO, "runtime-ok",
                "Playtested live: the reference solution grades to complete.")

    # -- AI certifier: composability review (WARN, soft gate) -------------- #
    if not (frag_dict.get("spirit") or "").strip():
        rep.add(WARN, "no-spirit",
                "No spirit/intent — the composer can't generate variants or negotiate level without "
                "a sentence on what success means.")
    if not has_output and not has_live:
        rep.add(WARN, "no-output",
                "No live/output check — this grades only the picture, not that the system works. "
                "Attach a Sink and add an output check.")
    if not frag.forks:
        rep.add(WARN, "no-difficulty",
                "No difficulty knob — the composer can't offer an easier/harder variant. Add input "
                "presets or a fork.")
    multi, probed = _multi_types(objectives), _probe_types(objectives)
    for tk in sorted(multi & probed):
        # not actually ambiguous — type-based grading reads this as "SOME <tk>" by default (add
        # ', all' if you mean EVERY one). Just a hint so the author picks the intended quantifier.
        rep.add(INFO, "quantifier",
                f"You have ≥2 '{tk}' and a check on '{tk}' — it grades as \"some {tk}\"; add ', all' "
                f"to the probe if you mean every one.")
    if len(objectives) > 8:
        rep.add(WARN, "too-big",
                f"{len(objectives)} objectives — big blocks recombine poorly; consider splitting "
                f"into single-idea fragments.")

    # -- AI certifier: dry-run the composition engine (WARN/INFO) ---------- #
    _dry_run_compose(frag, rep, library)
    return rep


def runtime_from_results(results, *, available: bool = True) -> RuntimeGrade:
    """Summarise an `objectives.evaluate_all` result list into a RuntimeGrade for `certify()`."""
    from . import objectives as _obj
    unmet = [r.id for r in results if r.status == _obj.UNMET]
    pending = [r.id for r in results if r.status == _obj.PENDING]
    return RuntimeGrade(available=available, unmet=unmet, pending=pending)

"""A case: the unit of work at the Health Center, and the rules it will not break.

Pure. The records are frozen dataclasses and every transition is a function that either returns
the next record or raises :class:`CaseError` with a sentence a person can read. Persistence and
HTTP wrap this the way ``services/probe_runner.py`` wraps ``domain.probes``, so the rules can be
tested without a server, and a server cannot hold a case the rules forbid.

Each rule below is here because a decision was made, not because it was tidy:

* **The code is the consent** (design 17). Opening a case vends one; a machine joins by typing it,
  and joining is what sends anything at all. Doctors poll outbound and are never addressed by each
  other — a task names a participant that has already appeared, so nothing can be pushed at a
  machine the case has never met.
* **A model failure stalls a case; it never loses data** (design 16). ``stall`` is a state, so
  everything attached stays readable and ``resume`` is always available. Nothing here imports a
  model: the agent is a client of this, and a case that is waiting on one is simply a case in a
  state a person can take over from.
* **Diagnose and recommend only** (design 18). A diagnosis carries remedies as text. There is no
  field for "applied" and no function that could set one.
* **No verdict without evidence** (STATUS §3.4 — "FAILED" with nothing behind it was the least
  useful line in the first real report). ``diagnose`` refuses empty evidence.
* **Consent is declared, never assumed** (design 7). A task asking for a group that starts a
  container must carry the sentence the doctor will show first. Which groups those are is asked of
  the doctor's own registry, so a third one added there cannot be posted from here as read-only.
* **A single report is nearly worthless; the comparison is the product** (STATUS §3.2), so
  ``differences`` is part of the model rather than a report on the side.

Time and randomness are injected (``now=``, ``rand=``) so a test can drive a whole case without
sleeping and without patching the clock for everyone.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple

from gini_doctor.stage1 import casecode
from gini_doctor.stage1.compare import Comparison, compare
from gini_doctor.stage1.report import Report

# -- states ----------------------------------------------------------------- #
OPEN, STALLED, CLOSED = "open", "stalled", "closed"
CASE_STATES = (OPEN, STALLED, CLOSED)

POSTED, TAKEN, ANSWERED, DECLINED, EXPIRED = "posted", "taken", "answered", "declined", "expired"
TASK_STATES = (POSTED, TAKEN, ANSWERED, DECLINED, EXPIRED)

# A task ends in exactly one of three ways, and DECLINED is one of them on purpose: a person
# refusing to start a container is an ANSWER to the question the task asked, not silence. Reading
# a refusal as a timeout would make the case look unfinished and invite the agent to ask again.
_TASK_NEXT = {
    POSTED: (TAKEN, EXPIRED),
    TAKEN: (ANSWERED, DECLINED, EXPIRED),
    ANSWERED: (),
    DECLINED: (),
    EXPIRED: (),
}


class CaseError(ValueError):
    """Something the case cannot accept, carrying the reason to show whoever asked."""


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _ident(rand=None, size: int = 8) -> str:
    return (rand or secrets.token_bytes)(size).hex()


# -- records ---------------------------------------------------------------- #
@dataclass(frozen=True)
class Case:
    id: str
    code: str                       # normalised 12 symbols; `casecode.pretty` for display
    subject: str                    # what is wrong, in the opener's own words
    opened_by: str
    opened_at: float
    state: str = OPEN
    stalled_why: str = ""
    closed_at: float = 0.0
    closed_why: str = ""

    @property
    def pretty_code(self) -> str:
        return casecode.pretty(self.code)


@dataclass(frozen=True)
class Participant:
    """A machine that joined a case by typing its code.

    ``token`` is the secret it polls with afterwards; ``id`` is what the portal shows. Identity is
    the token and not the hostname: a lab's hostnames are distinct today, but a container or a
    reimaged laptop can repeat one, and two machines sharing a row would silently merge two
    diagnoses into one.
    """
    id: str
    case: str
    host: str
    platform: str
    joined_at: float
    token: str = ""


@dataclass(frozen=True)
class CaseReport:
    """A report attached to a case. Immutable: a report is what a machine looked like at a moment,
    and a corrected one is a new report, not an edit of the old."""
    id: str
    case: str
    participant: str
    report: Report
    received_at: float
    answers: str = ""               # the task id this report answers, or "" if volunteered

    @property
    def host(self) -> str:
        return self.report.host

    @property
    def summary(self) -> str:
        return "%s (%s, %d facts, %s)" % (self.report.host, self.report.platform,
                                          len(self.report.facts), self.report.collected_at)


@dataclass(frozen=True)
class Task:
    """A probe the Health Center asks one participant to run.

    v1 asks only for the doctor's OWN groups. Nothing here can name a command, which is what keeps
    "the Health Center is trusted" (design 15) from having to mean "the Health Center may run
    anything": the worst a trusted server can ask for is a group the doctor already ships, tests
    and declares consent for. Probe packs, signed or otherwise, are deferred.
    """
    id: str
    case: str
    participant: str
    groups: Tuple[str, ...]
    reason: str                     # why this was asked, shown to the person
    consent: str = ""               # what it will run, shown BEFORE anything runs; "" = read-only
    posted_by: str = ""
    posted_at: float = 0.0
    state: str = POSTED
    ended_at: float = 0.0
    outcome: str = ""               # the report id that answered, or the reason it was refused


@dataclass(frozen=True)
class Diagnosis:
    case: str
    summary: str
    evidence: Tuple[str, ...]       # fact keys, report ids — never empty
    remedies: Tuple[str, ...] = ()  # proposals, as text. Shown, never run.
    by: str = ""                    # "agent", or a staff username
    at: float = 0.0


# -- the case --------------------------------------------------------------- #
def open_case(subject: str, opened_by: str, *, now: Optional[float] = None, rand=None) -> Case:
    """Open a case and vend its code in the same breath, because the code IS the consent: there is
    no moment at which a case exists but cannot be joined, and no way to have one without it."""
    if not (subject or "").strip():
        raise CaseError("Say in one line what is wrong. A case with no subject is one nobody "
                        "can pick up later.")
    if not (opened_by or "").strip():
        raise CaseError("A case is opened by someone; record who.")
    rnd = rand or secrets.token_bytes
    return Case(id=_ident(rnd), code=casecode.mint(rnd), subject=subject.strip(),
                opened_by=opened_by.strip(), opened_at=_now(now))


def _accepting(case: Case, what: str) -> None:
    """A CLOSED case accepts nothing further; a STALLED one accepts everything.

    Stalled means the agent stopped, not the machines: a doctor that is mid-poll should still be
    able to hand in what it gathered, and refusing it would throw away the one thing the design
    promises a stall keeps.
    """
    if case.state == CLOSED:
        raise CaseError("Case %s was closed on %s — %s. Open a new case."
                        % (case.id, time.strftime("%Y-%m-%d", time.gmtime(case.closed_at)), what))


def join(case: Case, host: str, platform: str, *, now: Optional[float] = None,
         rand=None) -> Participant:
    _accepting(case, "no more machines can join it")
    if not (host or "").strip():
        raise CaseError("A joining machine reports its hostname; a report that cannot be "
                        "identified cannot be compared with another.")
    rnd = rand or secrets.token_bytes
    return Participant(id=_ident(rnd), case=case.id, host=host.strip(), platform=platform,
                       joined_at=_now(now), token=_ident(rnd, 16))


def stall(case: Case, why: str, *, now: Optional[float] = None) -> Case:
    """Park a case that cannot go on by itself. The data stays and a person continues from the
    portal."""
    if case.state == CLOSED:
        raise CaseError("A closed case cannot stall.")
    return replace(case, state=STALLED, stalled_why=(why or "").strip())


def resume(case: Case) -> Case:
    if case.state == CLOSED:
        raise CaseError("A closed case cannot be resumed. Open a new one and reference this.")
    return replace(case, state=OPEN, stalled_why="")


def close(case: Case, why: str, *, now: Optional[float] = None) -> Case:
    if case.state == CLOSED:
        return case                 # closing twice is not an error, it is the same outcome
    return replace(case, state=CLOSED, closed_at=_now(now), closed_why=(why or "").strip())


# -- reports ---------------------------------------------------------------- #
def attach(case: Case, participant: Participant, report: Report, *, task: Optional[Task] = None,
           now: Optional[float] = None, rand=None) -> CaseReport:
    _accepting(case, "it takes no more reports")
    if participant.case != case.id:
        raise CaseError("That machine joined a different case.")
    if task is not None and task.case != case.id:
        raise CaseError("That task belongs to a different case.")
    return CaseReport(id=_ident(rand or secrets.token_bytes), case=case.id,
                      participant=participant.id, report=report, received_at=_now(now),
                      answers=task.id if task is not None else "")


def differences(reports: Sequence[CaseReport], *, include_noisy: bool = False) -> Comparison:
    """The product: only the facts on which this case's machines disagree.

    Delegated to the doctor's own ``compare``, so the Health Center cannot develop a second opinion
    about what counts as a difference — including its rule that a fact which is ``n/a`` on one
    platform is a platform difference and not a fault.
    """
    if len(reports) < 2:
        raise CaseError("A comparison needs two machines. This case has %d, so there is nothing "
                        "to compare it against yet — a single report says much less than a pair."
                        % len(reports))
    return compare([r.report for r in reports], include_noisy=include_noisy)


# -- tasks ------------------------------------------------------------------ #
def groups_needing_consent(groups: Sequence[str]) -> Tuple[str, ...]:
    """Which of these groups do more than look.

    Asked of the doctor's registry rather than listed here. ``live`` and ``xv6`` are the two today;
    the point is the third one, added to the doctor by someone who has never read this file, which
    would otherwise be postable from here as though it only looked.
    """
    from gini_doctor.stage1 import probes
    return tuple(g for g in groups if probes.consent_text(g))


def known_groups() -> Tuple[str, ...]:
    from gini_doctor.stage1 import probes
    return tuple(sorted(probes.groups()))


def post_task(case: Case, participant: Participant, groups: Sequence[str], reason: str, *,
              consent: str = "", posted_by: str = "", now: Optional[float] = None,
              rand=None) -> Task:
    _accepting(case, "no more tasks can be posted to it")
    if participant.case != case.id:
        raise CaseError("That machine joined a different case.")
    wanted = tuple(g for g in (groups or ()) if g)
    if not wanted:
        raise CaseError("A task names at least one probe group to run.")
    unknown = [g for g in wanted if g not in known_groups()]
    if unknown:
        raise CaseError("The doctor has no group called %s. It has: %s."
                        % (", ".join(unknown), ", ".join(known_groups())))
    if not (reason or "").strip():
        raise CaseError("Say why this is being asked for. The doctor shows the reason to whoever "
                        "is at the machine, and a task with no reason reads as a machine being "
                        "poked at by something they cannot question.")
    needs = groups_needing_consent(wanted)
    if needs and not (consent or "").strip():
        raise CaseError("%s start a container, so the task has to carry what it will run and why "
                        "— that text is what the person answers Y to. Without it the doctor has "
                        "nothing to show and will skip the group."
                        % ", ".join(needs))
    return Task(id=_ident(rand or secrets.token_bytes), case=case.id, participant=participant.id,
                groups=wanted, reason=reason.strip(), consent=(consent or "").strip(),
                posted_by=posted_by, posted_at=_now(now))


def _move(task: Task, to: str, *, now: Optional[float] = None, outcome: str = "") -> Task:
    if to not in _TASK_NEXT[task.state]:
        raise CaseError("A task that is %s cannot become %s." % (task.state, to))
    ended = 0.0 if to == TAKEN else _now(now)
    return replace(task, state=to, ended_at=ended, outcome=outcome)


def take(task: Task, *, now: Optional[float] = None) -> Task:
    """A doctor polled and picked this up. Recorded, so a second poll does not run it twice and
    the portal can say "this machine has it" rather than "no answer yet"."""
    return _move(task, TAKEN, now=now)


def answer(task: Task, report_id: str, *, now: Optional[float] = None) -> Task:
    if not (report_id or "").strip():
        raise CaseError("An answered task names the report that answered it.")
    return _move(task, ANSWERED, now=now, outcome=report_id.strip())


def decline(task: Task, why: str = "", *, now: Optional[float] = None) -> Task:
    """The person said no, or there was no terminal to ask on. A complete answer either way."""
    return _move(task, DECLINED, now=now,
                 outcome=(why or "").strip() or "declined at the machine")


def expire(task: Task, *, now: Optional[float] = None) -> Task:
    """Nobody came for it. Distinct from declined: the machine never answered, so the question is
    still open and asking again is reasonable."""
    return _move(task, EXPIRED, now=now, outcome="no doctor collected this")


# -- diagnosis -------------------------------------------------------------- #
def diagnose(case: Case, summary: str, evidence: Sequence[str], remedies: Sequence[str] = (), *,
             by: str = "", now: Optional[float] = None) -> Diagnosis:
    """Close the loop: what this case turned out to be, with what it is based on.

    Evidence is not optional. The legacy doctor's worst line was "FAILED" with the registry's own
    error thrown away, and a diagnosis with nothing behind it is the same mistake one level up —
    it cannot be checked, argued with, or re-run against a machine that has since changed.
    """
    _accepting(case, "its diagnosis is already recorded")
    if not (summary or "").strip():
        raise CaseError("A diagnosis says what this was, in a line.")
    cited = tuple(e.strip() for e in (evidence or ()) if (e or "").strip())
    if not cited:
        raise CaseError("Cite what this rests on — the facts that differ, or the reports they came "
                        "from. A diagnosis nobody can check is not one.")
    return Diagnosis(case=case.id, summary=summary.strip(), evidence=cited,
                     remedies=tuple(r.strip() for r in (remedies or ()) if (r or "").strip()),
                     by=by, at=_now(now))

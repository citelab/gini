"""Activity codes: vending them, judging them, and accepting what comes back.

A student takes a code from a public page, works free-form in gBuilder against a frozen Activity
Observation Plan, and their proof is submitted automatically. This module holds the rules that make
that safe. The rules live here rather than in `store.py` or `server.py` so they can be tested with
no database, no HTTP and no model.

Three ideas carry the design.

**A code is a scope, not an identity.** Nothing about a student is recorded — not here, not in the
tables. Anti-sharing does not come from making codes scarce; a code alone is worthless, because a
proof is a *chain* recorded under it. Handing a friend your code gives them nothing.

**Vending closes, and codes expire.** The vending deadline is what prevents late submissions. On its
own it would not: a student could take ten codes on day one and use one the following week, so
gating issuance alone does not gate use. Every code therefore carries an absolute `valid_until` of
`vend_until + session_minutes`. Hoarding buys nothing, and a code taken one minute before close
still gets its full session.

**Two collision checks, catching two different cheats.** A receipt collision means the same proof
*file* was handed in twice. An artifact collision means the same *topology* was built under two
codes — the collusion case a receipt cannot see, because the chain binds the ticket and the
timestamps, so identical work under different codes produces different receipts (pinned by
`frontend-ng/tests/test_receipt_distinctness.py`).
"""
from __future__ import annotations

import json
import time

from gini.domain import proof as _proof
from gini.domain import ticket as _ticket

#: Why a code was refused. Each maps to a sentence a student can act on — none of them leak whether
#: some *other* code would have worked, which would turn the endpoint into an oracle.
NO_ACTIVITY = "no_activity"
NOT_RELEASED = "not_released"
VENDING_CLOSED = "vending_closed"
UNKNOWN_CODE = "unknown_code"
EXPIRED = "expired"
ALREADY_USED = "already_used"
PLAN_MOVED = "plan_moved"

_MESSAGES = {
    NO_ACTIVITY: "There is no activity here. Check the link your instructor gave you.",
    NOT_RELEASED: "This activity has not been released yet.",
    VENDING_CLOSED: "Codes for this activity are no longer being issued.",
    UNKNOWN_CODE: "That code was not issued by this course.",
    EXPIRED: "That code has expired. Take a new one if the activity is still open.",
    ALREADY_USED: "Work has already been submitted under that code. Take a new one.",
    PLAN_MOVED: "This activity changed after that code was issued. Take a new one.",
}


def message(reason: str) -> str:
    return _MESSAGES.get(reason, "That code cannot be used.")


def activity_id(course: str, lab: str) -> str:
    return f"{course}/{lab}"


# --------------------------------------------------------------------------- #
# vending
# --------------------------------------------------------------------------- #
def vending_open(activity: dict, now: float | None = None) -> tuple[bool, str]:
    """Whether a fresh code may be issued for this activity right now."""
    if not activity:
        return False, NO_ACTIVITY
    if activity.get("status") != "released":
        return False, NOT_RELEASED
    vend_until = float(activity.get("vend_until") or 0)
    if vend_until and (now or time.time()) >= vend_until:
        return False, VENDING_CLOSED
    return True, ""


def valid_until_for(activity: dict) -> float:
    """When a code issued now stops working.

    `vend_until + session_minutes`, NOT "now + session_minutes". Anchoring to the vending deadline
    is what makes hoarding pointless: a code taken on day one and a code taken at the last minute
    both die at the same moment, so taking a pile in advance gains nothing. The session clock is
    separate and starts when the student arms (§5.1).
    """
    vend_until = float(activity.get("vend_until") or 0)
    if not vend_until:
        return 0.0                                  # no vending deadline ⇒ no absolute expiry
    return vend_until + float(activity.get("session_minutes") or 60) * 60.0


def mint_code(activity: dict, now: float | None = None) -> dict:
    """A fresh code bound to this activity and the plan it was released with.

    Minted by `gini.domain.ticket.mint` and never by hand: the 12th symbol is a hash-derived check
    digit, and gBuilder's `ticket.parse` refuses anything else with "That code has a typo in it" —
    so a locally-invented code would be issued happily and then rejected at arming, with the student
    blamed for a typo they did not make.
    """
    return {"code": _ticket.mint().code,
            "activity": activity["id"],
            "plan_hash": activity.get("plan_hash", ""),
            "issued": now if now is not None else time.time(),
            "valid_until": valid_until_for(activity),
            "used": 0}


# --------------------------------------------------------------------------- #
# redeeming
# --------------------------------------------------------------------------- #
def check_code(code_row: dict | None, activity: dict | None,
               now: float | None = None) -> tuple[bool, str]:
    """Whether a code may be armed against right now.

    Checked at ARM time, before the student does any work, so an expired or spent code costs them a
    moment rather than an evening. The same function guards submission, because a code can expire
    between arming and submitting.
    """
    t = now if now is not None else time.time()
    if not code_row:
        return False, UNKNOWN_CODE
    if not activity:
        return False, NO_ACTIVITY
    if int(code_row.get("used") or 0):
        return False, ALREADY_USED
    valid_until = float(code_row.get("valid_until") or 0)
    if valid_until and t >= valid_until:
        return False, EXPIRED
    # A code names the instrument it was minted against. If the teacher re-released a changed plan,
    # this code belongs to a plan that no longer exists — and a student must never be measured by an
    # instrument other than the one their code named.
    if code_row.get("plan_hash") and code_row["plan_hash"] != activity.get("plan_hash"):
        return False, PLAN_MOVED
    return True, ""


def normalize(code: str) -> str:
    """A typed code reduced to what the tables key on. Delegates to the ticket vocabulary so a
    student may type it with or without hyphens, in any case, with O for 0."""
    return _ticket.normalize(code)


# --------------------------------------------------------------------------- #
# submission
# --------------------------------------------------------------------------- #
class Rejected(Exception):
    """A submission that cannot be accepted, carrying a reason fit to show."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason, self.detail = reason, detail
        super().__init__(detail or message(reason))


BAD_PROOF = "bad_proof"
WRONG_PLAN = "wrong_plan"
DUPLICATE = "duplicate"

_MESSAGES.update({
    BAD_PROOF: "That proof does not verify. It may have been edited after it was produced.",
    WRONG_PLAN: "That proof was recorded against a different version of this activity.",
    DUPLICATE: "A submission already exists for that code or that proof.",
})


def session_seconds(proof: dict) -> tuple[float, float]:
    """(started, finished) from the CHAIN, never from arrival time.

    A student who finished inside the window but synced the next morning is not late. Both
    timestamps live inside the signed chain, so the window is judged on what was recorded rather
    than on when the network happened to come back.
    """
    entries = proof.get("entries") or []
    if not entries:
        return 0.0, 0.0
    return float(entries[0].get("t") or 0), float(entries[-1].get("t") or 0)


def genesis_plan_hash(proof: dict) -> str:
    """The plan a chain was recorded against.

    Read from the genesis entry directly: `proof.verify_proof` cross-checks the ticket and the
    assignment but not this, so relying on verification alone would let a proof recorded against an
    older plan pass silently.
    """
    entries = proof.get("entries") or []
    if not entries:
        return ""
    return str((entries[0].get("data") or {}).get("plan_hash") or "")


def artifact_hash(proof: dict) -> str:
    """The topology's fingerprint, from the chain's submit entry."""
    for entry in reversed(proof.get("entries") or []):
        data = entry.get("data") or {}
        art = data.get("artifact")
        if isinstance(art, dict) and art.get("sha256"):
            return str(art["sha256"])
    return ""


def prepare(payload: dict, code_row: dict, activity: dict,
            now: float | None = None) -> dict:
    """Validate a submission and shape the row to store. Raises `Rejected`.

    Integrity only. Nothing here judges the *work* — that is the report's job and ultimately the
    teacher's. What this refuses is a proof that has been tampered with, one recorded against a
    different plan, or one arriving under a code that cannot accept it.
    """
    ok, reason = check_code(code_row, activity, now=now)
    if not ok:
        raise Rejected(reason)

    proof = payload.get("proof")
    if not isinstance(proof, dict):
        raise Rejected(BAD_PROOF, "the submission carried no proof")

    # `expect_ticket` makes verification check the proof was recorded under THIS code, so a proof
    # cannot be replayed against a different one. Doing it inside verify_proof rather than
    # afterwards means the check is part of the integrity verdict, not a separate thing to forget.
    verdict = _proof.verify_proof(proof, expect_ticket=code_row["code"])
    if not verdict.ok:
        raise Rejected(BAD_PROOF, verdict.reason or "the chain does not verify")

    recorded_plan = genesis_plan_hash(proof)
    if recorded_plan and recorded_plan != activity.get("plan_hash"):
        raise Rejected(WRONG_PLAN)

    started, finished = session_seconds(proof)
    return {"code": code_row["code"],
            "receipt": _proof.receipt_code(proof),
            "activity": activity["id"],
            "plan_hash": activity.get("plan_hash", ""),
            "artifact_hash": artifact_hash(proof),
            "ts": now if now is not None else time.time(),
            "started": started, "finished": finished,
            "verdict": "pass",
            "data": json.dumps(payload, sort_keys=True)}


def within_session(row: dict, activity: dict) -> bool:
    """Was the work done inside the session window the code granted?

    Reported, never enforced here — a run that overran is a fact for the teacher to weigh, not
    grounds for the server to throw away a student's evening.
    """
    limit = float(activity.get("session_minutes") or 0) * 60.0
    if not limit or not row.get("started") or not row.get("finished"):
        return True
    return (float(row["finished"]) - float(row["started"])) <= limit

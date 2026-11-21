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

**Two ways to run a lab, and the duration picks which.** With `session_minutes > 0` the lab is a
TIMED ATTEMPT: the student gets that long, and the last code out still gets its full window past
the deadline. With `session_minutes = 0` the lab has a FIXED HAND-IN TIME: `valid_until` is the
vending deadline exactly, so everyone is due at the same moment however early they started. Today
Wednesday 20:00, vending stops Thursday 21:00, duration 0 — a student starting now has until
Thursday 21:00. Zero is a real value here and is never defaulted away; see `session_minutes_for`.

**A lab is pausable unless the teacher says one sitting.** gBuilder lets a student pause a
recording and resume it later — the chain carries a `stopped` mark and a `resumed` one with the
length of the gap — and the arm reply tells gBuilder whether this lab allows it (`pausable`). The
server never refuses a resumed chain either way: a one-sitting lab done in three is REPORTED, with
the sittings counted, and the teacher weighs it. The only thing the flag changes here is what the
session window is measured against — recording time for a pausable lab, wall-clock for one sitting.

**Two collision checks, catching two different cheats.** A receipt collision means the same proof
*file* was handed in twice. An artifact collision means the same *topology* was built under two
codes — the collusion case a receipt cannot see, because the chain binds the ticket and the
timestamps, so identical work under different codes produces different receipts (pinned by
`frontend-ng/tests/test_receipt_distinctness.py`). In v1 this pair IS the point: there is no
observation plan, so the report describes what happened and these two checks are what stop the same
work being handed in twice.
"""
from __future__ import annotations

import json
import time

from gini.domain import proof as _proof
from gini.domain import proof_events as _ev
from gini.domain import ticket as _ticket

#: Why a code was refused. Each maps to a sentence a student can act on — none of them leak whether
#: some *other* code would have worked, which would turn the endpoint into an oracle.
NO_ACTIVITY = "no_activity"
NOT_RELEASED = "not_released"
VENDING_CLOSED = "vending_closed"
UNKNOWN_CODE = "unknown_code"
EXPIRED = "expired"
ALREADY_USED = "already_used"
ALREADY_CLAIMED = "already_claimed"
NO_SUCH_RECEIPT = "no_such_receipt"
BAD_LINK = "bad_link"
#: The code is good, but this gBuilder is older than the lab's `min_gini` (or did not say). Its own
#: reason, so a current gBuilder can show a pop-up with the upgrade command instead of a line.
UPDATE_GINI = "update_gini"

_MESSAGES = {
    NO_ACTIVITY: "There is no activity here. Check the link your instructor gave you.",
    NOT_RELEASED: "This activity has not been released yet.",
    # Says nothing about whether the lab exists, is released, or is merely mistyped. All three
    # read the same, because the point of the code is that a student who has lab3's link cannot
    # learn anything by editing it to lab4 — including whether lab4 is there.
    BAD_LINK: "That link is not valid for this activity. Use the link your instructor gave you, "
              "in full — it ends with a four-character code.",
    VENDING_CLOSED: "Codes for this activity are no longer being issued.",
    UNKNOWN_CODE: "That code was not issued by this course.",
    EXPIRED: "That code has expired. Take a new one if the activity is still open.",
    ALREADY_USED: "Work has already been submitted under that code. Take a new one.",
    # Says only THAT it is claimed, never by whom: naming the first student to the second would
    # hand out one student's id on nothing more than a guessed receipt. The teacher sees both.
    ALREADY_CLAIMED: "That receipt has already been claimed. If you believe it is yours, speak to "
                     "your instructor — they can see every claim on it.",
    NO_SUCH_RECEIPT: "No work has been submitted under that receipt. Check it, and make sure "
                     "gBuilder finished submitting.",
}


def message(reason: str) -> str:
    return _MESSAGES.get(reason, "That code cannot be used.")


# --------------------------------------------------------------------------- #
# the oldest GINI a lab can be done with
# --------------------------------------------------------------------------- #
def min_gini_value(text) -> str | None:
    """A teacher's "minimum GINI version" field -> what to store: "" for none, "6.22.12" for a
    version, None if it cannot be read. Kept as typed, minus spaces and a leading v."""
    from gini.domain.versions import version_tuple
    t = str(text or "").strip().lstrip("vV")
    if not t:
        return ""
    if version_tuple(t) is None or not t.replace(".", "").isdigit() or t.count(".") > 2:
        return None
    return t


def gini_too_old(activity: dict | None, client_version: str) -> dict | None:
    """The refusal for a gBuilder older than the lab needs, or None if it may arm.

    `client_version` is what gBuilder sent with the code. Every gBuilder before this check sends
    nothing, and those are precisely the students the check exists for, so "no version" is refused
    like an old one. They cannot show a pop-up, but they already put a refusal's `error` in front
    of the student and do not arm, so the sentence is written to stand alone: what is needed, what
    they have, and the command that fixes it.
    """
    from gini.domain.versions import at_least
    need = str((activity or {}).get("min_gini") or "").strip()
    if not need or at_least(client_version, need):
        return None
    have = str(client_version or "").strip()
    yours = f"this gBuilder is {have}" if have else "this gBuilder is older than that"
    return {"ok": False, "reason": UPDATE_GINI, "min_gini": need, "your_gini": have,
            "error": f"This lab needs GINI {need} or newer, and {yours}. Update it with "
                     f"'pipx upgrade gini-toolkit' (or 'pip install --upgrade gini-toolkit'), "
                     f"reopen gBuilder, and enter the code again."}


def activity_id(course: str, lab: str) -> str:
    return f"{course}/{lab}"


# --------------------------------------------------------------------------- #
# the release code — what makes a student link unguessable
# --------------------------------------------------------------------------- #
#: How many symbols. Four is a deliberate speed bump, not a secret: 32**4 is about a million, which
#: stops a student editing `lab3` to `lab4` in the address bar and stops nothing else. It travels in
#: a URL, so it is in browser history, server logs and every forwarded email — treat it as "this
#: link is not public", never as authentication.
RELEASE_CODE_LEN = 4


def mint_release_code(rand=None) -> str:
    """Four symbols from the ticket alphabet — Crockford base32, no I/L/O/U.

    The same alphabet as an activity code on purpose: a teacher reads this over a bench and a
    student types it, so the two confusable pairs that cause every mistyped code (O/0, I/1) must be
    impossible here for the same reason they are impossible there.
    """
    import secrets
    rnd = rand or (lambda n: secrets.token_bytes(n))
    return "".join(_ticket.ALPHABET[b % len(_ticket.ALPHABET)]
                   for b in rnd(RELEASE_CODE_LEN))


def normalize_release_code(code: str) -> str:
    """What the student typed, reduced to what we compare. Same folding as an activity code."""
    return _ticket.normalize(code)[:RELEASE_CODE_LEN]


def release_code_ok(activity: dict, supplied: str) -> bool:
    """Does this link carry the right code? An activity without one accepts anything.

    The empty case is not a loophole left open — every activity is given a code when it is saved,
    and the ones that predate the feature are backfilled at startup. It exists so that a row which
    somehow has none still behaves, rather than becoming unreachable with no way to fix it.
    """
    want = normalize_release_code(str((activity or {}).get("release_code") or ""))
    return not want or normalize_release_code(supplied) == want


def is_draft_run(activity: dict) -> bool:
    """Is this a rehearsal on an unreleased lab? Only reachable WITH the release code.

    The whole point of handing a code to the TAs before release: they run the lab end to end, get
    real submissions out of it, and those submissions must be distinguishable from a student's or
    they will be marked.
    """
    return (activity or {}).get("status") != "released"


# --------------------------------------------------------------------------- #
# vending
# --------------------------------------------------------------------------- #
def vending_open(activity: dict, now: float | None = None, *,
                 release_code: str = "") -> tuple[bool, str]:
    """Whether a fresh code may be issued for this activity right now.

    The release code is checked FIRST and its refusal is indistinguishable from "no such activity",
    so the endpoint cannot be used to discover which labs exist or what state they are in.

    A correct code also opens a DRAFT. That is the feature, not a hole: a teacher hands the link to
    their TAs, the TAs run the lab for real before anyone else can reach it, and the vending
    deadline still applies to them. What it buys is that the rehearsal uses exactly the path a
    student will use — the same vend, the same arm, the same submit — rather than an approximation
    of it that can be right while the real one is broken.
    """
    if not activity:
        return False, NO_ACTIVITY
    if not release_code_ok(activity, release_code):
        return False, BAD_LINK
    if activity.get("status") != "released" and not (activity.get("release_code") or ""):
        # No code to have got here with, so this is the old refusal: an unreleased lab.
        return False, NOT_RELEASED
    vend_until = float(activity.get("vend_until") or 0)
    if vend_until and (now or time.time()) >= vend_until:
        return False, VENDING_CLOSED
    return True, ""


#: What "minutes per attempt" means when a lab never said. A stored row always has a value (the
#: schema defaults it), so this is for dicts built by hand — tests, and older callers.
DEFAULT_SESSION_MINUTES = 60


def session_minutes_for(activity: dict) -> float:
    """The per-attempt allowance, in minutes.

    ZERO IS A REAL VALUE and must not be defaulted away: it means "there is no per-attempt window
    — the lab is simply due at the vending deadline". Only an ABSENT value falls back.

    This existed as `float(activity.get("session_minutes") or 60)` in two places, and `or` cannot
    tell 0 from unset: a teacher who set the duration to 0 silently got an hour PAST their
    deadline, which is the opposite of what they asked for.
    """
    v = (activity or {}).get("session_minutes")
    if v is None or v == "":
        return float(DEFAULT_SESSION_MINUTES)
    return float(v)


def valid_until_for(activity: dict) -> float:
    """When a code issued now stops working.

    `vend_until + session_minutes`, NOT "now + session_minutes". Anchoring to the vending deadline
    is what makes hoarding pointless: a code taken on day one and a code taken at the last minute
    both die at the same moment, so taking a pile in advance gains nothing. The session clock is
    separate and starts when the student arms (§5.1).

    **Duration 0 means the deadline IS the due date.** With `session_minutes = 0` this returns
    `vend_until` exactly, so the lab is due when vending stops: today is Wednesday 20:00, vending
    stops Thursday 21:00, and a student who starts now has until Thursday 21:00 — not an hour
    after it. That is the natural way to run a lab with a fixed hand-in time rather than a timed
    attempt, and `within_session()` already reports no per-attempt limit for 0, so the two agree.
    """
    vend_until = float(activity.get("vend_until") or 0)
    if not vend_until:
        return 0.0                                  # no vending deadline ⇒ no absolute expiry
    return vend_until + session_minutes_for(activity) * 60.0


def mint_code(activity: dict, now: float | None = None) -> dict:
    """A fresh code bound to this activity and the plan it was released with.

    Minted by `gini.domain.ticket.mint` and never by hand: the 12th symbol is a hash-derived check
    digit, and gBuilder's `ticket.parse` refuses anything else with "That code has a typo in it" —
    so a locally-invented code would be issued happily and then rejected at arming, with the student
    blamed for a typo they did not make.

    The code CARRIES whether this lab asks questions. gBuilder arms offline when the course server
    cannot be reached, and this is the only way it can know it is missing something — otherwise a
    student works the whole lab, hands in, and nobody finds out until a marker sees two blanks
    caused by hotel wifi. Decided here from the same activity row the caller passes to
    `pick_questions` in the same request, so the flag and the questions cannot disagree.
    """
    return {"code": _ticket.mint(questions=int(activity.get("show_n") or 0) > 0).code,
            "activity": activity["id"],
            "issued": now if now is not None else time.time(),
            "valid_until": valid_until_for(activity),
            "used": 0}


# --------------------------------------------------------------------------- #
# redeeming
# --------------------------------------------------------------------------- #
def check_code(code_row: dict | None, activity: dict | None,
               now: float | None = None, *, staff: bool = False) -> tuple[bool, str]:
    """Whether a code may be armed against right now.

    Checked at ARM time, before the student does any work, so an expired or spent code costs them a
    moment rather than an evening. The same function guards submission, because a code can expire
    between arming and submitting.

    `staff=True` waives EXPIRY and nothing else. It is for a teacher accepting a late submission by
    hand: the student finished, the code lapsed before the upload landed, and the proof is now
    unacceptable for ever — the outbox keeps retrying something the server will refuse until the end
    of time, because `expired` is deliberately not in `SETTLED`. A teacher deciding to take it is
    the authorisation the clock would otherwise have provided.

    What it does NOT waive is who the work belongs to. An unknown code is still refused — that is
    not a late submission, it is somebody else's proof — and so is a code already spent, because a
    second submission under one code is a duplicate whoever asks.
    """
    t = now if now is not None else time.time()
    if not code_row:
        return False, UNKNOWN_CODE
    if not activity:
        return False, NO_ACTIVITY
    if int(code_row.get("used") or 0):
        return False, ALREADY_USED
    valid_until = float(code_row.get("valid_until") or 0)
    if valid_until and not staff and t >= valid_until + grace_seconds(activity):
        return False, EXPIRED
    return True, ""


def pausable(activity: dict | None) -> bool:
    """May a student pause this lab and come back to it? Yes unless the teacher ticked one
    sitting — and yes for a row that predates the column, which is every lab there was."""
    return not int((activity or {}).get("one_sitting") or 0)


def grace_seconds(activity: dict | None) -> float:
    """How long after the deadline a submission is still taken, tagged LATE.

    Zero by default, so an activity that says nothing behaves exactly as it always has.

    It does not move the deadline — the work is still recorded as late and the teacher still sees
    it. What it removes is the cliff: a student who finished at 23:58 and lost their wifi currently
    holds a proof the server will refuse for ever, and their only route back is a member of staff
    accepting it by hand. A few hours of grace turns the common case back into something that
    resolves itself, and leaves `POST /api/submissions/accept` for the ones that do not.
    """
    return max(0.0, float((activity or {}).get("grace_minutes") or 0) * 60.0)


def is_late(code_row: dict | None, now: float | None = None) -> bool:
    """Did this arrive after the deadline the code carried?"""
    t = now if now is not None else time.time()
    valid_until = float((code_row or {}).get("valid_until") or 0)
    return bool(valid_until and t >= valid_until)


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
WRONG_TOPOLOGY = "wrong_topology"
DUPLICATE = "duplicate"

_MESSAGES.update({
    BAD_PROOF: "That proof does not verify. It may have been edited after it was produced.",
    WRONG_TOPOLOGY: "The work sent does not match the proof recorded for it. Submit from the "
                    "gBuilder that recorded the work.",
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


def paused_seconds(proof: dict) -> float:
    """How long the recording was paused, from the chain's own `resumed` marks.

    Inside the signed chain like `session_seconds`, so it cannot be claimed after the fact. The
    same sum `gini.domain.grader_view.paused_seconds` takes; computed here too so an older
    gini-core beside this server still measures the window correctly.
    """
    return sum(float((e.get("data") or {}).get("away") or 0.0)
               for e in (proof.get("entries") or []) if e.get("kind") == _ev.RESUMED)


def topology_matches(topology: dict, proof: dict) -> bool:
    """Does this topology hash to the fingerprint the chain committed to?

    THE anti-theft check, and the reason a stolen file is worthless. The chain is bound to the code
    (verified with `expect_ticket`), and the chain's submit entry carries `sha256` of the topology
    it was generated from. So a student who obtains a classmate's project file cannot submit it
    under their own code: their chain would commit to a different digest, and swapping the file in
    afterwards changes the digest and fails here.

    Hashed with the SAME function the recorder used, so the two can never disagree about
    canonicalisation — key order, whitespace, float formatting.
    """
    return bool(topology) and _proof.artifact_summary(topology).get("sha256") == artifact_hash(proof)


def built_sources(proof: dict) -> dict:
    """`{filename: sha256}` from the LAST successful build in the chain, per file.

    The last one because that is the kernel the student left running. Earlier builds are the story
    of getting there and the report narrates them; only the final one is the thing to check the
    submitted files against.
    """
    out: dict = {}
    for entry in proof.get("entries") or []:
        if (entry or {}).get("kind") != _ev.BUILD:
            continue
        data = entry.get("data") or {}
        if not data.get("ok"):
            continue
        for name, meta in (data.get("sources") or {}).items():
            sha = str((meta or {}).get("sha256") or "")
            if sha:
                out[str(name)] = sha
    return out


def check_sources(shadows: dict | None, proof: dict) -> list:
    """Pair each submitted source with what the chain says was compiled. REPORTED, never refused.

    A topology mismatch is fraud-shaped: it means the work handed in is not the work proved, so
    `prepare` refuses it. A source mismatch is not. It means "the file you sent is not the file you
    last compiled", which is what happens when a student tidies up after a build, or starts the
    next experiment before handing in — normal, innocent, and no grounds for throwing an evening
    away. So it is surfaced for the marker to weigh and nothing more.

    Matching on the FILENAME, not the full "<machine>/<file>" key, because a build entry knows
    which files it compiled and not which host directory they came from.
    """
    want = built_sources(proof)
    out = []
    for key, meta in sorted((shadows or {}).items()):
        meta = meta if isinstance(meta, dict) else {}
        name = str(key).rsplit("/", 1)[-1]
        got, expected = str(meta.get("sha256") or ""), want.get(name, "")
        out.append({
            "path": str(key),
            "lines": int(meta.get("lines") or 0),
            "bytes": int(meta.get("bytes") or 0),
            "text": meta.get("text", ""),
            "omitted": str(meta.get("omitted") or ""),
            # "" when the chain records no successful build of this file at all — which is itself
            # worth seeing: a source handed in that was never compiled is not a submission.
            "built_sha256": expected,
            "matches": bool(expected) and got == expected,
            "never_built": not expected,
        })
    return out


def recheck_tlabs(proof: dict) -> list:
    """Re-run every T-Lab claim in the chain against the engine and say whether it holds.
    REPORTED, never refused — the same rule as `check_sources`.

    A T-Lab's engine (`gini.domain.schedlab`) is pure and lives in gini-core, which this server
    depends on, so a `schedule` entry can be REPLAYED here — the same code, the same start, the
    same moves — and a `proved` entry's code can be explored again. A claim that does not
    re-check is shown the way an edited source file is shown: a tag beside the row, for the marker
    to weigh. It means the entry was hand-edited, or the code was changed after the proof, or the
    "fix" moved a tile the face pins — none of which the lab itself can produce.

    `confirmed` is True, False, or None when the check could not be run at all: a server whose
    gini-core predates the engine, or a level file this server does not ship. Saying "not
    checked" beats a false "not confirmed" on a student who did nothing wrong.
    """
    try:
        from gini.domain import schedlab as _sl
    except Exception:                                   # noqa: BLE001 — an older gini-core
        _sl = None
    out = []
    later = _later_tlab_kinds()
    for i, entry in enumerate(proof.get("entries") or []):
        kind = (entry or {}).get("kind", "")
        if kind in later:
            # T-Lab 03 and 04 (deadlock handling, the file system). Their engines are in
            # gini-core too, but this server does not replay their entries yet, so it says
            # "not checked" — never "does not re-check", which would accuse a student of an
            # edit nobody looked for.
            d = entry.get("data") or {}
            out.append({"kind": "proof" if kind == later[0] else "goal",
                        "lab": str(d.get("lab", "")), "level": str(d.get("level", "")),
                        "version": str(d.get("version", "") or d.get("start", "")),
                        "start": d.get("start") or 0, "goal": str(d.get("goal", "")),
                        "claimed": (("proved" if d.get("ok")
                                     else f"not proved: {d.get('reason', '')}")
                                    if kind == later[0] else str(d.get("goal", ""))),
                        "confirmed": None,
                        "why": "not checked: this server does not re-check this T-Lab yet",
                        "seq": entry.get("seq", i)})
            continue
        if kind not in (_ev.SCHEDULE, _ev.PROVED):
            continue
        d = entry.get("data") or {}
        row = {"kind": "schedule" if kind == _ev.SCHEDULE else "proof",
               "lab": str(d.get("lab", "")), "level": str(d.get("level", "")),
               "version": str(d.get("version", "")), "start": int(d.get("start") or 0),
               "goal": str(d.get("goal", "")),
               "claimed": (str(d.get("result", "")) if kind == _ev.SCHEDULE
                           else ("proved" if d.get("ok")
                                 else f"not proved: {d.get('reason', '')}")),
               "confirmed": None, "why": "", "seq": entry.get("seq", i)}
        out.append(row)
        if _sl is None:
            row["why"] = "this server's gini-core has no scheduler engine"
            continue
        try:
            row["confirmed"], row["why"] = _recheck_one(_sl, kind, d)
        except Exception as e:                          # noqa: BLE001 — a report must render
            row["confirmed"], row["why"] = None, f"could not re-check: {e}"
    return out


def _later_tlab_kinds() -> tuple:
    """T-Lab 03/04 entry kinds, `orders` first; empty on a gini-core that predates them."""
    names = ("ORDERS", "ALLOCATE", "BANK", "FS_WALK", "FS_READ", "FS_MAKE", "FS_TRACE")
    if not all(hasattr(_ev, n) for n in names):
        return ()
    return tuple(getattr(_ev, n) for n in names)


def _recheck_one(sl, kind: str, d: dict) -> tuple:
    lab = sl.get(str(d.get("lab", "")))
    if lab is None:
        return None, f"this server does not ship the level file {d.get('lab', '')!r}"
    lv = lab.level(str(d.get("level", "")))
    if lv is None:
        return None, f"the level file has no level {d.get('level', '')!r}"
    version = str(d.get("version", ""))
    code = tuple(tuple(str(t) for t in th) for th in (d.get("code") or []))
    if len(code) != len(lv.threads) or any(t not in lv.tiles for th in code for t in th):
        return False, "the code names a tile the level does not have"
    # The code must be one the face can show: a shipped version verbatim, or in Fix it a
    # rearrangement of the broken version's tiles that leaves every pinned tile where it was.
    if version == "fix":
        if lv.fix is None:
            return False, "the level has no Fix it"
        base = lv.code_of(lv.fix.from_version)
        for t, th in enumerate(code):
            if sorted(th) != sorted(base[t]):
                return False, "the fix is not a rearrangement of the level's lines"
            for pin in lv.fix.pinned:
                if pin in base[t] and base[t].index(pin) != th.index(pin):
                    return False, f"the fix moved {pin!r}, which the level pins"
    else:
        if lv.version(version) is None:
            return False, f"the level has no version {version!r}"
        if code != lv.code_of(version):
            return False, f"the code is not the level's {version!r} version"
    if kind == _ev.SCHEDULE:
        start = int(d.get("start") or 0)
        if lv.starts and not (0 <= start < len(lv.starts)):
            return False, "the start is not one the level has"
        moves = [int(m) for m in d.get("moves") or []]
        if any(m not in (0, 1) for m in moves):
            return False, "the schedule names a thread the level does not have"
        goal = str(d.get("goal", ""))
        if goal not in sl.goals_reached(lv, version, code, moves, start):
            return False, f"replaying the schedule does not reach the goal {goal!r}"
        rep = sl.replay(lv, code, moves, start)
        claimed = str(d.get("result", ""))
        if claimed == "deadlock" and not rep.deadlock:
            return False, "replaying the schedule does not deadlock"
        if claimed not in ("deadlock", "both_inside", "") and (
                not rep.done or sl.outcome_label(lv, rep.st) != claimed):
            return False, f"replaying the schedule does not end with {claimed!r}"
        return True, ""
    proof = sl.prove(lv, code)
    if proof.proved != bool(d.get("ok")):
        return False, ("exploring this code proves it, but the entry says it was not"
                       if proof.proved else
                       f"exploring this code does not prove it: {proof.reason}")
    return True, ""


def measurements(proof: dict) -> list[dict]:
    """Everything GINI MEASURED while the work was happening, as rows a marker can scan.

    The chain has carried these since the probes and riders shipped — `measure` from a Source/Sink
    rider, `witness` from a behavioural probe, `objective` from an objective changing state — and
    the report has never had a section for them. They reached a teacher only as a clause inside
    the narration ("…3 measurement(s)"), which is prose to read rather than a result to check, and
    is why a networking lab's numbers were effectively invisible at marking time.

    ONE list across every kind of lab, deliberately. A C-Lab's throughput reading, a T-Lab's
    reachability verdict and an OS lab's kernel metric are the same thing to a marker — something
    GINI observed and stands behind — and three sections would make a teacher learn which lab
    produces which. The `kind` column says where a row came from for anyone who cares.

    IN CHAIN ORDER, and repeats are kept. A probe that failed and then passed is the story of the
    attempt; collapsing it to its last verdict would hide the work and flatter the student, and
    collapsing it to its first would do the opposite.

    Nothing here is scored. `ok` is what GINI observed, not a mark: whether three of five probes
    passing is a pass is the teacher's call, exactly as `check_sources` reports a mismatched file
    and refuses to draw a conclusion from it.
    """
    out: list[dict] = []
    for i, entry in enumerate(proof.get("entries") or []):
        kind = (entry or {}).get("kind", "")
        d = entry.get("data") or {}
        if kind == _ev.MEASURE:
            out.append({
                # A reading that could not be TAKEN is not a failed one. An A-Lab metric needs
                # something to have moved between two readings, and an idle machine gives it
                # nothing to compare — reporting that as failed marks a student down for a
                # workload that did not run.
                "kind": "measurement", "name": str(d.get("name", "")),
                "ok": bool(d.get("ok")), "pending": bool(d.get("pending")),
                "detail": ", ".join(f"{k}={v}" for k, v in sorted(
                    (d.get("measurement") or {}).items())),
                "summary": str(d.get("summary", "")),
                "seq": entry.get("seq", i), "t": entry.get("t", 0.0)})
        elif kind == _ev.WITNESS:
            verdict = str(d.get("verdict", ""))
            out.append({
                "kind": "probe", "name": str(d.get("probe", "")),
                # "pending" is recorded by the client on purpose — they pressed Check with
                # nothing running — so it must not be reported here as a failure.
                "ok": verdict == "ok", "pending": verdict == "pending",
                "detail": verdict, "summary": "",
                "seq": entry.get("seq", i), "t": entry.get("t", 0.0)})
        elif kind == _ev.OBJECTIVE:
            out.append({
                # `ok` is None, not True. An objective moving from one status to another is a
                # CHANGE, not a verdict, and the statuses are the activity's own words — calling
                # that "passed" would be this section inventing a judgement, which is the one
                # thing v1 does not do. The transition is reported and the teacher reads it.
                "kind": "objective", "name": str(d.get("id", "")),
                "ok": None, "pending": False,
                "detail": f"{d.get('from', '')} -> {d.get('to', '')}",
                "summary": str(d.get("say", "")),
                "seq": entry.get("seq", i), "t": entry.get("t", 0.0)})
        elif kind == _ev.SCHEDULE:
            # A goal the student's own schedule reached — the deadlock, both inside, the lost
            # update. `ok` is True because the entry only exists when the goal was met; whether
            # it holds is `recheck_tlabs`'s question, shown beside this row.
            moves = d.get("moves") or []
            out.append({
                "kind": "schedule", "name": f"{d.get('level', '')} · {d.get('goal', '')}",
                "ok": True, "pending": False,
                "detail": f"{d.get('result', '')} in {len(moves)} step"
                          f"{'' if len(moves) == 1 else 's'}",
                "summary": "", "seq": entry.get("seq", i), "t": entry.get("t", 0.0)})
        elif kind in _later_tlab_kinds()[1:]:
            # A T-Lab 03/04 goal: like a schedule row, it exists only because the goal was met.
            steps = d.get("moves") or d.get("clicks") or d.get("steps") or d.get("writes") or []
            out.append({
                "kind": "schedule", "name": f"{d.get('level', '')} · {d.get('goal', '')}",
                "ok": True, "pending": False,
                "detail": f"{d.get('lab', '')}, {len(steps)} step"
                          f"{'' if len(steps) == 1 else 's'}",
                "summary": "", "seq": entry.get("seq", i), "t": entry.get("t", 0.0)})
        elif kind in (_ev.PROVED, *_later_tlab_kinds()[:1]):
            out.append({
                "kind": "proof", "name": f"{d.get('level', '')} · {d.get('version', '')}",
                "ok": bool(d.get("ok")), "pending": False,
                "detail": (f"proved from {len(d.get('starts') or [])} start(s)" if d.get("ok")
                           else str(d.get("reason", ""))),
                "summary": "", "seq": entry.get("seq", i), "t": entry.get("t", 0.0)})
    return out


def measurement_tally(rows: list[dict] | None) -> dict:
    """`{kind: {ok, pending, total}}` — the top of the section, so a marker sees the shape before
    reading the rows.

    Counted from the ROWS rather than recomputed from the chain, so the summary and the list can
    never disagree about what happened.

    An objective has no verdict — see `measurements` — so it only ever adds to a total, and a
    kind whose rows carry no verdict reads as a count rather than a score.
    """
    tally: dict = {}
    for r in rows or []:
        slot = tally.setdefault(r["kind"], {"ok": 0, "pending": 0, "total": 0})
        slot["total"] += 1
        if r.get("pending"):
            slot["pending"] += 1
        elif r.get("ok") is True:                # None is "no verdict", not a failure
            slot["ok"] += 1
    return tally


def artifact_hash(proof: dict) -> str:
    """The topology's fingerprint, from the chain's submit entry."""
    for entry in reversed(proof.get("entries") or []):
        data = entry.get("data") or {}
        art = data.get("artifact")
        if isinstance(art, dict) and art.get("sha256"):
            return str(art["sha256"])
    return ""


def prepare(payload: dict, code_row: dict, activity: dict,
            now: float | None = None, *, accepted_by: str = "") -> dict:
    """Validate a submission and shape the row to store. Raises `Rejected`.

    Integrity only. Nothing here judges the *work* — that is the report's job and ultimately the
    teacher's. What this refuses is a proof that has been tampered with, one recorded against a
    different plan, or one arriving under a code that cannot accept it.

    `accepted_by` names the member of staff taking a LATE submission by hand, and is the only thing
    that waives the code's expiry. Every other check runs exactly as it does for a student, because
    a teacher's judgement is about the deadline, not about whether the chain verifies — nobody
    should be able to launder a tampered proof through a kindness.
    """
    ok, reason = check_code(code_row, activity, now=now, staff=bool(accepted_by))
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

    # The runnable package. Optional only so an older gBuilder still submits something rather than
    # nothing; when it IS sent it must match, or the submission is refused outright.
    topology = payload.get("topology")
    if topology is not None and not topology_matches(topology, proof):
        raise Rejected(WRONG_TOPOLOGY,
                       "the submitted topology is not the one this proof was generated from")

    # The student's kernel code. Checked against the chain and REPORTED, never refused — see
    # check_sources. Kept in the payload so it travels with the submission into the report.
    if payload.get("shadows") and not isinstance(payload.get("shadows"), dict):
        raise Rejected(BAD_PROOF, "the submitted sources are not a mapping")

    if accepted_by:
        # Recorded IN the payload, so it travels with the submission and shows in the report. A
        # late submission that looked like any other would quietly rewrite the deadline.
        payload = dict(payload, accepted_by=str(accepted_by),
                       accepted_at=now if now is not None else time.time())

    started, finished = session_seconds(proof)
    return {"code": code_row["code"],
            # Recorded, never a refusal — the teacher weighs it, as with an overrun session.
            "late": 1 if is_late(code_row, now=now) else 0,
            # Carried from the code, which learned it at vend time. A TA's rehearsal on an
            # unreleased lab is a real submission and must not look like a student's.
            "draft": 1 if int(code_row.get("draft") or 0) else 0,
            "receipt": _proof.receipt_code(proof),
            "activity": activity["id"],
            "artifact_hash": artifact_hash(proof),
            "ts": now if now is not None else time.time(),
            "started": started, "finished": finished,
            # Seconds the recording was paused, so the session window and the minutes column
            # can be judged on time spent recording rather than on an overnight gap.
            "away": paused_seconds(proof),
            "verdict": "pass",
            "data": json.dumps(payload, sort_keys=True)}


def within_session(row: dict, activity: dict) -> bool:
    """Was the work done inside the session window the code granted?

    Reported, never enforced here — a run that overran is a fact for the teacher to weigh, not
    grounds for the server to throw away a student's evening.
    """
    # `or 0` deliberately, NOT session_minutes_for(): 0 and "unset" both mean "no per-attempt
    # window" HERE, and reporting an overrun against a default nobody chose would invent a fact.
    # For a duration of 0 the two readings already agree — no limit, and the lab is due at the
    # vending deadline (valid_until_for). They differ only when the field is absent, where this
    # side stays silent rather than assuming an hour.
    limit = float(activity.get("session_minutes") or 0) * 60.0
    if not limit or not row.get("started") or not row.get("finished"):
        return True
    spent = float(row["finished"]) - float(row["started"])
    if pausable(activity):
        # A pause is not work. For a lab the teacher allowed to be paused, the window is judged
        # on recording time; for a one-sitting lab the wall clock stands, and a chain that was
        # resumed anyway shows up in the report as more than one sitting.
        spent -= float(row.get("away") or 0.0)
    return spent <= limit


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #
def narrate(proof: dict) -> str:
    """The submission as prose: what was built, in what order, what was run.

    THIS IS THE REPORT in v1. There is no observation plan, so nothing is scored against
    expectations — the account is simply what the chain says happened, which is true by
    construction. `domain/narration.py` is model-free, so the same submission always reads the same
    way and a teacher can be shown it without a model in the loop.
    """
    try:
        from gini.domain import narration as _n
        from gini.domain import proof as _p
        entries = [_p.Entry(seq=e.get("seq", i), t=e.get("t", 0.0), kind=e.get("kind", ""),
                            data=e.get("data") or {}, prev=e.get("prev", ""))
                   for i, e in enumerate(proof.get("entries") or [])]
        return _n.narrate(entries)
    except Exception as e:                      # noqa: BLE001 — a report must still render
        return f"(could not narrate this chain: {e})"


def cook(proof: dict, title: str, answers: list, measured: list, sources: list) -> dict:
    """The grader's cooked view of this submission. Like `narrate`, it never takes the report
    down with it: a chain it cannot read yields an empty view and the raw log still renders."""
    try:
        from gini.domain import grader_view as _g
        return _g.cook(proof.get("entries") or [], title, answers, measured, sources)
    except Exception as e:                      # noqa: BLE001 — a report must still render
        return {"error": f"could not build the grader's view: {e}", "look_at": []}


def answered(proof: dict, questions: list[dict] | None) -> list[dict]:
    """Pair what the lab asked with what the student wrote, for one marker to read.

    Nothing is compared. `key` travels beside `given` and they are left side by side, because
    deciding whether "it uses a lock" answers "how does sleep avoid a lost wakeup?" is the whole
    of the marking and is not a string comparison.

    An unanswered question is REPORTED, not hidden — a blank is a fact about the attempt. And the
    prompt shown is the one recorded in the chain when it differs from the one on file, so a
    question edited after the lab cannot make an answer look like a reply to something it never
    replied to.
    """
    said: dict[str, list[dict]] = {}
    for e in proof.get("entries") or []:
        if (e or {}).get("kind") != _ev.ANSWER:
            continue
        d = e.get("data") or {}
        said.setdefault(str(d.get("id", "")), []).append(d)
    out = []
    for q in questions or []:
        turns = said.get(q["id"], [])
        last = turns[-1] if turns else {}
        out.append({
            "id": q["id"],
            "prompt": q.get("prompt", ""),
            # Only when it moved. A marker should be told about an edit, not made to compare two
            # identical strings on every row.
            "asked_as": (last.get("prompt", "") if last.get("prompt", q.get("prompt", ""))
                         != q.get("prompt", "") else ""),
            "given": last.get("text", ""),
            "answered": bool(turns),
            # They may think again; the chain keeps every pass. The count is here so a marker can
            # see that happened without the report reprinting all of them.
            "revisions": max(0, len(turns) - 1),
            "key": q.get("answer", ""),
        })
    return out


def report(row: dict, activity: dict, twins: list, attempts: list | None = None,
           questions: list[dict] | None = None) -> dict:
    """Everything a teacher sees for one receipt.

    Integrity, the account of what happened, whether it fit the session window, the duplicate flags,
    and who claimed it. Deliberately no score: v1 describes, the teacher judges.
    """
    payload = row.get("data")
    payload = json.loads(payload) if isinstance(payload, str) and payload else (payload or {})
    proof = payload.get("proof") or {}
    measured = measurements(proof)
    qa = answered(proof, questions)
    srcs = check_sources(payload.get("shadows"), proof)
    rechecks = recheck_tlabs(proof)
    cooked = cook(proof, (activity or {}).get("title", ""), qa, measured, srcs)
    # A T-Lab claim that did not re-check is the one thing a marker must not skim past, so it
    # joins the cooked view's look-at list beside a failed Load or a mismatched file.
    for r in rechecks:
        if r["confirmed"] is False:
            cooked.setdefault("look_at", []).append(
                f"T-Lab: the {r['kind']} on level {r['level']} ({r['version']}) does not "
                f"re-check — {r['why']}")
    one_sitting = not pausable(activity)
    sittings = int(cooked.get("sittings") or 0)
    if one_sitting and sittings > 1:
        # Not a refusal — gBuilder could not stop a student closing the laptop, and a crash
        # looks the same as a pause from here. It is the teacher's rule, so the teacher sees it.
        away = float(row.get("away") or 0.0) or paused_seconds(proof)
        cooked.setdefault("look_at", []).append(
            f"This lab is set to one sitting; the recording was resumed "
            f"{sittings - 1} time{'' if sittings == 2 else 's'}"
            + (f", paused for {round(away / 60.0)} min in all" if away else "") + ".")
    away_s = float(row.get("away") or 0.0)
    return {
        "receipt": row.get("receipt", ""),
        "activity": row.get("activity", ""),
        # Empty for an ordinary submission; the member of staff who took it, when it came in late
        # by hand. A marker must be able to see that the clock was overridden and by whom.
        "accepted_by": payload.get("accepted_by", ""),
        "late": bool(row.get("late")),
        # A rehearsal run by a TA before the lab was released. Shown so a marker never wonders
        # which student this was.
        "draft": bool(row.get("draft")),
        "title": (activity or {}).get("title", ""),
        "verdict": row.get("verdict", ""),
        "started": row.get("started", 0), "finished": row.get("finished", 0),
        # Minutes spent RECORDING: the pauses are taken out and shown beside it. A submission
        # that predates `away` has 0 there and reads exactly as it did.
        "minutes": round(max(0.0, (row.get("finished") or 0) - (row.get("started") or 0)
                             - away_s) / 60.0, 1),
        "paused_minutes": round(away_s / 60.0, 1),
        "sittings": sittings,
        "one_sitting": one_sitting,
        "within_session": within_session(row, activity or {}),
        # The COOKED view — a reading of the chain for the grader, shown first. The raw log below
        # (`narration`) is left exactly as captured; see gini.domain.grader_view.
        "cooked": cooked,
        "narration": narrate(proof),
        # Every T-Lab claim in the chain, replayed or re-explored here. Confirmed, not confirmed
        # (with why), or not checked (an older gini-core); never a refusal — see `recheck_tlabs`.
        "rechecks": rechecks,
        # Prompt, what the student wrote, and the teacher's key — side by side and unjudged.
        # There is no mark here and no auto-comparison: a person reads these.
        "questions": qa,
        # The kernel code, each file paired with the hash the chain says was compiled. An OS lab's
        # deliverable, which a marker could previously read nothing of.
        "sources": srcs,
        # What GINI measured — probes, riders, objectives — in one list across every kind of lab.
        # Described, never scored; see `measurements`.
        "measurements": measured,
        "measurement_tally": measurement_tally(measured),
        "entries": len(proof.get("entries") or []),
        "artifact": payload.get("artifact"),
        # Whether the teacher can actually OPEN this, or only read about it. An older gBuilder
        # sends a proof and no package; saying so beats a download button that yields nothing.
        "runnable": bool(payload.get("topology")),
        # Same topology under another code. FLAGGED, never rejected: a shared starter topology is a
        # legitimate reason for two submissions to match, and only the teacher can tell.
        "twins": twins,
        # Who claimed this work, and who ELSE tried. A refused claim is shown only here, to staff:
        # it is the reason the refusal is not a dead end, because the teacher can now take it up
        # with both students instead of one of them silently losing their evening.
        "student_id": row.get("student_id", ""),
        "claimed_at": row.get("claimed_at", 0),
        "contested_by": [a["student_id"] for a in (attempts or [])
                         if a.get("outcome") == "already_claimed"],
        "attempts": attempts or [],
    }


# --------------------------------------------------------------------------- #
# the class view — every submission to one activity as a row
# --------------------------------------------------------------------------- #
def summary_version() -> str:
    """What a cached row was computed BY. The source of `grader_view` itself, hashed, beside this
    module's own shape number — a version string alone would not move on a source checkout, and a
    newer reading of the chain must recompute rather than serve rows in an older shape."""
    import hashlib
    from pathlib import Path
    try:
        from gini.domain import grader_view as _g
        src = Path(_g.__file__).read_bytes()
    except Exception:                           # noqa: BLE001 — no core still gets a key
        src = b""
    return f"1:{hashlib.sha1(src).hexdigest()[:12]}"


def summarize(row: dict, activity: dict, questions: list[dict] | None) -> dict:
    """One submission's row for the class view: `grader_view`'s table plus the handful of cooked
    facts a class view sorts on. Built from the same `report` inputs, so a row can never disagree
    with the receipt it opens."""
    payload = row.get("data")
    try:
        payload = json.loads(payload) if isinstance(payload, str) and payload else (payload or {})
    except ValueError:
        payload = {}
    proof = payload.get("proof") or {}
    measured = measurements(proof)
    qa = answered(proof, questions)
    c = cook(proof, (activity or {}).get("title", ""), qa, measured,
             check_sources(payload.get("shadows"), proof))
    b = c.get("builds") or {}
    return {"table": c.get("table") or {"checks": [], "parts": {}, "metrics": [], "questions": []},
            "minutes": c.get("minutes", 0), "sittings": c.get("sittings", 0),
            "loads": b.get("loads", 0), "last_load_ok": b.get("last_ok"),
            "tests": (c.get("tests") or {}).get("runs", 0),
            "commands": (c.get("commands") or {}).get("count", 0),
            "look_at": list(c.get("look_at") or [])}


def class_table(subs: list[tuple[dict, dict]], marks: dict, twins: dict) -> dict:
    """`subs` is `[(submission list-row, summarize() of it)]`, oldest first.

    Columns are whatever the submissions recorded, in the order they first appear, so a lab whose
    spec gained a metric mid-term shows it without anything being configured. The column summary
    counts SUBMISSIONS, drafts left out: "0 of 28 passed share_stride" says the lab is broken, not
    the cohort, and a TA's rehearsal run must not be one of the 28.
    """
    metric_ids: list[str] = []
    part_ids: set[str] = set()
    q_total = 0
    for _row, s in subs:
        t = s.get("table") or {}
        for m in t.get("metrics") or []:
            if m["id"] not in metric_ids:
                metric_ids.append(m["id"])
        part_ids.update((t.get("parts") or {}).keys())
        q_total = max(q_total, len(t.get("questions") or []))
    parts = sorted(part_ids)

    rows, col = [], {m: {"passed": 0, "failed": 0, "pending": 0, "of": 0} for m in metric_ids}
    part_done = {p: 0 for p in parts}
    counted = 0
    for row, s in subs:
        t = s.get("table") or {}
        verdicts = {m["id"]: ("pending" if m.get("pending") else "passed" if m.get("ok")
                              else "failed") for m in t.get("metrics") or []}
        mk = marks.get(row["receipt"]) or {}
        qs = t.get("questions") or []
        rows.append({
            "receipt": row["receipt"], "student_id": row.get("student_id") or "",
            "ts": row.get("ts") or 0, "late": bool(row.get("late")),
            "draft": bool(row.get("draft")),
            "verdict": row.get("verdict") or "",
            "parts": {p: list((t.get("parts") or {}).get(p) or []) for p in parts},
            "metrics": {m: verdicts.get(m, "") for m in metric_ids},
            "answered": sum(1 for q in qs if q.get("answered")), "questions": len(qs),
            "minutes": s.get("minutes", 0), "loads": s.get("loads", 0),
            "last_load_ok": s.get("last_load_ok"), "commands": s.get("commands", 0),
            "look_at": len(s.get("look_at") or []),
            "twins": int(twins.get(row.get("artifact_hash") or "", 1)) - 1
            if row.get("artifact_hash") else 0,
            "mark": mk.get("mark", ""), "comment": mk.get("comment", ""),
            "marker": mk.get("marker", ""), "marked_at": mk.get("ts", 0)})
        if row.get("draft"):
            continue
        counted += 1
        for m in metric_ids:
            v = verdicts.get(m)
            col[m]["of"] += 1
            if v:
                col[m][v] += 1
        for p in parts:
            a_b = (t.get("parts") or {}).get(p)
            if a_b and a_b[0] == a_b[1]:
                part_done[p] += 1
    return {"metrics": metric_ids, "parts": parts, "questions": q_total, "rows": rows,
            "summary": {"submissions": counted, "metrics": col, "parts_complete": part_done}}


#: Leading characters a spreadsheet reads as the start of a formula. A student id is typed by the
#: student, so a cell beginning with one is quoted rather than handed to the gradebook to evaluate.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_FORMULA) else s


def class_csv(tbl: dict) -> str:
    """The class table as CSV, for the gradebook. One row per submission; the mark and comment are
    the marker's, everything else is what the chain recorded."""
    import csv
    import io
    from datetime import datetime, timezone
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["student_id", "receipt", "submitted_at", "late", "draft", "integrity",
                *[f"part {p}" for p in tbl["parts"]], *tbl["metrics"],
                "questions_answered", "questions_asked", "loads", "minutes", "mark", "comment",
                "marker"])
    for r in tbl["rows"]:
        when = (datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                if r["ts"] else "")
        w.writerow([_cell(r["student_id"]), r["receipt"], when,
                    "yes" if r["late"] else "", "yes" if r["draft"] else "", _cell(r["verdict"]),
                    *[(f"{v[0]}/{v[1]}" if v else "")
                      for v in (r["parts"][p] for p in tbl["parts"])],
                    *[r["metrics"][m] or "" for m in tbl["metrics"]],
                    r["answered"], r["questions"], r["loads"], r["minutes"],
                    _cell(r["mark"]), _cell(r["comment"]), _cell(r["marker"])])
    return out.getvalue()

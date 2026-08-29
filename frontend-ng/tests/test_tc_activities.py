"""Teaching Center activities: vending, deadlines, redemption, submission, privacy.

These are the rules a class depends on, so they are written as the things that must not happen:
a code issued after the deadline, a hoarded code still working, the same work handed in twice, a
student's identity landing in a table that promised not to hold one.

v1 has no observation plan, so nothing here mentions one. A code names an *activity*, and the
report narrates what the chain says happened. What is still enforced is integrity: the chain
verifies, and it was recorded under the code being redeemed.

No network, no model, no Docker. The store is a real SQLite database in a temp directory, because
the uniqueness constraints ARE the design and a mock would not have them.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini_teaching_center import activities as ACT                                       # noqa: E402
from gini_teaching_center.store import Store                                        # noqa: E402

from gini.domain import proof as P                             # noqa: E402
from gini.domain import ticket as T                            # noqa: E402

pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")

HOUR = 3600.0
NOW = 1_000_000.0


@pytest.fixture
def store(tmp_path):
    Store._instances.clear()          # the Store is keyed-singleton; isolate each test
    return Store(str(tmp_path))


def an_activity(store, *, released=True, vend_until=NOW + HOUR, session_minutes=60):
    rec = {"id": "comp535/lab1", "course": "comp535", "lab": "lab1", "title": "Multi-LAN",
           "brief": "Join two LANs with a router and show they can talk.",
           "status": "released" if released else "draft",
           "vend_until": vend_until, "session_minutes": session_minutes,
           "created": NOW, "released": NOW if released else 0.0}
    store.activity_put(rec)
    return store.activity("comp535/lab1")


def a_proof(code, *, t0=NOW, minutes=10.0, topo=None):
    """A chain that looks like real work, recorded under `code`.

    Two things here are load-bearing, and both were learned the hard way.

    `Chain.start(t=…)`: genesis is stamped with the real clock unless told otherwise, and the
    session window is measured from genesis (when the student armed) to the last entry. A helper
    that let genesis default would measure from *now* to a made-up past and quietly pass every
    timing test.

    `artifact_summary(...)`: the submit entry is built by the SAME function the recorder uses, so
    the fixture cannot drift into a shape the real gBuilder never emits. A hand-written
    `{"sha256": …, "devices": 1}` looks plausible, carries no `elements` map, and makes the
    narration report "Nothing was handed in." over a chain that plainly built something.
    """
    topo = topo if topo is not None else {"devices": [{"id": "1", "name": "R1", "type": "Router"}],
                                          "links": []}
    chain = P.Chain.start(code, assignment="comp535/lab1", gini_version="test", t=t0)
    for d in topo["devices"]:
        chain.append("place", {"id": d["id"], "type": d["type"], "name": d["name"]}, t=t0 + 1)
    chain.append("submit", {"artifact": P.artifact_summary(topo)}, t=t0 + minutes * 60)
    return P.build_proof(chain)


# -- vending ------------------------------------------------------------------ #
def test_a_released_activity_vends(store):
    ok, why = ACT.vending_open(an_activity(store), now=NOW)
    assert ok and why == ""


def test_a_draft_never_vends(store):
    ok, why = ACT.vending_open(an_activity(store, released=False), now=NOW)
    assert not ok and why == ACT.NOT_RELEASED


def test_nothing_vends_after_the_deadline(store):
    """The hard stop. This is the whole late-submission control."""
    act = an_activity(store, vend_until=NOW + HOUR)
    assert ACT.vending_open(act, now=NOW + HOUR + 1)[1] == ACT.VENDING_CLOSED


def test_the_deadline_is_exclusive_at_the_instant(store):
    act = an_activity(store, vend_until=NOW + HOUR)
    assert ACT.vending_open(act, now=NOW + HOUR)[1] == ACT.VENDING_CLOSED
    assert ACT.vending_open(act, now=NOW + HOUR - 1)[0]


def test_a_missing_activity_says_so(store):
    assert ACT.vending_open(None, now=NOW)[1] == ACT.NO_ACTIVITY


def test_every_visit_vends_a_different_code(store):
    act = an_activity(store)
    codes = {ACT.mint_code(act, now=NOW)["code"] for _ in range(50)}
    assert len(codes) == 50


def test_a_vended_code_is_one_gbuilder_will_accept(store):
    """Minted by ticket.mint, so the check digit is right. A hand-rolled code would be issued
    happily and then rejected at arming, blaming the student for a typo they did not make."""
    code = ACT.mint_code(an_activity(store), now=NOW)["code"]
    assert T.valid(code)


def test_a_code_names_the_activity_it_was_minted_for(store):
    act = an_activity(store)
    assert ACT.mint_code(act, now=NOW)["activity"] == act["id"]


# -- hoarding ----------------------------------------------------------------- #
def test_valid_until_is_anchored_to_the_deadline_not_to_issue_time(store):
    """The anti-hoarding property: a code taken on day one and one taken at the last minute die at
    the same moment, so taking a pile in advance gains nothing."""
    act = an_activity(store, vend_until=NOW + HOUR, session_minutes=60)
    early = ACT.mint_code(act, now=NOW)
    late = ACT.mint_code(act, now=NOW + HOUR - 1)
    assert early["valid_until"] == late["valid_until"] == NOW + HOUR + 60 * 60


def test_a_hoarded_code_stops_working(store):
    act = an_activity(store, vend_until=NOW + HOUR)
    row = ACT.mint_code(act, now=NOW)
    assert ACT.check_code(row, act, now=NOW + HOUR)[0]                    # still fine
    assert ACT.check_code(row, act, now=row["valid_until"] + 1)[1] == ACT.EXPIRED


def test_a_code_taken_a_minute_before_close_keeps_its_full_session(store):
    """The stated consequence of the design: work is accepted up to session_minutes past the
    vending close. A teacher wanting a hard 5pm cutoff closes vending at 4pm."""
    act = an_activity(store, vend_until=NOW + HOUR, session_minutes=60)
    row = ACT.mint_code(act, now=NOW + HOUR - 60)
    assert ACT.check_code(row, act, now=NOW + HOUR + 59 * 60)[0]


def test_no_vending_deadline_means_no_absolute_expiry(store):
    act = an_activity(store, vend_until=0)
    row = ACT.mint_code(act, now=NOW)
    assert row["valid_until"] == 0
    assert ACT.check_code(row, act, now=NOW + 10 * 365 * 24 * HOUR)[0]


# -- redeeming ---------------------------------------------------------------- #
def test_an_unknown_code_is_refused(store):
    assert ACT.check_code(None, an_activity(store), now=NOW)[1] == ACT.UNKNOWN_CODE


def test_a_spent_code_is_refused(store):
    act = an_activity(store)
    row = dict(ACT.mint_code(act, now=NOW), used=1)
    assert ACT.check_code(row, act, now=NOW)[1] == ACT.ALREADY_USED


def test_every_refusal_has_a_sentence_a_student_can_act_on():
    for reason in (ACT.NO_ACTIVITY, ACT.NOT_RELEASED, ACT.VENDING_CLOSED, ACT.UNKNOWN_CODE,
                   ACT.EXPIRED, ACT.ALREADY_USED, ACT.BAD_PROOF, ACT.DUPLICATE):
        assert ACT.message(reason).endswith((".", "!"))


def test_a_code_may_be_typed_however_it_lands():
    code = T.mint()
    assert ACT.normalize(code.pretty) == code.code
    assert ACT.normalize(code.pretty.lower()) == code.code


# -- submission --------------------------------------------------------------- #
def test_a_good_submission_is_prepared(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    proof = a_proof(row["code"])
    rec = ACT.prepare({"proof": proof}, row, act, now=NOW)
    assert rec["receipt"] == P.receipt_code(proof)
    assert rec["artifact_hash"] == proof["entries"][-1]["data"]["artifact"]["sha256"]
    assert rec["verdict"] == "pass"


def test_a_tampered_proof_is_refused(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    proof = a_proof(row["code"])
    proof["entries"][1]["data"]["name"] = "R2"                  # edit after the fact
    with pytest.raises(ACT.Rejected) as e:
        ACT.prepare({"proof": proof}, row, act, now=NOW)
    assert e.value.reason == ACT.BAD_PROOF


def test_a_proof_recorded_under_another_code_is_refused(store):
    """Replaying someone else's proof against your own code."""
    act = an_activity(store)
    mine = ACT.mint_code(act, now=NOW)
    theirs = ACT.mint_code(act, now=NOW)
    with pytest.raises(ACT.Rejected):
        ACT.prepare({"proof": a_proof(theirs["code"])}, mine, act, now=NOW)


def test_a_submission_with_no_proof_is_refused(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    with pytest.raises(ACT.Rejected):
        ACT.prepare({"artifact": {}}, row, act, now=NOW)


def test_an_expired_code_is_refused_at_submission_too(store):
    """A code can expire between arming and submitting."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    with pytest.raises(ACT.Rejected) as e:
        ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=row["valid_until"] + 1)
    assert e.value.reason == ACT.EXPIRED


# -- session timing ----------------------------------------------------------- #
def test_the_session_window_is_measured_from_the_chain(store):
    act = an_activity(store, session_minutes=60)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"], minutes=10)}, row, act, now=NOW)
    assert ACT.within_session(rec, act)


def test_syncing_a_day_late_is_not_late(store):
    """Finished inside the window, submitted the next morning. Arrival time is metadata."""
    act = an_activity(store, vend_until=0, session_minutes=60)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"], minutes=10)}, row, act, now=NOW + 24 * HOUR)
    assert ACT.within_session(rec, act)


def test_an_overrun_is_reported_not_rejected(store):
    """A run that overran is a fact for the teacher to weigh, not grounds to throw away an
    evening's work."""
    act = an_activity(store, session_minutes=60)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"], minutes=90)}, row, act, now=NOW)
    assert rec["verdict"] == "pass"
    assert not ACT.within_session(rec, act)


# -- duplicates --------------------------------------------------------------- #
def test_one_code_one_submission(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    assert store.submission_put(rec) is True
    assert store.submission_put(dict(rec, receipt="OTHR-RCPT")) is False


def test_the_same_proof_cannot_be_handed_in_twice(store):
    """David's proof file, submitted by Paul under his own code."""
    act = an_activity(store)
    a, b = ACT.mint_code(act, now=NOW), ACT.mint_code(act, now=NOW)
    proof = a_proof(a["code"])
    first = ACT.prepare({"proof": proof}, a, act, now=NOW)
    assert store.submission_put(first) is True
    assert store.submission_put(dict(first, code=b["code"])) is False       # receipt collides


def test_identical_work_under_two_codes_is_flagged_not_rejected(store):
    """The collusion case a receipt CANNOT see. A shared starter topology is a legitimate reason
    for two submissions to share an artifact, so this flags for review."""
    act = an_activity(store)
    a, b = ACT.mint_code(act, now=NOW), ACT.mint_code(act, now=NOW)
    ra = ACT.prepare({"proof": a_proof(a["code"])}, a, act, now=NOW)
    rb = ACT.prepare({"proof": a_proof(b["code"], t0=NOW + 500)}, b, act, now=NOW)
    assert ra["receipt"] != rb["receipt"]                 # different receipts...
    assert ra["artifact_hash"] == rb["artifact_hash"]     # ...same topology
    assert store.submission_put(ra) and store.submission_put(rb)
    twins = store.artifact_twins(rb["artifact_hash"], exclude_code=rb["code"])
    assert [t["code"] for t in twins] == [ra["code"]]


def test_a_receipt_finds_the_whole_submission(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    store.submission_put(rec)
    assert store.submission_by_receipt(rec["receipt"])["code"] == row["code"]


# -- the report --------------------------------------------------------------- #
def test_the_report_narrates_what_the_chain_says_happened(store):
    """v1's whole claim. Not 'did they meet the expectations' — 'here is what they did'."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    store.submission_put(rec)
    rep = ACT.report(store.submission_by_receipt(rec["receipt"]), act, [])
    assert rep["receipt"] == rec["receipt"]
    assert rep["title"] == "Multi-LAN"
    assert rep["narration"].strip()                      # prose, not an empty string
    assert "R1" in rep["narration"]                      # the placement is actually described
    assert rep["within_session"] is True


def test_the_report_never_asks_a_model_anything(store):
    """The narration is model-free by construction. If this ever needed a network it would fail
    here, in a test with no network."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    store.submission_put(rec)
    assert isinstance(ACT.narrate(json.loads(rec["data"])["proof"]), str)


def test_a_report_survives_an_unreadable_chain(store):
    """A teacher opening a receipt at 9am must get a page, not a 500. Say the chain is unreadable
    and show everything else."""
    assert "could not" in ACT.narrate({"entries": "not a list"}).lower()


# -- privacy ------------------------------------------------------------------ #
#
# The guarantee CHANGED when claiming was added, so these tests were rewritten rather than left to
# pass by accident. They did pass by accident: the old assertion looked for a column called
# `student`, and the new column is `student_id`.
#
# What v1 promises now is narrower and still worth defending: the portal learns an identity ONLY
# because a student volunteered it, and never for anything else.


def test_vending_records_no_identity_at_all(store):
    """Codes are untracked — the student page says so in as many words. An identity column on
    `activity_code` would make every vend a record of who asked."""
    cols = {r["name"] for r in store._all("PRAGMA table_info(activity_code)")}
    assert not (cols & {"student", "student_id", "username", "name", "sis_id", "email"})


def test_an_activity_records_no_identity(store):
    cols = {r["name"] for r in store._all("PRAGMA table_info(activity)")}
    assert not (cols & {"student", "student_id", "username", "name", "sis_id", "email"})


def test_a_submission_holds_an_identity_ONLY_once_it_is_claimed(store):
    """gBuilder submits anonymously. The id arrives later, from the student, or never."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    store.submission_put(rec)
    stored = store.submission_by_code(row["code"])
    assert stored["student_id"] == ""            # nothing known yet
    assert stored["claimed_at"] == 0

    ok, outcome = store.claim(rec["receipt"], "260123456", NOW + 60)
    assert ok and outcome == "claimed"
    assert store.submission_by_receipt(rec["receipt"])["student_id"] == "260123456"


def test_the_only_identity_column_in_the_whole_schema_is_the_claimed_one(store):
    """A single, named place where identity lives. If a future change adds another, this fails and
    the decision gets made deliberately instead of drifting in."""
    identity = {"student", "student_id", "sis_id", "email", "name"}
    found = set()
    for t in store._all("SELECT name FROM sqlite_master WHERE type='table'"):
        table = t["name"]
        if table.startswith("sqlite_"):        # sqlite_sequence.name is SQLite's own bookkeeping
            continue
        for c in store._all(f"PRAGMA table_info({table})"):
            if c["name"] in identity:
                found.add(f"{table}.{c['name']}")
    assert found == {"activity_submission.student_id", "claim_attempt.student_id"}, found


def test_a_stored_submission_carries_no_identity(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    store.submission_put(rec)
    stored = store.submission_by_code(row["code"])
    assert "student" not in stored
    blob = json.loads(stored["data"])
    assert "student" not in blob and "student_id" not in blob


def test_the_teaching_center_holds_no_model_client():
    """An acceptance criterion of v1, asserted rather than remembered: nothing under
    teaching-center/ may reach a model host."""
    for py in _TC.glob("*.py"):
        text = py.read_text()
        assert "Ollama" not in text and "ollama" not in text, py.name

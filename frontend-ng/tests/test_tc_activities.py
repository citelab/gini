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


def an_activity(store, *, released=True, vend_until=NOW + HOUR, session_minutes=60,
                grace_minutes=0):
    rec = {"id": "comp535/lab1", "course": "comp535", "lab": "lab1", "title": "Multi-LAN",
           "brief": "Join two LANs with a router and show they can talk.",
           "status": "released" if released else "draft",
           "vend_until": vend_until, "session_minutes": session_minutes,
           "grace_minutes": grace_minutes,
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
    """A code can expire between arming and handing in. Sealed AFTER the deadline: one sealed
    before it and only arriving late is taken — see test_tc_resend.py."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    late_work = a_proof(row["code"], t0=row["valid_until"] + 1, minutes=1)
    with pytest.raises(ACT.Rejected) as e:
        ACT.prepare({"proof": late_work}, row, act, now=row["valid_until"] + 120)
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


# -- the grace period --------------------------------------------------------- #
# The deadline used to be a cliff: a second past `valid_until` and the proof became unacceptable
# for ever, because `expired` is not a settled outcome in the outbox — gBuilder would retry until
# the end of time something the server would refuse every time. A grace period turns the cliff
# into a slope for the ordinary case (finished at 23:58, wifi dropped), and a member of staff
# taking the work in by hand covers the rest.
def test_inside_the_grace_period_the_work_is_taken(store):
    act = an_activity(store, grace_minutes=360)
    row = ACT.mint_code(act, now=NOW)
    assert ACT.check_code(row, act, now=row["valid_until"] + 5 * HOUR)[0]


def test_past_the_grace_period_it_is_refused_again(store):
    """A grace period is a longer deadline, not the absence of one."""
    act = an_activity(store, grace_minutes=360)
    row = ACT.mint_code(act, now=NOW)
    assert ACT.check_code(row, act, now=row["valid_until"] + 7 * HOUR)[1] == ACT.EXPIRED


def test_no_grace_period_is_still_a_hard_stop(store):
    """The default must not have moved: an existing course keeps the cutoff it was set up with."""
    act = an_activity(store)
    assert ACT.grace_seconds(act) == 0
    row = ACT.mint_code(act, now=NOW)
    assert ACT.check_code(row, act, now=row["valid_until"] + 1)[1] == ACT.EXPIRED


def test_work_inside_the_grace_period_is_tagged_late(store):
    """The point of accepting it is not to pretend it was on time."""
    act = an_activity(store, grace_minutes=360)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act,
                      now=row["valid_until"] + 2 * HOUR)
    assert rec["late"] == 1
    assert ACT.report(rec, act, [], [])["late"] is True


def test_work_that_beat_the_deadline_is_not_tagged_late(store):
    act = an_activity(store, grace_minutes=360)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW + 60)
    assert rec["late"] == 0


def test_a_grace_period_is_measured_from_the_deadline_not_from_arrival(store):
    """Otherwise every retry would buy another six hours and the deadline would never arrive."""
    act = an_activity(store, grace_minutes=60)
    row = ACT.mint_code(act, now=NOW)
    assert ACT.grace_seconds(act) == 60 * 60
    assert ACT.check_code(row, act, now=row["valid_until"] + 59 * 60)[0]
    assert not ACT.check_code(row, act, now=row["valid_until"] + 61 * 60)[0]


# -- a member of staff taking one in ------------------------------------------ #
def test_staff_may_waive_the_deadline(store):
    """The recovery for work that missed even the grace period. Everything else is still checked
    — this waives the clock, nothing else."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    late = row["valid_until"] + 30 * 24 * HOUR
    assert not ACT.check_code(row, act, now=late)[0]
    assert ACT.check_code(row, act, now=late, staff=True)[0]


def test_a_waiver_does_not_waive_the_proof_itself(store):
    """The failure this must not have: staff acceptance becoming a way to launder a tampered
    proof through a kindness."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    proof = a_proof(row["code"])
    proof["entries"][1]["data"]["name"] = "R2"
    with pytest.raises(ACT.Rejected) as e:
        ACT.prepare({"proof": proof}, row, act, now=NOW, accepted_by="prof")
    assert e.value.reason == ACT.BAD_PROOF


def test_a_waiver_does_not_waive_the_binding_to_the_code(store):
    act = an_activity(store)
    mine = ACT.mint_code(act, now=NOW)
    theirs = ACT.mint_code(act, now=NOW)
    with pytest.raises(ACT.Rejected):
        ACT.prepare({"proof": a_proof(theirs["code"])}, mine, act, now=NOW, accepted_by="prof")


def test_the_report_names_whoever_waived_the_deadline(store):
    """An override nobody can see is not a record. A teacher opening the work months later must
    be able to tell it did not arrive on its own."""
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act,
                      now=row["valid_until"] + HOUR, accepted_by="prof")
    r = ACT.report(rec, act, [], [])
    assert r["accepted_by"] == "prof"
    assert r["late"] is True


def test_an_ordinary_submission_names_nobody(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act, now=NOW)
    assert ACT.report(rec, act, [], [])["accepted_by"] == ""


def test_the_late_tag_survives_a_round_trip_through_the_database(store):
    """The tag is read back from the row, so a column that is written and never selected would
    show every hand-in as on time."""
    act = an_activity(store, grace_minutes=360)
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    rec = ACT.prepare({"proof": a_proof(row["code"])}, row, act,
                      now=row["valid_until"] + HOUR, accepted_by="prof")
    assert store.submission_put(rec)
    back = store.submission_by_receipt(rec["receipt"])
    assert back["late"] == 1
    assert ACT.report(back, act, [], [])["accepted_by"] == "prof"


# ============================================================================ #
# The release code — what makes a student link unguessable
#
# `/getcode?course=comp310_ecse427&lab=lab3` is a URL a student can EDIT. Knowing lab3's link
# means knowing lab4's, and lab4's brief describes an assignment nobody has been set yet. The
# code closes that, and doubles as the way a lab reaches its TAs before release.
# ============================================================================ #
def a_coded_activity(store, *, released=True, code="Z5G6"):
    act = an_activity(store, released=released)
    store.activity_set_release_code(act["id"], code)
    return store.activity(act["id"])


def test_a_link_without_the_code_vends_nothing():
    """The whole feature in one line."""
    act = {"status": "released", "release_code": "Z5G6", "vend_until": NOW + HOUR}
    ok, why = ACT.vending_open(act, NOW)
    assert not ok and why == ACT.BAD_LINK


def test_the_right_code_vends_as_before():
    act = {"status": "released", "release_code": "Z5G6", "vend_until": NOW + HOUR}
    assert ACT.vending_open(act, NOW, release_code="Z5G6") == (True, "")


def test_a_wrong_code_says_nothing_about_the_lab():
    """A refusal must not become an oracle. "wrong code", "no such lab" and "not released yet"
    all have to read the same, or a student learns which labs exist by trying."""
    seen = {ACT.message(ACT.vending_open(a, NOW, release_code="AAAA")[1])
            for a in ({"status": "released", "release_code": "Z5G6"},
                      {"status": "draft", "release_code": "Z5G6"})}
    assert len(seen) == 1, "a draft and a released lab answered a bad code differently"


def test_the_code_is_typed_the_way_a_student_types_it():
    """Same folding as an activity code: upper-cased, O read as 0, separators dropped. A teacher
    reads this over a bench and a student types it."""
    act = {"status": "released", "release_code": "Z5G0", "vend_until": NOW + HOUR}
    for typed in ("z5g0", "Z5G0", "z5-g0", " Z5GO "):
        assert ACT.vending_open(act, NOW, release_code=typed)[0], typed


def test_a_minted_code_avoids_the_confusable_letters():
    """I/L/O/U are absent from the alphabet on purpose — those are the two pairs that cause every
    mistyped code, and a four-symbol code has no check digit to catch one."""
    for _ in range(200):
        c = ACT.mint_release_code()
        assert len(c) == ACT.RELEASE_CODE_LEN
        assert set(c) <= set(T.ALPHABET)
        assert not (set(c) & set("ILOU"))


# -- draft runs: the lab reaches its TAs before it reaches the class ---------- #
def test_the_code_opens_a_draft_so_the_TAs_can_rehearse():
    """The second half of the feature. A teacher hands the link to their TAs, who run the lab end
    to end — the same vend, the same arm, the same submit — before anyone else can reach it."""
    act = {"status": "draft", "release_code": "Z5G6", "vend_until": NOW + HOUR}
    assert ACT.vending_open(act, NOW, release_code="Z5G6") == (True, "")


def test_an_unreleased_lab_with_no_code_is_still_closed():
    """The old refusal has to survive: a draft nobody has given a code to is nobody's business."""
    ok, why = ACT.vending_open({"status": "draft", "vend_until": NOW + HOUR}, NOW)
    assert not ok and why == ACT.NOT_RELEASED


def test_a_draft_run_is_marked_all_the_way_to_the_submission(store):
    """A TA's rehearsal is a real, verifying submission. It must not sit in the marking list
    looking like a student's."""
    act = a_coded_activity(store, released=False)
    issued = ACT.mint_code(act, now=NOW)
    issued["draft"] = 1 if ACT.is_draft_run(act) else 0
    store.code_put(issued)
    assert store.code(issued["code"])["draft"] == 1

    proof = a_proof(issued["code"])
    row = ACT.prepare({"proof": proof}, store.code(issued["code"]), act, now=NOW + 60)
    assert row["draft"] == 1
    assert store.submission_put(row)
    assert store.activity_submissions(act["id"])[0]["draft"] == 1


def test_a_released_lab_produces_ordinary_submissions(store):
    act = a_coded_activity(store, released=True)
    issued = ACT.mint_code(act, now=NOW)
    issued["draft"] = 1 if ACT.is_draft_run(act) else 0
    store.code_put(issued)
    row = ACT.prepare({"proof": a_proof(issued["code"])}, store.code(issued["code"]), act,
                      now=NOW + 60)
    assert row["draft"] == 0


# -- every lab has one, including the ones that predate the feature ----------- #
def test_a_lab_saved_before_release_codes_can_be_backfilled(store):
    """"Required" has to be true of every row, not of every row created from now on. A lab with
    no code would otherwise be either permanently unreachable or permanently unguarded."""
    an_activity(store)
    stale = store.activities_missing_release_code()
    assert [a["id"] for a in stale] == ["comp535/lab1"]
    store.activity_set_release_code("comp535/lab1", ACT.mint_release_code())
    assert store.activities_missing_release_code() == []


def test_an_activity_without_a_code_still_vends(store):
    """The empty case is a fallback, not a loophole: a row that somehow has no code must stay
    reachable rather than becoming unfixable."""
    act = an_activity(store)
    assert act.get("release_code", "") == ""
    assert ACT.vending_open(act, NOW)[0]


# -- duration 0: the lab is due AT the vending deadline ---------------------------------------- #
# A lab is run one of two ways, and "minutes per attempt" picks which. Above zero it is a TIMED
# ATTEMPT. At zero it has a FIXED HAND-IN TIME: everyone is due the moment vending stops, however
# early they started. Zero used to be impossible — the route refused it, and `or 60` would have
# turned it into an hour PAST the deadline anyway, the opposite of what it asks for.

def test_zero_minutes_makes_the_deadline_the_due_date(store):
    """The worked example: it is Wednesday 20:00, vending stops Thursday 21:00, duration 0. A
    student who takes a code NOW has until Thursday 21:00 — not an hour after it."""
    wed_2000 = NOW
    thu_2100 = NOW + 25 * HOUR
    act = an_activity(store, vend_until=thu_2100, session_minutes=0)
    row = ACT.mint_code(act, now=wed_2000)
    assert row["valid_until"] == thu_2100                      # the deadline itself, exactly


def test_zero_minutes_gives_everyone_the_same_moment(store):
    """The point of a fixed hand-in time: starting earlier buys nothing and starting later costs
    nothing. With a timed attempt the last code out still gets its full window past the deadline."""
    act = an_activity(store, vend_until=NOW + HOUR, session_minutes=0)
    early = ACT.mint_code(act, now=NOW)
    late = ACT.mint_code(act, now=NOW + HOUR - 1)
    assert early["valid_until"] == late["valid_until"] == NOW + HOUR


def test_a_zero_minute_code_works_up_to_the_deadline_and_not_past_it(store):
    act = an_activity(store, vend_until=NOW + HOUR, session_minutes=0)
    row = ACT.mint_code(act, now=NOW)
    assert ACT.check_code(row, act, now=NOW + HOUR - 1)[0] is True     # a minute before: fine
    assert ACT.check_code(row, act, now=NOW + HOUR)[1] == ACT.EXPIRED  # at the deadline: closed


def test_zero_is_a_real_duration_and_only_an_absent_one_defaults():
    """`or 60` could not tell 0 from unset, and that single character was the whole bug."""
    assert ACT.session_minutes_for({"session_minutes": 0}) == 0
    assert ACT.session_minutes_for({"session_minutes": 30}) == 30
    assert ACT.session_minutes_for({}) == ACT.DEFAULT_SESSION_MINUTES        # absent -> default
    assert ACT.session_minutes_for({"session_minutes": None}) == ACT.DEFAULT_SESSION_MINUTES


def test_zero_minutes_reports_no_per_attempt_overrun(store):
    """The two readings of 0 must agree: no per-attempt window here, due at the deadline there.
    A run of any length is inside a window that does not exist."""
    act = an_activity(store, vend_until=NOW + HOUR, session_minutes=0)
    assert ACT.within_session({"started": NOW, "finished": NOW + 10 * HOUR}, act) is True


def test_a_timed_attempt_is_unchanged(store):
    """The existing behaviour is load-bearing and must not move: 60 minutes still means the code
    lives an hour PAST the deadline, so a code taken at the last minute keeps its full session."""
    act = an_activity(store, vend_until=NOW + HOUR, session_minutes=60)
    assert ACT.mint_code(act, now=NOW)["valid_until"] == NOW + HOUR + 60 * 60


# -- pausing ------------------------------------------------------------------- #
#
# gBuilder lets a student pause a recording and resume it later; the chain carries a `stopped`
# mark and a `resumed` one with the length of the gap. The teacher may declare a lab one sitting,
# which hides gBuilder's Pause button. Here the flag changes only what the session window is
# judged against — and a one-sitting lab done in several is reported, never refused.

def a_paused_proof(code, *, t0=NOW, work_minutes=10.0, away_minutes=600.0):
    """`work_minutes` of recording split around a pause of `away_minutes`."""
    topo = {"devices": [{"id": "1", "name": "R1", "type": "Router"}], "links": []}
    chain = P.Chain.start(code, assignment="comp535/lab1", gini_version="test", t=t0)
    half = work_minutes * 30.0
    chain.append("place", {"id": "1", "type": "Router", "name": "R1"}, t=t0 + 1)
    chain.append("stopped", {"devices": 1, "links": 0, "ids": ["1"]}, t=t0 + half)
    chain.append("resumed", {"devices": 1, "links": 0, "ids": ["1"], "away": away_minutes * 60.0},
                 t=t0 + half + away_minutes * 60.0)
    chain.append("submit", {"artifact": P.artifact_summary(topo)},
                 t=t0 + work_minutes * 60.0 + away_minutes * 60.0)
    return P.build_proof(chain)


def test_a_lab_is_pausable_unless_the_teacher_said_one_sitting(store):
    assert ACT.pausable(an_activity(store)) is True
    assert ACT.pausable({}) is True, "a row that predates the column is every lab there was"
    store.activity_put({**an_activity(store), "one_sitting": 1})
    assert ACT.pausable(store.activity("comp535/lab1")) is False


def test_the_pause_is_measured_from_the_chain_and_stored_with_the_submission(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    proof = a_paused_proof(row["code"], work_minutes=10, away_minutes=600)
    assert ACT.paused_seconds(proof) == 600 * 60.0
    rec = ACT.prepare({"proof": proof}, row, act, now=NOW)
    assert rec["away"] == 600 * 60.0
    assert store.submission_put(rec)
    assert store.submission_by_code(row["code"])["away"] == 600 * 60.0


def test_a_pausable_lab_judges_the_window_on_recording_time(store):
    """Ten minutes of work around a ten-hour pause is inside a sixty-minute window."""
    act = an_activity(store, session_minutes=60)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_paused_proof(row["code"], work_minutes=10, away_minutes=600)},
                      row, act, now=NOW)
    assert ACT.within_session(rec, act) is True
    rec = ACT.prepare({"proof": a_paused_proof(row["code"], work_minutes=90, away_minutes=600)},
                      row, act, now=NOW)
    assert ACT.within_session(rec, act) is False, "the work itself overran"


def test_a_one_sitting_lab_judges_the_window_on_the_clock(store):
    """The teacher said one sitting: the clock stands, and the pause counts against it."""
    store.activity_put({**an_activity(store, session_minutes=60), "one_sitting": 1})
    act = store.activity("comp535/lab1")
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_paused_proof(row["code"], work_minutes=10, away_minutes=600)},
                      row, act, now=NOW)
    assert ACT.within_session(rec, act) is False


def test_a_submission_that_predates_the_pause_column_reads_as_it_did(store):
    act = an_activity(store, session_minutes=60)
    assert ACT.within_session({"started": NOW, "finished": NOW + 90 * 60}, act) is False
    assert ACT.within_session({"started": NOW, "finished": NOW + 10 * 60}, act) is True


def test_the_report_shows_recording_minutes_and_the_pause_beside_them(store):
    act = an_activity(store)
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_paused_proof(row["code"], work_minutes=10, away_minutes=600)},
                      row, act, now=NOW)
    r = ACT.report(rec, act, [])
    assert r["minutes"] == 10.0 and r["paused_minutes"] == 600.0
    assert r["sittings"] == 2 and r["one_sitting"] is False
    assert not any("one sitting" in x for x in r["cooked"]["look_at"])


def test_a_one_sitting_lab_done_in_two_is_flagged_for_the_teacher_not_refused(store):
    """gBuilder cannot stop a student closing the laptop, and a crash looks the same as a pause
    from here. The rule is the teacher's, so the teacher sees it."""
    store.activity_put({**an_activity(store), "one_sitting": 1})
    act = store.activity("comp535/lab1")
    row = ACT.mint_code(act, now=NOW)
    rec = ACT.prepare({"proof": a_paused_proof(row["code"], work_minutes=10, away_minutes=600)},
                      row, act, now=NOW)
    assert rec["verdict"] == "pass"
    r = ACT.report(rec, act, [])
    assert r["one_sitting"] is True
    flag = [x for x in r["cooked"]["look_at"] if "one sitting" in x]
    assert flag and "resumed 1 time" in flag[0] and "600 min" in flag[0]

"""Identity in the v1 Teaching Center. Written as ATTACKS, because that is what it defends against.

v1 has **staff accounts only** — a student never signs in, so the whole roster/enrolment-token
surface these tests used to cover is gone along with the thing it protected. What remains is
smaller and more load-bearing: a staff session vends codes and reads every submission in a course,
so an account taken over is a master key on an open port.

The claim-token flow is the specific hole being closed. Usernames are guessable, so
first-password-wins would let a stranger become a teacher just by reaching the portal first.
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
sys.path.insert(0, str(_TC))

from gini_teaching_center import accounts as A           # noqa: E402
from gini_teaching_center.store import Store             # noqa: E402

GOOD = "a-good-password"


@pytest.fixture()
def portal(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ADMIN_ID", "boss")
    Store._instances.clear()
    acc = A.Accounts(tmp_path)
    acc.root = tmp_path
    return acc


# -- claiming ----------------------------------------------------------------- #
def test_a_stranger_cannot_claim_an_account_they_guessed(portal):
    """THE hole the claim token exists to close."""
    portal.add_staff("ada")
    r = portal.claim("ada", "guessed", GOOD)
    assert not r["ok"]


def test_the_claim_token_is_spent_on_use(portal):
    made = portal.add_staff("ada")
    assert portal.claim("ada", made["claim_token"], GOOD)["ok"]
    assert not portal.claim("ada", made["claim_token"], "another-password")["ok"]


def test_claiming_returns_a_session_and_never_stores_the_password(portal):
    made = portal.add_staff("ada")
    r = portal.claim("ada", made["claim_token"], GOOD)
    assert r["ok"] and r["session"]
    rec = portal.store.account("ada")
    assert GOOD not in str(rec)                       # not in the row, in any field
    assert rec["hash"] and rec["salt"]


def test_weak_passwords_are_refused(portal):
    made = portal.add_staff("ada")
    assert not portal.claim("ada", made["claim_token"], "short")["ok"]


def test_an_unclaimed_account_is_told_to_claim_rather_than_just_refused(portal):
    """A teacher staring at 'wrong password' will retype it ten times. Say what is actually
    needed."""
    portal.add_staff("ada")
    r = portal.login("ada", GOOD)
    assert not r["ok"] and "claim" in r["error"].lower()


def test_a_genuinely_unknown_username_says_so_plainly(portal):
    assert "No such account" in portal.login("nobody", GOOD)["error"]


# -- sessions ----------------------------------------------------------------- #
def test_wrong_password_is_refused_and_the_right_one_is_not(portal):
    made = portal.add_staff("ada")
    portal.claim("ada", made["claim_token"], GOOD)
    assert not portal.login("ada", "wrong")["ok"]
    assert portal.login("ada", GOOD)["ok"]


def test_sessions_expire_and_expired_tokens_stop_working(portal, monkeypatch):
    made = portal.add_staff("ada")
    s = portal.claim("ada", made["claim_token"], GOOD)["session"]
    assert portal.whoami(s)["who"] == "ada"
    later = time.time() + A.SESSION_TTL + 1
    monkeypatch.setattr(A.time, "time", lambda: later)   # patch the module under test, not stdlib
    assert portal.whoami(s) is None


def test_logout_kills_the_session(portal):
    made = portal.add_staff("ada")
    s = portal.claim("ada", made["claim_token"], GOOD)["session"]
    portal.logout(s)
    assert portal.whoami(s) is None


def test_a_made_up_token_is_nobody(portal):
    assert portal.whoami("not-a-real-token") is None
    assert portal.whoami("") is None


# -- roles -------------------------------------------------------------------- #
def test_a_new_account_is_a_teacher_unless_asked_otherwise(portal):
    assert portal.add_staff("ada")["role"] == A.TEACHER
    assert portal.add_staff("zoe", role=A.ADMIN)["role"] == A.ADMIN


def test_the_session_carries_the_role_it_was_signed_in_with(portal):
    made = portal.add_staff("zoe", role=A.ADMIN)
    s = portal.claim("zoe", made["claim_token"], GOOD)["session"]
    assert portal.whoami(s)["role"] == A.ADMIN


def test_the_last_admin_cannot_be_removed_or_demoted(portal):
    """Otherwise the portal is left with no way in, and there is no recovery procedure."""
    portal.ensure_admin()
    assert not portal.remove_staff("boss")["ok"]
    assert not portal.set_role("boss", A.TEACHER)["ok"]


def test_an_admin_can_step_down_once_someone_else_is_admin(portal):
    portal.ensure_admin()
    made = portal.add_staff("zoe", role=A.ADMIN)
    portal.claim("zoe", made["claim_token"], GOOD)
    assert portal.set_role("boss", A.TEACHER)["ok"]


def test_a_removed_account_takes_its_pending_claim_with_it(portal):
    """A claim token left behind is a spare key to an account that no longer exists — until
    someone re-adds the name."""
    portal.ensure_admin()
    portal.add_staff("ada")
    assert portal.remove_staff("ada")["ok"]
    assert portal.store.kv_get("claim:ada") is None


# -- bootstrap ---------------------------------------------------------------- #
def test_a_fresh_portal_mints_a_claim_token_rather_than_standing_open(portal):
    """Without this, whoever reaches the port first becomes the admin."""
    token = portal.ensure_admin()
    assert token
    assert not portal.login("boss", GOOD)["ok"]
    assert portal.claim("boss", token, GOOD)["ok"]


def test_ADMIN_PASSWORD_is_authoritative_each_boot(portal, monkeypatch):
    """The real trap, kept from v0: the env password only applied at account *creation*, so an
    admin who set it on a portal that already existed was silently ignored and locked out."""
    portal.ensure_admin()                                  # exists, unclaimed
    monkeypatch.setenv("ADMIN_PASSWORD", "set-on-the-second-boot")
    assert portal.ensure_admin() is None                   # reconciled, nothing to print
    assert portal.login("boss", "set-on-the-second-boot")["ok"]


def test_setting_ADMIN_PASSWORD_retires_the_claim_token(portal, monkeypatch):
    token = portal.ensure_admin()
    monkeypatch.setenv("ADMIN_PASSWORD", "now-there-is-a-password")
    portal.ensure_admin()
    assert not portal.claim("boss", token, GOOD)["ok"]


def test_ensure_admin_is_idempotent(portal, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "steady-as-she-goes")
    portal.ensure_admin()
    portal.ensure_admin()
    assert portal.login("boss", "steady-as-she-goes")["ok"]
    assert len([a for a in portal.store.accounts() if a["role"] == A.ADMIN]) == 1


# -- privacy ------------------------------------------------------------------ #
def test_there_is_no_student_account_table_at_all(portal):
    """v1's privacy property, asserted rather than trusted: the portal cannot leak who did the work
    because it never learns it."""
    tables = {r["name"] for r in
              portal.store._all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not (tables & {"enrolment", "profile", "student", "roster"})


# -- getting back in ----------------------------------------------------------- #
def test_a_pending_claim_token_is_reprinted_on_every_boot(portal):
    """It was a one-time SIGHTING as well as a one-time secret.

    The token printed only on the boot that created the account. Miss that line in a scrollback —
    on a run you were doing for some other reason — and the legitimate admin is locked out, with no
    route back but an environment variable they would have to already know about, or a SQLite
    query against the live database. Reprinting while it is still unclaimed costs nothing.
    """
    first = portal.ensure_admin()
    assert first                                  # created, token issued
    assert portal.ensure_admin() == first         # and still offered on the next start
    assert portal.ensure_admin() == first         # and the one after that


def test_claiming_stops_the_reprinting_for_good(portal):
    """The other half: once it is spent it must not linger on the console for the next person who
    walks past the terminal."""
    token = portal.ensure_admin()
    assert portal.claim("boss", token, GOOD)["ok"]
    assert portal.ensure_admin() is None


def test_an_admin_password_still_beats_a_lost_token(portal, monkeypatch):
    """The escape hatch that already existed, pinned so the reprint above cannot quietly replace
    it: ADMIN_PASSWORD is authoritative on every boot, claimed or not."""
    portal.ensure_admin()
    monkeypatch.setenv("ADMIN_PASSWORD", GOOD)
    assert portal.ensure_admin() is None          # no token — the password settled it
    assert portal.login("boss", GOOD)["ok"]


# -- recovery: the admin un-claims an account so it can be claimed again -------- #
# The whole of password recovery, and deliberately the whole of it. This server has no address to
# send mail from and no second factor, so a "forgot my password" link would be a way in for whoever
# reads the mailbox rather than a recovery. The admin knows their teachers.
def _claimed(portal, who="ada"):
    made = portal.add_staff(who)
    return portal.claim(who, made["claim_token"], GOOD)


def test_a_reset_account_can_be_claimed_again_with_a_new_password(portal):
    """The point of the feature, start to finish."""
    _claimed(portal)
    r = portal.reset_staff("ada", by="boss")
    assert r["ok"] and r["claim_token"]
    again = portal.claim("ada", r["claim_token"], "a-different-one")
    assert again["ok"]
    assert portal.login("ada", "a-different-one")["ok"]


def test_the_old_password_stops_working(portal):
    _claimed(portal)
    portal.reset_staff("ada", by="boss")
    assert not portal.login("ada", GOOD)["ok"]


def test_the_old_claim_token_stops_working_too(portal):
    """A fresh token, not the old one re-offered. The reason to reset may be that the first token
    went astray, and re-issuing it would recover nothing."""
    made = portal.add_staff("ada")
    portal.claim("ada", made["claim_token"], GOOD)
    r = portal.reset_staff("ada", by="boss")
    assert r["claim_token"] != made["claim_token"]
    assert not portal.claim("ada", made["claim_token"], "another-one")["ok"]


def test_a_reset_ends_every_session_the_account_had(portal):
    """A session is a bearer token good for a working day. Leaving them alive would mean a reset
    that changed nothing for the person you reset it because of."""
    first = _claimed(portal)
    second = portal.login("ada", GOOD)
    assert portal.whoami(first["session"]) and portal.whoami(second["session"])
    r = portal.reset_staff("ada", by="boss")
    assert r["sessions_ended"] == 2
    assert portal.whoami(first["session"]) is None
    assert portal.whoami(second["session"]) is None


def test_a_reset_keeps_the_role(portal):
    """A forgotten password is not a demotion. An admin who came back as a teacher would need
    another admin to put them back, which is the situation this is meant to get out of."""
    made = portal.add_staff("ada", role=A.ADMIN)
    portal.claim("ada", made["claim_token"], GOOD)
    r = portal.reset_staff("ada", by="boss")
    assert r["role"] == A.ADMIN
    assert portal.claim("ada", r["claim_token"], "a-new-one")["role"] == A.ADMIN


def test_a_reset_keeps_the_courses_they_staff(portal):
    """`remove_staff` drops course_staff rows and `reset_staff` must not — the difference between
    someone leaving and someone forgetting a password."""
    _claimed(portal)
    portal.store.put_course({"id": "comp535", "title": "Networks"})
    portal.store.add_staff("comp535", "ada")
    portal.reset_staff("ada", by="boss")
    assert "ada" in portal.store.course_staff("comp535")


def test_you_cannot_reset_your_own_account(portal):
    """It is never the recovery path — you have to be signed in to press it, so you have not
    forgotten anything — and all it can do is sign you out holding a token you must not lose. For
    the last admin that is a locked-out portal."""
    _claimed(portal)
    r = portal.reset_staff("ada", by="ada")
    assert not r["ok"] and "your own" in r["error"]
    assert portal.login("ada", GOOD)["ok"], "the refusal must not half-apply"


def test_resetting_an_account_that_does_not_exist_says_so(portal):
    assert not portal.reset_staff("nobody", by="boss")["ok"]


def test_an_unclaimed_account_can_be_given_a_fresh_token(portal):
    """The same button, for the other half of "it never arrived" — a token handed over and lost
    before it was ever used."""
    made = portal.add_staff("ada")
    r = portal.reset_staff("ada", by="boss")
    assert r["ok"] and r["claim_token"] != made["claim_token"]
    assert portal.claim("ada", r["claim_token"], GOOD)["ok"]


def test_the_new_token_is_visible_to_the_admin_afterwards(portal):
    """The staff list shows it for as long as the account is unclaimed. An admin who closed the
    page has not lost the only copy — the same reasoning as reprinting the admin's own token."""
    _claimed(portal)
    r = portal.reset_staff("ada", by="boss")
    row = next(s for s in portal.staff() if s["username"] == "ada")
    assert row["claimed"] is False and row["claim_token"] == r["claim_token"]


def test_a_claimed_account_shows_no_token_to_anybody(portal):
    """The other half: it is deleted on use, so the row goes quiet again."""
    _claimed(portal)
    portal.reset_staff("ada", by="boss")
    r = portal.reset_staff("ada", by="boss")
    portal.claim("ada", r["claim_token"], "a-new-one")
    row = next(s for s in portal.staff() if s["username"] == "ada")
    assert row["claimed"] is True and not row["claim_token"]

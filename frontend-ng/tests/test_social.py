"""Phases B–C: groups, presence, and chat — with the privacy invariants tested as ATTACKS.

The load-bearing decision (design D4): **student↔student DMs are outside the staff plane.** Not read
by the teacher, not read by ProfAI, not in the console. The only path from a private DM to staff is a
student *reporting* it — consent-based disclosure.

This is a chosen blind spot, and the tests exist to make sure it stays exactly as wide as chosen: no
wider (staff can't peek) and no narrower (a report must still work).
"""
import json
import os
import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
sys.path.insert(0, str(_TC))

import social as S                                       # noqa: E402
import teacher as T                                      # noqa: E402


@pytest.fixture()
def world(tmp_path):
    c = T.Course(tmp_path, "c1")
    c.enrol("ana", name="Ana", group="g1")
    c.enrol("ben", name="Ben", group="g1")
    c.enrol("cara", name="Cara", group="g2")
    c.enrol("dan", name="Dan")                            # ungrouped: groups are optional
    return S.Social(tmp_path, c), c


# -- groups ------------------------------------------------------------------ #
def test_groups_are_teacher_formed_and_optional(world):
    soc, c = world
    assert c.group_of("ana") == "g1"
    assert set(c.members_of("g1")) == {"ana", "ben"}
    assert c.group_of("dan") == ""                       # ungrouped is a smaller product, not an error
    assert soc.my_group("dan") == {"group": "", "members": []}


def test_the_group_view_shows_where_your_teammates_ARE(world):
    soc, _ = world
    soc.heartbeat("ana", {"lesson_id": "lab01", "title": "Build a LAN", "level": 3,
                          "met": 5, "total": 7})
    soc.heartbeat("ben", {"lesson_id": "lab01", "title": "Build a LAN", "level": 1,
                          "met": 1, "total": 7})
    g = soc.my_group("ben")
    assert g["group"] == "g1"
    me = next(m for m in g["members"] if m["id"] == "ben")
    ana = next(m for m in g["members"] if m["id"] == "ana")
    assert me["me"] and g["members"][0]["id"] == "ben"   # you come first in your own list
    assert ana["online"] and ana["progress"]["level"] == 3   # "Ana is on Level 3" — the useful view


def test_re_enrolling_does_not_silently_reset_a_token_or_a_group(world):
    _, c = world
    c.enrol("ana", name="Ana", group="g1", sis_id="9001")
    before = next(r for r in c.roster() if r["id"] == "ana")
    c.enrol("ana", name="Ana Fixed-Spelling")            # teacher fixes a typo
    after = next(r for r in c.roster() if r["id"] == "ana")
    assert after["name"] == "Ana Fixed-Spelling"
    assert after["token"] == before["token"]             # …without invalidating the token she's about to use
    assert after["group"] == "g1"                        # …or dropping her out of her group
    assert after["sis_id"] == "9001"                     # …or forgetting her registrar number


def test_username_and_school_id_are_separate_fields(world):
    _, c = world
    row = c.enrol("vikram", name="Vikram S", sis_id="2513")
    assert row["id"] == "vikram"                          # the login handle — a friendly nickname
    assert row["sis_id"] == "2513"                        # bookkeeping only, never a login
    assert row["name"] == "Vikram S"


# -- who may talk to whom ---------------------------------------------------- #
def test_you_can_only_dm_inside_your_own_group(world):
    soc, _ = world
    assert soc.can_send("ana", "ben")[0]                 # same group
    assert not soc.can_send("ana", "cara")[0]            # different group
    assert not soc.can_send("dan", "ana")[0]             # ungrouped: no peers
    assert soc.can_send("ana", "teacher")[0]             # everyone can reach the instructor
    assert soc.send("ana", "cara", "hi")["ok"] is False  # …and the SERVER enforces it, not the UI


# -- THE privacy invariant --------------------------------------------------- #
def test_the_teacher_cannot_read_student_to_student_DMs(world):
    """The chosen blind spot. If this test ever goes green-by-deletion, the product changed."""
    soc, _ = world
    soc.send("ana", "ben", "this lab is driving me insane")
    soc.send("ana", "group", "anyone got the router working?")
    soc.send("ana", "teacher", "I'm stuck on subnetting")

    staff = soc.inbox("teacher", "teacher")
    bodies = [m["body"] for m in staff]
    assert "anyone got the router working?" in bodies    # group channel: yes, that's a workspace
    assert "I'm stuck on subnetting" in bodies           # to the instructor: obviously
    assert "this lab is driving me insane" not in bodies  # the private DM: NO
    assert all(m["chan_kind"] != "dm" for m in staff)


def test_a_student_cannot_read_a_dm_they_are_not_part_of(world):
    soc, _ = world
    soc.send("ana", "ben", "secret")
    assert "secret" not in [m["body"] for m in soc.inbox("cara")]
    assert "secret" in [m["body"] for m in soc.inbox("ben")]      # …but the recipient can
    assert "secret" in [m["body"] for m in soc.inbox("ana")]      # …and so can the sender


def test_reporting_is_the_ONLY_path_from_a_private_dm_to_staff(world):
    """Consent-based disclosure: staff see it because someone showed them, not because they watched."""
    soc, _ = world
    m = soc.send("ben", "ana", "something horrible")["message"]
    assert soc.reports() == []                                     # nothing, until a human acts

    assert soc.report("ana", m["id"], note="this is not ok")["ok"]
    rep = soc.reports()
    assert len(rep) == 1 and rep[0]["message"]["body"] == "something horrible"
    assert rep[0]["by"] == "ana" and rep[0]["note"] == "this is not ok"

    # …and you cannot report a message you were never party to (that would be a read primitive)
    assert not soc.report("cara", m["id"])["ok"]


def test_private_dms_age_out_but_course_channels_keep_the_term(world, monkeypatch):
    soc, _ = world
    soc.send("ana", "ben", "ephemeral")
    soc.send("ana", "group", "durable")

    real = S.time.time
    monkeypatch.setattr(S.time, "time", lambda: real() + (S.DM_TTL_DAYS + 1) * 86400)
    bodies = [m["body"] for m in soc.inbox("ana")]
    assert "ephemeral" not in bodies        # less to leak, less to regret
    assert "durable" in bodies


def test_channels_are_derived_from_what_is_readable_so_they_cannot_drift(world):
    soc, _ = world
    ch = {c["id"]: c for c in soc.channels("ana")}
    assert "teacher:ana" in ch and "group:g1" in ch
    assert "dm:ana|ben" in ch                            # her groupmate
    assert not any(c["id"].startswith("dm:") and "cara" in c["id"] for c in ch.values())

    solo = soc.channels("dan")                           # ungrouped
    assert [c["id"] for c in solo] == ["teacher:dan"]    # just the instructor — no group, no peers

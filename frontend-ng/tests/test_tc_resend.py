"""A hand-in sealed before the deadline is taken whenever it gets through.

Reported 2026-11-21: a student pressed Hand in on A-Lab 03 on November 17, was shown a receipt,
and the upload never landed. Four days later gBuilder's outbox still said "1 submission waiting to
send · 6 attempts", with the VPN on or off — because every resend after the deadline was refused
as `expired`, and `expired` is deliberately not settled in the outbox. A correct receipt, for work
the course would never see without a teacher taking the file in by hand.

Whether work is late is a question about when it was done. The chain's closing `submit` entry
records that, inside the MAC, so the server now judges a resend on it: sealed before the deadline
(or inside the grace period) is taken, verified exactly as any upload, and shown to the teacher
as having arrived late. And a teacher who does take a file in by hand now keeps the kernel code an
outbox entry carries — dropping it took an A-Lab in without the part it is marked on.
"""
from __future__ import annotations

import time

import pytest
from gini.domain import proof as P
from test_tc_activities import HOUR, NOW, a_proof, an_activity, store  # noqa: F401 (fixture)
from test_tc_handin import _call, a_chain, a_topology, tc  # noqa: F401 (tc is a fixture)

from gini.services import tc_submit

ACT = pytest.importorskip("gini_teaching_center.activities")


def _row(store, act):
    row = ACT.mint_code(act, now=NOW)
    store.code_put(row)
    return row


def _after(row, hours=24.0):
    return float(row["valid_until"]) + hours * HOUR


# --------------------------------------------------------------------------- #
# the rule
# --------------------------------------------------------------------------- #

def test_sealed_before_the_deadline_is_taken_days_later_and_not_late(store):
    act = an_activity(store)
    row = _row(store, act)
    proof = a_proof(row["code"])                                  # sealed ten minutes in
    rec = ACT.prepare({"proof": proof}, row, act, now=_after(row, 96))
    assert rec["late"] == 0
    import json
    data = json.loads(rec["data"])
    assert data["resent_after_deadline"] == _after(row, 96)
    assert data["sealed_at"] == ACT.sealed_at(proof) > 0


def test_sealed_inside_the_grace_period_is_taken_and_late(store):
    act = an_activity(store, grace_minutes=120)
    row = _row(store, act)
    proof = a_proof(row["code"], t0=float(row["valid_until"]) + 60, minutes=1)
    rec = ACT.prepare({"proof": proof}, row, act, now=_after(row, 48))
    assert rec["late"] == 1


def test_sealed_after_the_deadline_is_still_refused(store):
    """The premise: the deadline still means something."""
    act = an_activity(store)
    row = _row(store, act)
    proof = a_proof(row["code"], t0=float(row["valid_until"]) + 60, minutes=1)
    with pytest.raises(ACT.Rejected) as e:
        ACT.prepare({"proof": proof}, row, act, now=_after(row))
    assert e.value.reason == ACT.EXPIRED


def test_a_seal_from_the_future_or_from_before_the_code_is_not_believed(store):
    act = an_activity(store)
    row = _row(store, act)
    early = a_proof(row["code"], t0=NOW - 3 * HOUR)               # before the code was issued
    assert not ACT.sealed_in_time(early, row, act, now=_after(row))
    ahead = a_proof(row["code"], t0=NOW + 10)
    assert not ACT.sealed_in_time(ahead, row, act, now=NOW - 2 * HOUR)


def test_an_edited_seal_time_fails_verification(store):
    """The seal time is trusted only because the chain is verified after it is read."""
    act = an_activity(store)
    row = _row(store, act)
    proof = a_proof(row["code"], t0=float(row["valid_until"]) + 60)
    proof["entries"][-1]["t"] = NOW + 60                          # "I handed in on time"
    with pytest.raises(ACT.Rejected) as e:
        ACT.prepare({"proof": proof}, row, act, now=_after(row))
    assert e.value.reason == ACT.BAD_PROOF


def test_a_chain_that_does_not_end_in_a_hand_in_has_no_seal():
    assert ACT.sealed_at({"entries": [{"kind": "place", "t": 5.0}]}) == 0.0
    assert ACT.sealed_at({}) == 0.0


# --------------------------------------------------------------------------- #
# over the wire, as gBuilder's outbox sends it
# --------------------------------------------------------------------------- #

def _lapse(code, *, issued_hours_ago=6.0):
    """The deadline passed an hour ago; the code was issued well before. Returns the row."""
    from gini_teaching_center import server as S
    row = dict(S._STORE.code(ACT.normalize(code)))
    row["issued"] = time.time() - issued_hours_ago * HOUR
    row["valid_until"] = time.time() - HOUR
    S._STORE.code_put(row)
    return row


SHADOWS = {"m1/kernel/sem.c": {"name": "sem.c", "sha256": "ab" * 32, "lines": 3, "bytes": 40,
                               "text": "int sem;\n"}}


def test_the_outbox_resend_gets_through_with_its_kernel_code(tc):
    code = tc.code()
    topo = a_topology()
    _lapse(code)
    proof = a_chain(code, topo, t0=time.time() - 3 * HOUR)        # handed in before the deadline
    r = tc_submit.submit(tc.url, code, proof, topo, SHADOWS)
    assert r["ok"], r
    rep = tc.staff("/api/receipt?receipt=" + r["receipt"])
    assert rep["late"] is False
    assert rep["resent_after_deadline"] > rep["sealed_at"] > 0
    assert [s["path"] for s in rep["sources"]] == ["m1/kernel/sem.c"]
    again = tc_submit.submit(tc.url, code, proof, topo, SHADOWS)  # the outbox's next attempt
    assert again["reason"] in ("duplicate", "already_used")       # settled: the outbox forgets it


def test_work_finished_after_the_deadline_is_still_refused_over_the_wire(tc):
    code = tc.code()
    topo = a_topology()
    _lapse(code)
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), topo)  # sealed just now
    assert not r["ok"] and r["reason"] == "expired"


def test_taking_an_outbox_file_in_by_hand_keeps_the_kernel_code(tc):
    code = tc.code()
    topo = a_topology()
    proof = a_chain(code, topo)
    _lapse(code)
    r = _call(tc.url, "/api/submissions/accept",
              {"proof": proof, "topology": topo, "shadows": SHADOWS}, tc.token)
    assert r["ok"], r
    rep = tc.staff("/api/receipt?receipt=" + r["receipt"])
    assert [s["path"] for s in rep["sources"]] == ["m1/kernel/sem.c"] and rep["accepted_by"] == "boss"


def test_the_proof_format_still_ends_in_its_seal():
    """sealed_at reads the chain's last entry; that only works while a hand-in closes the chain."""
    assert P.SUBMIT == "submit"

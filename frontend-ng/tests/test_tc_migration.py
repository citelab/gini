"""Opening a database that an OLDER Teaching Center created.

Written after a teacher's first save died with `table activity has no column named brief`.
`CREATE TABLE IF NOT EXISTS` does nothing at all to a table that already exists, so a v0 database
keeps its v0 columns and the first write to a new one raises. Every real installation has an
existing database; a fresh temp directory is the ONE case where this cannot go wrong — which is
exactly why the whole suite missed it, because every other test starts from `tmp_path`.

So these tests start from a hand-built v0 database with v0 rows in it, and assert two things: the
server works afterwards, and the teacher's existing data is still there.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from store import Store                                       # noqa: E402

# The v0 shape, as it actually shipped: activity carried the plan, had no course/lab/brief columns,
# and there were tables for lessons and a student roster.
V0 = """
CREATE TABLE account (username TEXT PRIMARY KEY, role TEXT DEFAULT 'teacher',
                      salt TEXT, hash TEXT, n INTEGER, r INTEGER, p INTEGER, claimed_at INTEGER);
CREATE TABLE session (token TEXT PRIMARY KEY, who TEXT, role TEXT, expires INTEGER);
CREATE TABLE activity (
  id TEXT PRIMARY KEY, title TEXT DEFAULT '', intent TEXT DEFAULT '',
  plan TEXT DEFAULT '', plan_hash TEXT DEFAULT '', selection TEXT DEFAULT '',
  status TEXT DEFAULT 'draft', vend_until REAL DEFAULT 0, session_minutes INTEGER DEFAULT 60,
  created REAL DEFAULT 0, released REAL DEFAULT 0);
CREATE TABLE activity_code (code TEXT PRIMARY KEY, activity TEXT NOT NULL,
                            plan_hash TEXT NOT NULL,          -- retired in v1, still demanded
                            issued REAL, valid_until REAL, used INTEGER DEFAULT 0);
CREATE TABLE activity_submission (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, receipt TEXT NOT NULL UNIQUE,
  activity TEXT NOT NULL, plan_hash TEXT NOT NULL,            -- retired in v1, still demanded
  artifact_hash TEXT, ts REAL, started REAL, finished REAL, verdict TEXT, data TEXT);
CREATE TABLE enrolment (id TEXT PRIMARY KEY, name TEXT, token TEXT);   -- retired in v1
CREATE TABLE message (id TEXT PRIMARY KEY, body TEXT);                 -- retired in v1
CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT);
"""


@pytest.fixture
def v0_root(tmp_path):
    """A course root holding a genuine v0 database with a teacher's work already in it."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    db = sqlite3.connect(data / "gini.db")
    db.executescript(V0)
    db.execute("INSERT INTO account(username, role, salt, hash, claimed_at) VALUES(?,?,?,?,?)",
               ("mahesh", "teacher", "aa", "bb", 1))
    db.execute("INSERT INTO activity(id, title, plan_hash, status, vend_until, session_minutes,"
               " created, released) VALUES(?,?,?,?,?,?,?,?)",
               ("comp535/lab1", "Multi-LAN", "abc123", "released", 2_000_000_000.0, 90,
                1_000_000.0, 1_000_001.0))
    db.execute("INSERT INTO activity_code(code, activity, plan_hash, issued, valid_until, used)"
               " VALUES(?,?,?,?,?,?)", ("OLDCODE1", "comp535/lab1", "abc123", 1.0, 2.0, 1))
    db.execute("INSERT INTO activity_submission(code, receipt, activity, plan_hash,"
               " artifact_hash, ts, started, finished, verdict, data)"
               " VALUES(?,?,?,?,?,?,?,?,?,?)",
               ("OLDCODE1", "OLD-RCPT", "comp535/lab1", "abc123", "deadbeef", 3.0, 1.0, 2.0,
                "pass", "{}"))
    db.execute("INSERT INTO enrolment(id, name, token) VALUES('ravi','Ravi','T-ravi')")
    db.commit()
    db.close()
    Store._instances.clear()
    return tmp_path


def test_opening_a_v0_database_adds_the_missing_columns(v0_root):
    """THE bug: `table activity has no column named brief`."""
    store = Store(str(v0_root))
    cols = {r["name"] for r in store._all("PRAGMA table_info(activity)")}
    assert {"course", "lab", "brief"} <= cols


def test_saving_a_lab_works_on_a_migrated_database(v0_root):
    """The failure a teacher actually saw, reproduced end to end."""
    store = Store(str(v0_root))
    store.activity_put({"id": "comp535/lab2", "course": "comp535", "lab": "lab2",
                        "title": "New one", "brief": "Do the thing.", "status": "draft",
                        "vend_until": 0, "session_minutes": 60, "created": time.time(),
                        "released": 0})
    assert store.activity("comp535/lab2")["brief"] == "Do the thing."


def test_an_existing_lab_keeps_its_deadline_and_its_release(v0_root):
    """A migration that silently reset a released lab's deadline would reopen a closed lab."""
    row = Store(str(v0_root)).activity("comp535/lab1")
    assert row["status"] == "released"
    assert row["vend_until"] == 2_000_000_000.0
    assert row["session_minutes"] == 90


def test_an_existing_lab_becomes_visible_in_its_course(v0_root):
    """v0 stored the course inside the id only. Without a backfill the v1 queries filter on an
    empty column, and a teacher's existing labs vanish from their own course."""
    store = Store(str(v0_root))
    rows = store.activities("comp535")
    assert [r["id"] for r in rows] == ["comp535/lab1"]
    assert rows[0]["lab"] == "lab1"


def test_the_course_row_is_created_so_the_course_can_be_staffed(v0_root):
    """v0 had no course table at all. Without a row, the course cannot be listed, staffed, or
    opened — the labs would exist but be unreachable."""
    store = Store(str(v0_root))
    assert store.course("comp535") is not None
    store.add_staff("comp535", "mahesh")
    assert store.staffs("comp535", "mahesh", "teacher")


def test_existing_submissions_survive_and_stay_findable(v0_root):
    """Proof of activity is the point of the system; losing a submission to a schema change would
    be the worst possible migration bug."""
    store = Store(str(v0_root))
    assert store.submission_by_receipt("OLD-RCPT")["activity"] == "comp535/lab1"
    assert store.artifact_twins("deadbeef") != []


def test_staff_accounts_still_sign_in_after_the_migration(v0_root):
    import accounts as A
    acc = A.Accounts(str(v0_root))
    assert acc.store.account("mahesh")["role"] == "teacher"


def test_retired_v0_tables_are_left_alone(v0_root):
    """Additive only. Dropping `enrolment` would destroy a teacher's archive to save a few KB, and
    v1 simply never reads it."""
    store = Store(str(v0_root))
    tables = {r["name"] for r in
              store._all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "enrolment" in tables


def test_the_migration_is_idempotent(v0_root):
    """Opening twice must not fail on a duplicate column."""
    Store(str(v0_root))
    Store._instances.clear()
    Store(str(v0_root))          # would raise sqlite3.OperationalError if ALTER ran twice
    Store._instances.clear()
    assert Store(str(v0_root)).activity("comp535/lab1") is not None


def test_a_fresh_database_is_unaffected(tmp_path):
    """The migration must not disturb the case every other test covers."""
    Store._instances.clear()
    store = Store(str(tmp_path))
    cols = {r["name"] for r in store._all("PRAGMA table_info(activity)")}
    assert {"course", "lab", "brief", "status"} <= cols
    assert store.activities() == []


# -- retired NOT NULL columns ------------------------------------------------- #
def test_vending_a_code_works_though_v0_demanded_a_plan_hash(v0_root):
    """v0 declared `activity_code.plan_hash NOT NULL`. v1 has no plan to name, so every vend failed
    on a constraint — and adding columns cannot fix a constraint."""
    import activities as ACT
    store = Store(str(v0_root))
    act = store.activity("comp535/lab1")
    store.code_put(ACT.mint_code(act))          # raised IntegrityError before the rebuild


def test_a_submission_works_though_v0_demanded_a_plan_hash(v0_root):
    """The one that would have bitten at deadline time, with a class waiting to hand in."""
    store = Store(str(v0_root))
    assert store.submission_put({
        "code": "NEWCODE1", "receipt": "NEW-RCPT", "activity": "comp535/lab1",
        "artifact_hash": "cafe", "ts": 1.0, "started": 1.0, "finished": 2.0,
        "verdict": "pass", "data": "{}"}) is True


def test_the_rebuild_keeps_every_existing_row(v0_root):
    """A rebuild that lost a submission would be a far worse bug than the one it fixes."""
    store = Store(str(v0_root))
    sub = store.submission_by_receipt("OLD-RCPT")
    assert sub is not None and sub["code"] == "OLDCODE1"
    assert sub["artifact_hash"] == "deadbeef" and sub["verdict"] == "pass"
    assert store.code("OLDCODE1")["activity"] == "comp535/lab1"


def test_the_rebuild_preserves_the_retired_data_rather_than_dropping_it(v0_root):
    """Relaxed, not deleted. The AOP work is shelved for v2, not abandoned, so the old plan_hash
    values are carried across as a nullable column."""
    store = Store(str(v0_root))
    row = store._one("SELECT plan_hash FROM activity_submission WHERE receipt='OLD-RCPT'")
    assert row["plan_hash"] == "abc123"


def test_the_uniqueness_guarantees_survive_the_rebuild(v0_root):
    """These constraints ARE the anti-duplicate design; a rebuild that quietly dropped them would
    leave the system looking fine and silently accepting the same work twice."""
    store = Store(str(v0_root))
    rec = {"code": "C1", "receipt": "R1", "activity": "comp535/lab1", "artifact_hash": "x",
           "ts": 1.0, "started": 1.0, "finished": 2.0, "verdict": "pass", "data": "{}"}
    assert store.submission_put(rec) is True
    assert store.submission_put(dict(rec, code="C2")) is False      # receipt still unique
    assert store.submission_put(dict(rec, receipt="R2")) is False   # code still unique


def test_the_indexes_come_back_after_a_rebuild(v0_root):
    """DROP TABLE takes its indexes with it. They must be recreated, or every lookup degrades to a
    scan and nothing tells you."""
    store = Store(str(v0_root))
    names = {r["name"] for r in
             store._all("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_activity_sub_artifact", "ix_activity_code_act", "ix_activity_course"} <= names


def test_a_second_open_does_not_rebuild_again(v0_root):
    """Once relaxed, there is nothing left to relax — and a rebuild on every boot would be a slow
    surprise on a large database."""
    Store(str(v0_root))
    Store._instances.clear()
    store = Store(str(v0_root))
    tables = {r["name"] for r in
              store._all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.endswith("__migrating") for t in tables)
    assert store.submission_by_receipt("OLD-RCPT") is not None

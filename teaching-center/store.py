"""The Teaching Center's system of record — SQLite, behind one small data-access layer.

Why this exists: a flat-file store corrupts under concurrent writes. The server is threaded, so two
submissions landing at the same instant can interleave and truncate a file. For class data that
"cannot be lost" that is the real risk, and SQLite closes it: atomic transactions, durable WAL
writes, no torn files. It ships with Python, so there is nothing to deploy.

Everything goes through `Store`. The method surface is deliberately storage-shaped (get/put/list),
not SQL-shaped, so moving to Postgres later is a swap of this one file.

Concurrency: one connection, WAL mode, guarded by a re-entrant lock. Classroom scale does not need
a pool, and a single guarded connection is the simplest thing that is provably correct.

**v1 scope.** Staff, courses, activities (labs), and course materials. No lessons, no roster, no
messages, no AI — see TEACHING_CENTER_V1_SPEC.md. The previous, larger schema is in git history if
v2 needs it back.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
-- Staff only. `admin` is the portal owner (the initial password); `teacher` runs courses.
-- There is deliberately NO student account table: a student never signs in. A vended activity code
-- is the entire interaction, which is what keeps the portal from ever learning who did the work.
CREATE TABLE IF NOT EXISTS account (
  username   TEXT PRIMARY KEY,
  role       TEXT DEFAULT 'teacher',        -- admin | teacher
  salt       TEXT, hash TEXT, n INTEGER, r INTEGER, p INTEGER,
  claimed_at INTEGER
);
CREATE TABLE IF NOT EXISTS session (
  token   TEXT PRIMARY KEY,
  who     TEXT, role TEXT, expires INTEGER
);

CREATE TABLE IF NOT EXISTS course (
  id       TEXT PRIMARY KEY,                -- "comp535"
  title    TEXT DEFAULT '',
  created  REAL DEFAULT 0,
  archived INTEGER DEFAULT 0
);
-- Which teachers run which course. An admin sees everything; a teacher sees only their own, so a
-- shared portal does not become a shared filing cabinet.
CREATE TABLE IF NOT EXISTS course_staff (
  course   TEXT NOT NULL,
  username TEXT NOT NULL,
  PRIMARY KEY (course, username)
);

-- A lab. No plan and no plan_hash in v1: gBuilder records what the student DID and the report
-- narrates it, rather than scoring it against expectations.
CREATE TABLE IF NOT EXISTS activity (
  id              TEXT PRIMARY KEY,         -- "<course>/<lab>"
  course          TEXT NOT NULL,
  lab             TEXT NOT NULL,
  title           TEXT DEFAULT '',
  brief           TEXT DEFAULT '',          -- what the student is told, plain prose
  status          TEXT DEFAULT 'draft',     -- draft | released
  vend_until      REAL DEFAULT 0,
  session_minutes INTEGER DEFAULT 60,
  created         REAL DEFAULT 0,
  released        REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS activity_code (
  code        TEXT PRIMARY KEY,
  activity    TEXT NOT NULL,
  issued      REAL DEFAULT 0,
  valid_until REAL DEFAULT 0,   -- absolute: vend_until + session, so hoarding gains nothing
  used        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS activity_submission (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  code          TEXT NOT NULL UNIQUE,   -- one code, one submission
  receipt       TEXT NOT NULL UNIQUE,   -- the same proof cannot be handed in twice
  activity      TEXT NOT NULL,
  artifact_hash TEXT DEFAULT '',        -- indexed; collisions FLAG for review, never reject
  ts            REAL DEFAULT 0,
  started       REAL DEFAULT 0,         -- from the chain, for the session-window check
  finished      REAL DEFAULT 0,
  verdict       TEXT DEFAULT '',        -- integrity of the proof, NOT quality of the work
  data          TEXT DEFAULT '',        -- proof + artifact + narration
  student_id    TEXT DEFAULT '',        -- who CLAIMED it; empty until they do
  claimed_at    REAL DEFAULT 0
);

-- Course materials: notes, handouts, links. Files live on disk under COURSE_ROOT/materials/;
-- only the metadata is here, so the database stays small and a file can be served directly.
CREATE TABLE IF NOT EXISTS material (
  id       TEXT PRIMARY KEY,
  course   TEXT NOT NULL,
  kind     TEXT DEFAULT 'file',           -- file | link
  title    TEXT DEFAULT '',
  filename TEXT DEFAULT '',               -- kind=file
  url      TEXT DEFAULT '',               -- kind=link
  size     INTEGER DEFAULT 0,
  uploaded REAL DEFAULT 0
);

-- Every attempt to claim a receipt, including the refused ones. Refused attempts are the WHOLE
-- point: a second claim is turned away, but the teacher must still learn who made it, or "escalate
-- to the students" is impossible and the refusal is just a dead end for one of them.
CREATE TABLE IF NOT EXISTS claim_attempt (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt    TEXT NOT NULL,
  student_id TEXT NOT NULL,
  ts         REAL DEFAULT 0,
  outcome    TEXT DEFAULT ''          -- claimed | already_claimed | no_such_receipt
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

_INDEXES = """
-- Created AFTER the column migration: an index over a column a v0 table does not have
-- yet fails outright, and it fails during startup, before anything can report why.
CREATE INDEX IF NOT EXISTS ix_activity_course ON activity(course, lab);
CREATE INDEX IF NOT EXISTS ix_activity_code_act ON activity_code(activity);
CREATE INDEX IF NOT EXISTS ix_activity_sub_act ON activity_submission(activity);
CREATE INDEX IF NOT EXISTS ix_activity_sub_artifact ON activity_submission(artifact_hash);
CREATE INDEX IF NOT EXISTS ix_material_course ON material(course, uploaded);
CREATE INDEX IF NOT EXISTS ix_claim_receipt ON claim_attempt(receipt, ts);
"""


def _canonical_ddl(table: str) -> str:
    """The column block of `table` as `_SCHEMA` declares it.

    Read from the schema rather than restated in Python, so a rebuild cannot drift from the real
    definition — a migration that quietly builds a slightly different table is worse than one that
    fails.
    """
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", _SCHEMA, re.S)
    if not m:
        raise KeyError(f"no canonical schema for {table}")
    lines = []
    for line in m.group(1).splitlines():
        line = re.sub(r"\s*--.*$", "", line).rstrip()      # comments confuse nothing, but shorten
        if line.strip():
            lines.append(line.rstrip(","))
    return ",\n".join(lines)


def _canonical_columns() -> dict[str, list[str]]:
    """Table -> the column names v1 owns, in declaration order."""
    out: dict[str, list[str]] = {}
    for table, block in re.findall(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", _SCHEMA, re.S):
        cols = []
        for line in block.splitlines():
            line = re.sub(r"\s*--.*$", "", line).strip()
            if not line or line.upper().startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY")):
                continue
            for part in _split_columns(line):
                name = part.strip().split()[0]
                if name.upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
                    cols.append(name)
        out[table] = cols
    return out


def _split_columns(line: str) -> list[str]:
    """One schema line may declare several columns (`salt TEXT, hash TEXT, n INTEGER,`)."""
    return [p for p in line.rstrip(",").split(",") if p.strip()]


class Store:
    """One store per COURSE_ROOT, keyed so repeated construction returns the same connection."""

    _instances: dict = {}
    _guard = threading.Lock()

    def __new__(cls, root):
        key = str(Path(root).resolve())
        with cls._guard:
            inst = cls._instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                inst._init(key)
                cls._instances[key] = inst
            return inst

    def _init(self, key: str) -> None:
        self.lock = threading.RLock()
        self.root = Path(key)
        data = self.root / "data"
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / "gini.db"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")     # concurrent readers + durable writes
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)     # tables first...
        self._migrate()                    # ...then reconcile an older database's columns...
        self.db.executescript(_INDEXES)    # ...and only then index them
        self.db.commit()

    def _migrate(self) -> None:
        """Bring a database created by an older version up to the current schema.

        `CREATE TABLE IF NOT EXISTS` does exactly nothing to a table that already exists, so a v0
        database keeps its v0 columns and the first write to a new one dies with
        `table activity has no column named brief`. Every real installation has an existing
        database — a fresh temp directory is the ONE case where this cannot go wrong, which is
        precisely why it went unnoticed.

        Additive only: columns are added, never dropped or retyped, so a downgrade still reads and
        nothing a teacher already has is thrown away. Retired v0 tables are left in place for the
        same reason; they cost a few KB and they are somebody's archive.
        """
        want = {                       # column -> DDL type, per table the schema owns
            "account": {"role": "TEXT DEFAULT 'teacher'", "salt": "TEXT", "hash": "TEXT",
                        "n": "INTEGER", "r": "INTEGER", "p": "INTEGER", "claimed_at": "INTEGER"},
            "session": {"who": "TEXT", "role": "TEXT", "expires": "INTEGER"},
            "course": {"title": "TEXT DEFAULT ''", "created": "REAL DEFAULT 0",
                       "archived": "INTEGER DEFAULT 0"},
            "activity": {"course": "TEXT DEFAULT ''", "lab": "TEXT DEFAULT ''",
                         "title": "TEXT DEFAULT ''", "brief": "TEXT DEFAULT ''",
                         "status": "TEXT DEFAULT 'draft'", "vend_until": "REAL DEFAULT 0",
                         "session_minutes": "INTEGER DEFAULT 60", "created": "REAL DEFAULT 0",
                         "released": "REAL DEFAULT 0"},
            "activity_code": {"activity": "TEXT DEFAULT ''", "issued": "REAL DEFAULT 0",
                              "valid_until": "REAL DEFAULT 0", "used": "INTEGER DEFAULT 0"},
            "activity_submission": {"code": "TEXT DEFAULT ''", "receipt": "TEXT DEFAULT ''",
                                    "activity": "TEXT DEFAULT ''",
                                    "artifact_hash": "TEXT DEFAULT ''", "ts": "REAL DEFAULT 0",
                                    "started": "REAL DEFAULT 0", "finished": "REAL DEFAULT 0",
                                    "verdict": "TEXT DEFAULT ''", "data": "TEXT DEFAULT ''",
                                    "student_id": "TEXT DEFAULT ''",
                                    "claimed_at": "REAL DEFAULT 0"},
            "material": {"course": "TEXT DEFAULT ''", "kind": "TEXT DEFAULT 'file'",
                         "title": "TEXT DEFAULT ''", "filename": "TEXT DEFAULT ''",
                         "url": "TEXT DEFAULT ''", "size": "INTEGER DEFAULT 0",
                         "uploaded": "REAL DEFAULT 0"},
        }
        for table, cols in want.items():
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if not have:
                continue                                   # the schema above just created it
            for name, ddl in cols.items():
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

        self._relax_retired_columns()

        # v0 stored the course on the activity id only ("comp535/lab1"). Backfill the columns the
        # v1 queries filter on, or a teacher's existing labs are invisible in their own course.
        for row in self.db.execute(
                "SELECT id FROM activity WHERE course IS NULL OR course=''").fetchall():
            aid = row["id"]
            if "/" in aid:
                course, _, lab = aid.partition("/")
                self.db.execute("UPDATE activity SET course=?, lab=? WHERE id=?",
                                (course, lab, aid))

        # A course row must exist for a course to be listed or staffed at all.
        for row in self.db.execute("SELECT DISTINCT course FROM activity WHERE course<>''"):
            self.db.execute("INSERT OR IGNORE INTO course(id, title, created) VALUES(?, ?, ?)",
                            (row["course"], row["course"], time.time()))

    def _relax_retired_columns(self) -> None:
        """Rebuild any table whose OLD schema demands a column v1 no longer writes.

        Adding columns cannot fix this one. v0 declared `activity_code.plan_hash NOT NULL`, and v1
        has no plan to name — so vending a code fails on a constraint, and
        `activity_submission.plan_hash` would fail the same way on every student submission, at
        deadline time, with a class waiting.

        Nothing is lost: the table is rebuilt to the v1 shape, retired columns are carried over as
        NULLABLE rather than dropped, and every row is copied. So this relaxes a constraint, it
        does not discard the AOP data that v2 may still want.
        """
        for table, canonical in _canonical_columns().items():
            info = list(self.db.execute(f"PRAGMA table_info({table})"))
            if not info:
                continue
            blocking = [r["name"] for r in info
                        if r["notnull"] and r["dflt_value"] is None and not r["pk"]
                        and r["name"] not in canonical]
            if not blocking:
                continue
            legacy = [(r["name"], r["type"] or "TEXT") for r in info
                      if r["name"] not in canonical]
            keep = [c for c in canonical if c in {r["name"] for r in info}]
            cols = keep + [n for n, _ in legacy]
            ddl = _canonical_ddl(table)
            extra = "".join(f",\n  {n} {t}" for n, t in legacy)   # carried over, now nullable
            tmp = f"{table}__migrating"
            self.db.execute(f"DROP TABLE IF EXISTS {tmp}")
            self.db.execute(f"CREATE TABLE {tmp} (\n{ddl}{extra}\n)")
            names = ",".join(cols)
            self.db.execute(f"INSERT INTO {tmp}({names}) SELECT {names} FROM {table}")
            self.db.execute(f"DROP TABLE {table}")                # takes its indexes with it...
            self.db.execute(f"ALTER TABLE {tmp} RENAME TO {table}")   # ..._INDEXES rebuilds them

    # -- low-level -------------------------------------------------------- #
    def _all(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args=()) -> dict | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def _run(self, sql: str, args=()) -> None:
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    # -- accounts --------------------------------------------------------- #
    def account(self, username: str) -> dict | None:
        return self._one("SELECT * FROM account WHERE username=?", (username,))

    def accounts(self) -> list[dict]:
        return self._all("SELECT username, role, claimed_at FROM account ORDER BY role, username")

    def put_account(self, username: str, **f) -> None:
        cols = ("role", "salt", "hash", "n", "r", "p", "claimed_at")
        self._run(f"INSERT OR REPLACE INTO account(username,{','.join(cols)}) "
                  f"VALUES(?,{','.join('?' * len(cols))})",
                  (username, *(f.get(c) for c in cols)))

    def delete_account(self, username: str) -> None:
        self._run("DELETE FROM account WHERE username=?", (username,))
        self._run("DELETE FROM session WHERE who=?", (username,))
        self._run("DELETE FROM course_staff WHERE username=?", (username,))

    # -- sessions --------------------------------------------------------- #
    def put_session(self, token: str, who: str, role: str, expires: int) -> None:
        self._run("INSERT OR REPLACE INTO session(token,who,role,expires) VALUES(?,?,?,?)",
                  (token, who, role, expires))

    def session(self, token: str) -> dict | None:
        return self._one("SELECT * FROM session WHERE token=?", (token,))

    def drop_session(self, token: str) -> None:
        self._run("DELETE FROM session WHERE token=?", (token,))

    # -- courses ---------------------------------------------------------- #
    def put_course(self, rec: dict) -> None:
        self._run("INSERT OR REPLACE INTO course(id,title,created,archived) VALUES(?,?,?,?)",
                  (rec["id"], rec.get("title", ""), rec.get("created") or time.time(),
                   int(rec.get("archived", 0))))

    def course(self, cid: str) -> dict | None:
        return self._one("SELECT * FROM course WHERE id=?", (cid,))

    def courses(self, username: str = "", role: str = "") -> list[dict]:
        """An admin sees every course; a teacher sees only the ones they staff."""
        if role == "admin" or not username:
            return self._all("SELECT * FROM course ORDER BY archived, id")
        return self._all(
            "SELECT c.* FROM course c JOIN course_staff s ON s.course = c.id "
            "WHERE s.username=? ORDER BY c.archived, c.id", (username,))

    def add_staff(self, course: str, username: str) -> None:
        self._run("INSERT OR REPLACE INTO course_staff(course,username) VALUES(?,?)",
                  (course, username))

    def remove_staff(self, course: str, username: str) -> None:
        self._run("DELETE FROM course_staff WHERE course=? AND username=?", (course, username))

    def course_staff(self, course: str) -> list[str]:
        return [r["username"] for r in
                self._all("SELECT username FROM course_staff WHERE course=? ORDER BY username",
                          (course,))]

    def staffs(self, course: str, username: str, role: str = "") -> bool:
        if role == "admin":
            return True
        return self._one("SELECT 1 FROM course_staff WHERE course=? AND username=?",
                         (course, username)) is not None

    # -- activities ------------------------------------------------------- #
    def activity_put(self, rec: dict) -> None:
        cols = ("id", "course", "lab", "title", "brief", "status", "vend_until",
                "session_minutes", "created", "released")
        self._run(f"INSERT OR REPLACE INTO activity({','.join(cols)}) "
                  f"VALUES({','.join('?' * len(cols))})", tuple(rec.get(c, "") for c in cols))

    def activity(self, activity_id: str) -> dict | None:
        return self._one("SELECT * FROM activity WHERE id=?", (activity_id,))

    def activities(self, course: str = "") -> list[dict]:
        if course:
            return self._all("SELECT * FROM activity WHERE course=? ORDER BY lab", (course,))
        return self._all("SELECT * FROM activity ORDER BY course, lab")

    def activity_delete(self, activity_id: str) -> None:
        self._run("DELETE FROM activity WHERE id=?", (activity_id,))

    def code_put(self, rec: dict) -> None:
        self._run("INSERT OR REPLACE INTO activity_code(code,activity,issued,valid_until,used) "
                  "VALUES(?,?,?,?,?)",
                  (rec["code"], rec["activity"], rec.get("issued", 0.0),
                   rec.get("valid_until", 0.0), int(rec.get("used", 0))))

    def code(self, code: str) -> dict | None:
        return self._one("SELECT * FROM activity_code WHERE code=?", (code,))

    def code_mark_used(self, code: str) -> None:
        self._run("UPDATE activity_code SET used=1 WHERE code=?", (code,))

    def codes_delete_for(self, activity_id: str) -> int:
        """Drop the codes minted for an activity. Only meaningful when the activity itself goes:
        an orphaned code already refuses with "no activity here", but leaving thousands of dead
        rows behind makes the vended count of a re-created lab a lie."""
        with self.lock:
            n = self.db.execute("DELETE FROM activity_code WHERE activity=?",
                                (activity_id,)).rowcount
            self.db.commit()
            return n

    def codes_for(self, activity_id: str) -> list[dict]:
        return self._all("SELECT * FROM activity_code WHERE activity=? ORDER BY issued",
                         (activity_id,))

    def submission_put(self, rec: dict) -> bool:
        """Insert a submission. False when the code or receipt is already present.

        Uniqueness is the SCHEMA's, not a prior read's: two submissions racing would both pass a
        check-then-insert, and the loser must be rejected rather than silently overwriting.
        """
        cols = ("code", "receipt", "activity", "artifact_hash",
                "ts", "started", "finished", "verdict", "data")
        try:
            self._run(f"INSERT INTO activity_submission({','.join(cols)}) "
                      f"VALUES({','.join('?' * len(cols))})", tuple(rec.get(c, "") for c in cols))
            return True
        except sqlite3.IntegrityError:
            return False

    def submission_by_receipt(self, receipt: str) -> dict | None:
        return self._one("SELECT * FROM activity_submission WHERE receipt=?", (receipt,))

    def submission_by_code(self, code: str) -> dict | None:
        return self._one("SELECT * FROM activity_submission WHERE code=?", (code,))

    def activity_submissions(self, activity_id: str) -> list[dict]:
        return self._all("SELECT * FROM activity_submission WHERE activity=? ORDER BY ts DESC",
                         (activity_id,))

    def course_submissions(self, course: str) -> list[dict]:
        return self._all(
            "SELECT s.* FROM activity_submission s JOIN activity a ON a.id = s.activity "
            "WHERE a.course=? ORDER BY s.ts DESC", (course,))

    def claim(self, receipt: str, student_id: str, now: float) -> tuple[bool, str]:
        """Bind a student id to a submission. Returns (accepted, outcome).

        Every attempt is recorded before anything is decided, so a refusal still leaves the teacher
        able to see who tried. Done under one lock: two students claiming the same receipt at the
        same instant must not both win.
        """
        with self.lock:
            row = self._one("SELECT * FROM activity_submission WHERE receipt=?", (receipt,))
            if row is None:
                outcome = "no_such_receipt"
            elif (row.get("student_id") or "").strip():
                outcome = "already_claimed"
            else:
                self.db.execute(
                    "UPDATE activity_submission SET student_id=?, claimed_at=? WHERE receipt=?",
                    (student_id, now, receipt))
                outcome = "claimed"
            self.db.execute(
                "INSERT INTO claim_attempt(receipt, student_id, ts, outcome) VALUES(?,?,?,?)",
                (receipt, student_id, now, outcome))
            self.db.commit()
            return outcome == "claimed", outcome

    def claim_attempts(self, receipt: str) -> list[dict]:
        return self._all("SELECT * FROM claim_attempt WHERE receipt=? ORDER BY ts", (receipt,))

    def unclaimed(self, before: float = 0.0) -> list[dict]:
        """Submissions nobody has claimed. The retention policy the student page describes."""
        sql = "SELECT * FROM activity_submission WHERE student_id='' OR student_id IS NULL"
        if before:
            return self._all(sql + " AND ts < ? ORDER BY ts", (before,))
        return self._all(sql + " ORDER BY ts")

    def artifact_twins(self, artifact_hash: str, exclude_code: str = "") -> list[dict]:
        """Other submissions built from the same topology — the collusion signal a receipt cannot
        see, because two students doing identical work under their own codes produce different
        receipts. Flags for review; never rejects, since a shared starter topology is legitimate."""
        if not artifact_hash:
            return []
        return self._all(
            "SELECT code,receipt,activity,ts FROM activity_submission "
            "WHERE artifact_hash=? AND code<>? ORDER BY ts", (artifact_hash, exclude_code))

    # -- materials -------------------------------------------------------- #
    def material_put(self, rec: dict) -> None:
        cols = ("id", "course", "kind", "title", "filename", "url", "size", "uploaded")
        self._run(f"INSERT OR REPLACE INTO material({','.join(cols)}) "
                  f"VALUES({','.join('?' * len(cols))})", tuple(rec.get(c, "") for c in cols))

    def material(self, mid: str) -> dict | None:
        return self._one("SELECT * FROM material WHERE id=?", (mid,))

    def materials(self, course: str) -> list[dict]:
        return self._all("SELECT * FROM material WHERE course=? ORDER BY uploaded DESC", (course,))

    def material_delete(self, mid: str) -> None:
        self._run("DELETE FROM material WHERE id=?", (mid,))

    # -- kv --------------------------------------------------------------- #
    def kv_get(self, k: str) -> dict | None:
        r = self._one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else None

    def kv_put(self, k: str, v: dict) -> None:
        self._run("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))

    def kv_delete(self, k: str) -> None:
        self._run("DELETE FROM kv WHERE k=?", (k,))

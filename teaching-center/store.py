"""The Teaching Center's system of record — SQLite, behind one small data-access layer.

Why this exists: the old flat-file store (roster.json, messages.jsonl, …) corrupts under concurrent
writes. The server is threaded, so two students submitting at the same instant — or a heartbeat
landing mid-write — can interleave and truncate a file. For "cannot be lost" class data that is the
real risk, and SQLite closes it: atomic transactions, durable WAL writes, no torn files. It ships
with Python, so there is nothing to deploy.

Everything goes through `Store`. It's the seam: the method surface here is deliberately storage-shaped
(get/put/list), not SQL-shaped, so a move to Postgres later is a swap of this one file, not a rewrite
of accounts/social/teacher.

Concurrency: one connection, WAL mode, guarded by a re-entrant lock. Classroom scale doesn't need a
pool, and a single guarded connection is the simplest thing that is provably correct under threads.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrolment (
  username   TEXT PRIMARY KEY,
  name       TEXT DEFAULT '',
  sis_id     TEXT DEFAULT '',
  token      TEXT DEFAULT '',
  grp        TEXT DEFAULT '',
  ai_hosted  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS account (
  username   TEXT PRIMARY KEY,
  role       TEXT DEFAULT 'student',
  salt       TEXT, hash TEXT, n INTEGER, r INTEGER, p INTEGER,
  claimed_at INTEGER,
  photo      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS session (
  token   TEXT PRIMARY KEY,
  who     TEXT, role TEXT, expires INTEGER
);
CREATE TABLE IF NOT EXISTS profile (
  student TEXT PRIMARY KEY,
  data    TEXT
);
CREATE TABLE IF NOT EXISTS submission (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  student   TEXT, lesson_id TEXT, ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS message (
  id         TEXT PRIMARY KEY,
  ts         REAL,
  channel    TEXT, chan_kind TEXT,
  sender     TEXT, author TEXT, recipient TEXT,
  kind       TEXT, persona_version TEXT,
  body       TEXT,
  deleted    INTEGER DEFAULT 0,
  read_by    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_message_channel ON message(channel, ts);
CREATE TABLE IF NOT EXISTS presence (
  who      TEXT PRIMARY KEY,
  ts       REAL, progress TEXT
);
CREATE TABLE IF NOT EXISTS report (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       REAL, by_who TEXT, note TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS review (
  message_id      TEXT PRIMARY KEY,
  ts              REAL, student TEXT, question TEXT, answer TEXT,
  kind            TEXT, persona_version TEXT,
  escalate        INTEGER, reviewed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY, v TEXT
);
"""


class Store:
    _instances: dict[str, "Store"] = {}
    _instances_lock = threading.Lock()

    def __new__(cls, root):
        """One Store per course root (one SQLite connection per DB file). Multiple Course/Social/
        Accounts objects over the same root must share the connection, or WAL gives them stale reads
        of each other's writes."""
        key = str(Path(root).resolve())
        with cls._instances_lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                inst._init(key)
                cls._instances[key] = inst
            return inst

    def _init(self, key: str) -> None:
        self.lock = threading.RLock()
        data = Path(key) / "data"
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / "gini.db"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")     # concurrent readers + durable writes
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self.db.commit()

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

    # -- enrolment (the roster) ------------------------------------------- #
    def roster(self) -> list[dict]:
        rows = self._all("SELECT * FROM enrolment ORDER BY username")
        for r in rows:
            r["id"] = r.pop("username")
            r["group"] = r.pop("grp")
            r["ai_hosted"] = bool(r["ai_hosted"])
        return rows

    def enrolment(self, username: str) -> dict | None:
        r = self._one("SELECT * FROM enrolment WHERE username=?", (username,))
        if r is None:
            return None
        r["id"] = r.pop("username")
        r["group"] = r.pop("grp")
        r["ai_hosted"] = bool(r["ai_hosted"])
        return r

    def upsert_enrolment(self, username, *, name, sis_id, token, group, ai_hosted) -> None:
        self._run(
            "INSERT INTO enrolment(username,name,sis_id,token,grp,ai_hosted) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET name=excluded.name, sis_id=excluded.sis_id, "
            "token=excluded.token, grp=excluded.grp, ai_hosted=excluded.ai_hosted",
            (username, name, sis_id, token, group, 1 if ai_hosted else 0))

    def delete_enrolment(self, username: str) -> None:
        self._run("DELETE FROM enrolment WHERE username=?", (username,))

    def set_field(self, username: str, field: str, value) -> None:
        col = {"group": "grp", "ai_hosted": "ai_hosted", "name": "name", "sis_id": "sis_id"}[field]
        if col == "ai_hosted":
            value = 1 if value else 0
        self._run(f"UPDATE enrolment SET {col}=? WHERE username=?", (value, username))

    # -- accounts --------------------------------------------------------- #
    def account(self, username: str) -> dict | None:
        return self._one("SELECT * FROM account WHERE username=?", (username,))

    def accounts(self) -> dict:
        return {r["username"]: r for r in self._all("SELECT * FROM account")}

    def put_account(self, username, *, role, salt, hash, n, r, p, claimed_at) -> None:
        self._run(
            "INSERT INTO account(username,role,salt,hash,n,r,p,claimed_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET role=excluded.role, salt=excluded.salt, "
            "hash=excluded.hash, n=excluded.n, r=excluded.r, p=excluded.p, "
            "claimed_at=excluded.claimed_at",
            (username, role, salt, hash, n, r, p, claimed_at))

    def delete_account(self, username: str) -> None:
        self._run("DELETE FROM account WHERE username=?", (username,))

    def set_photo(self, username: str, photo: str) -> None:
        self._run("UPDATE account SET photo=? WHERE username=?", (photo, username))

    def photo(self, username: str) -> str:
        r = self.account(username)
        return (r or {}).get("photo", "") or ""

    # -- sessions --------------------------------------------------------- #
    def put_session(self, token, who, role, expires) -> None:
        self._run("INSERT OR REPLACE INTO session(token,who,role,expires) VALUES(?,?,?,?)",
                  (token, who, role, expires))

    def session(self, token: str) -> dict | None:
        return self._one("SELECT * FROM session WHERE token=?", (token,))

    def delete_session(self, token: str) -> None:
        self._run("DELETE FROM session WHERE token=?", (token,))

    def delete_sessions_of(self, who: str) -> None:
        self._run("DELETE FROM session WHERE who=?", (who,))

    def gc_sessions(self, now: float) -> None:
        self._run("DELETE FROM session WHERE expires < ?", (now,))

    # -- profiles --------------------------------------------------------- #
    def profile(self, student: str) -> dict | None:
        r = self._one("SELECT data FROM profile WHERE student=?", (student,))
        return json.loads(r["data"]) if r else None

    def put_profile(self, student: str, data: dict) -> None:
        self._run("INSERT OR REPLACE INTO profile(student,data) VALUES(?,?)",
                  (student, json.dumps(data)))

    # -- submissions ------------------------------------------------------ #
    def add_submission(self, rec: dict) -> None:
        self._run("INSERT INTO submission(student,lesson_id,ts,data) VALUES(?,?,?,?)",
                  (rec.get("student", ""), rec.get("lesson_id", ""), time.time(), json.dumps(rec)))

    def submissions(self) -> list[dict]:
        return [json.loads(r["data"]) for r in
                self._all("SELECT data FROM submission ORDER BY id")]

    # -- messages --------------------------------------------------------- #
    def add_message(self, m: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO message(id,ts,channel,chan_kind,sender,author,recipient,kind,"
            "persona_version,body,deleted,read_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["id"], m["ts"], m["channel"], m["chan_kind"], m.get("from", ""), m.get("author", ""),
             m.get("to", ""), m.get("kind", "human"), m.get("persona_version", ""),
             m.get("body", ""), int(m.get("deleted", 0)), m.get("read_by", "")))

    def _msg_row(self, r: dict) -> dict:
        return {"id": r["id"], "ts": r["ts"], "channel": r["channel"], "chan_kind": r["chan_kind"],
                "from": r["sender"], "author": r["author"], "to": r["recipient"], "kind": r["kind"],
                "persona_version": r["persona_version"], "body": r["body"],
                "deleted": bool(r["deleted"]), "read_by": r["read_by"] or ""}

    def messages(self, *, include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM message"
        if not include_deleted:
            sql += " WHERE deleted=0"
        sql += " ORDER BY ts"
        return [self._msg_row(r) for r in self._all(sql)]

    def message(self, mid: str) -> dict | None:
        r = self._one("SELECT * FROM message WHERE id=?", (mid,))
        return self._msg_row(r) if r else None

    def set_deleted(self, mid: str, deleted: bool) -> None:
        self._run("UPDATE message SET deleted=? WHERE id=?", (1 if deleted else 0, mid))

    def purge_deleted_before(self, ts: float) -> int:
        with self.lock:
            cur = self.db.execute("DELETE FROM message WHERE chan_kind='dm' AND ts < ?", (ts,))
            self.db.commit()
            return cur.rowcount

    # -- presence --------------------------------------------------------- #
    def put_presence(self, who: str, ts: float, progress: dict | None) -> None:
        cur = self._one("SELECT progress FROM presence WHERE who=?", (who,))
        prog = json.dumps(progress) if progress else (cur or {}).get("progress", "")
        self._run("INSERT OR REPLACE INTO presence(who,ts,progress) VALUES(?,?,?)",
                  (who, ts, prog or ""))

    def presence(self, who: str) -> dict:
        r = self._one("SELECT * FROM presence WHERE who=?", (who,))
        if not r:
            return {"ts": 0, "progress": {}}
        return {"ts": r["ts"], "progress": json.loads(r["progress"]) if r["progress"] else {}}

    # -- reports ---------------------------------------------------------- #
    def add_report(self, ts, by_who, note, message) -> None:
        self._run("INSERT INTO report(ts,by_who,note,message) VALUES(?,?,?,?)",
                  (ts, by_who, note, json.dumps(message)))

    def reports(self) -> list[dict]:
        return [{"ts": r["ts"], "by": r["by_who"], "note": r["note"],
                 "message": json.loads(r["message"])}
                for r in self._all("SELECT * FROM report ORDER BY id DESC")]

    # -- review queue ----------------------------------------------------- #
    def add_review(self, rec: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO review(message_id,ts,student,question,answer,kind,"
            "persona_version,escalate,reviewed) VALUES(?,?,?,?,?,?,?,?,?)",
            (rec["message_id"], rec["ts"], rec["student"], rec["question"], rec["answer"],
             rec.get("kind", ""), rec.get("persona_version", ""), int(rec.get("escalate", 0)),
             int(rec.get("reviewed", 0))))

    def review(self, only_unreviewed: bool = True) -> list[dict]:
        sql = "SELECT * FROM review"
        if only_unreviewed:
            sql += " WHERE reviewed=0"
        sql += " ORDER BY escalate DESC, ts DESC"
        out = []
        for r in self._all(sql):
            d = dict(r)
            d["escalate"] = bool(d["escalate"])
            d["reviewed"] = bool(d["reviewed"])
            out.append(d)
        return out

    def mark_reviewed(self, mid: str) -> None:
        self._run("UPDATE review SET reviewed=1 WHERE message_id=?", (mid,))

    # -- kv (persona, teacher_setup) -------------------------------------- #
    def kv_get(self, k: str) -> dict | None:
        r = self._one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else None

    def kv_put(self, k: str, v: dict) -> None:
        self._run("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))

    def kv_delete(self, k: str) -> None:
        self._run("DELETE FROM kv WHERE k=?", (k,))

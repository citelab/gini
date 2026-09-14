"""The Discord log inside the Teaching Center, and the reference that expires.

The privacy design the bot was built on says the log holds no way back to a message: an id fetches
the message and the message names its author. Replying from the console needs exactly that way
back, so the two are split by LIFETIME instead of being traded off — the ability to reply expires,
the record does not.

These tests are about that split holding.
"""
from __future__ import annotations

import tempfile
import time

import pytest

from gini_teaching_center.store import Store


@pytest.fixture
def store():
    return Store(tempfile.mkdtemp())


def _obs(store, at=1.0, who="abc", text="where do we send the receipt code", thread=""):
    return store.observe_put({"at": at, "channel": "help", "who": who, "kind": "question",
                              "text": text, "terms": "code receipt send", "thread": thread})


def test_pushing_the_same_message_twice_records_it_once_and_returns_the_same_row(store):
    """The bot pushes duplicates as a matter of course — a backfill overlapping the live feed — and
    the second push must still say WHICH row it is, or a message reference cannot be attached."""
    first = _obs(store)
    assert _obs(store) == first
    assert store.observation_counts()["total"] == 1


def test_the_log_itself_holds_no_way_back_to_discord(store):
    """The property the whole design rests on. Everything identifying lives in the separate,
    expiring table — never on the observation.

    Asserted as the WHOLE column set rather than as "no message_id", so that adding a column to this
    table is a decision somebody has to come here and make. A privacy guarantee that is a property
    of a schema needs the schema pinned; `source` was added deliberately and landed here first."""
    obs = store.observation(_obs(store))
    assert set(obs) == {"id", "at", "channel", "who", "kind", "text", "terms", "thread",
                        "answered", "source"}
    assert "message_id" not in obs and "channel_id" not in obs


def test_where_a_question_was_asked_travels_with_it(store):
    """`source` is load-bearing, not bookkeeping: a chat question counts toward every number a
    teacher sees, and none of its words may ever be quoted into a public channel."""
    pub = _obs(store, at=1.0, text="asked in public")
    assert store.observation(pub)["source"] == "discord", "the default is the public case"

    priv = store.observe_put({"at": 2.0, "channel": "chat", "who": "z", "kind": "question",
                              "text": "asked privately", "terms": "ask privat", "source": "chat"})
    assert store.observation(priv)["source"] == "chat"


def test_a_teacher_can_reply_to_something_recent(store):
    now = time.time()
    oid = _obs(store, at=now)
    store.obs_ref_put(oid, "chan-1", "msg-1", now + 30 * 86400)
    store.reply_queue(oid, "Send it on MyCourses.", "maheswar@cs.mcgill.ca", now)

    pending = store.replies_pending()
    assert len(pending) == 1
    assert (pending[0]["channel_id"], pending[0]["message_id"]) == ("chan-1", "msg-1")
    assert pending[0]["body"] == "Send it on MyCourses."


def test_the_ability_to_reply_expires_and_the_record_does_not(store):
    """Nobody replies to something from March. After the window the reference is swept, the
    observation stays, and it is as anonymous as everything older than it."""
    now = time.time()
    oid = _obs(store, at=now)
    store.obs_ref_put(oid, "chan-1", "msg-1", now + 60)

    assert store.obs_refs_sweep(now) == 0, "a live reference must not be swept"
    assert store.obs_refs_sweep(now + 120) == 1
    assert store.obs_ref(oid) is None
    assert store.observation(oid) is not None, "the record outlives the way back to it"


def test_a_reply_written_after_the_reference_expired_has_nowhere_to_go(store):
    """It comes back with no channel, so the bot declines it rather than posting a naked message
    into a channel where nobody can tell what it answers."""
    now = time.time()
    oid = _obs(store, at=now)
    store.obs_ref_put(oid, "chan-1", "msg-1", now + 60)
    store.obs_refs_sweep(now + 120)
    store.reply_queue(oid, "too late", "maheswar@cs.mcgill.ca", now)
    assert store.replies_pending()[0]["channel_id"] is None


def test_sending_a_reply_marks_the_question_answered(store):
    now = time.time()
    oid = _obs(store, at=now)
    store.obs_ref_put(oid, "c", "m", now + 60)
    rid = store.reply_queue(oid, "here you go", "who@x", now)
    store.reply_settle(rid, now + 1)

    assert store.observation(oid)["answered"] > 0
    assert store.replies_pending() == [], "a sent reply must not be collected twice"


def test_a_failed_send_is_kept_with_its_reason_and_not_retried_forever(store):
    now = time.time()
    oid = _obs(store, at=now)
    rid = store.reply_queue(oid, "body", "who@x", now)
    store.reply_settle(rid, now, error="403 Forbidden: missing Send Messages")

    assert store.replies_pending() == []
    assert "Send Messages" in store.replies_for(oid)[0]["error"]
    assert not store.observation(oid)["answered"], "a failed send has not answered anybody"


def test_a_conversation_reads_oldest_first(store):
    for at, text in ((3.0, "third"), (1.0, "first"), (2.0, "second")):
        store.observe_put({"at": at, "channel": "help", "who": "a", "kind": "chatter",
                           "text": text, "terms": "", "thread": "t1"})
    assert [o["text"] for o in store.observation_thread("t1")] == ["first", "second", "third"]


def test_counts_are_by_kind_and_by_how_many_people(store):
    for who in ("a", "b", "a"):
        _obs(store, at=time.time(), who=who, text=f"question from {who} {time.time()}")
    c = store.observation_counts()
    assert c["people"] == 2, "one person asking twice is not two people"

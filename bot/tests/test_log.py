"""The log, and the one question it exists to answer."""
from __future__ import annotations

import time

from gini_bot.log import Log, salt_for
from gini_bot.observations import PROBLEM, observe

SALT = "a-test-salt"


def _log(tmp_path):
    return Log(tmp_path / "observations.db")


def _say(lg, uid, text, at=None, channel="help"):
    lg.append(observe(at=at or time.time(), channel=channel, user_id=uid, salt=SALT, text=text))


def test_the_salt_is_created_once_and_kept_outside_the_database(tmp_path):
    """A copy of the log taken away to be read carries no way to link a hash to a person."""
    first = salt_for(tmp_path)
    assert (tmp_path / "salt").exists()
    assert salt_for(tmp_path) == first, "a new salt each run would make everyone look like nobody"
    lg = Log(tmp_path / "observations.db")
    _say(lg, 1, "gbuilder core dumped")
    assert first not in (tmp_path / "observations.db").read_bytes().decode("latin-1")


def test_a_recurring_problem_is_counted_by_people_not_by_messages(tmp_path):
    """One insistent person is one problem; three people saying it once is a different fact, and
    the ordering of the report depends on telling them apart."""
    lg = _log(tmp_path)
    for uid, t in ((1, "gbuilder core dumped on the lab machine, help?"),
                   (2, "the lab machine core dumped gbuilder"),
                   (3, "my gbuilder core dumps on the lab machines too"),
                   (1, "gbuilder core dumped again")):
        _say(lg, uid, t)

    cl = lg.clusters(min_people=2)
    assert len(cl) == 1
    assert cl[0].people == 3, "a reworded or re-conjugated report was filed as a separate incident"
    assert cl[0].count == 4


def test_one_person_complaining_repeatedly_is_not_an_outbreak(tmp_path):
    lg = _log(tmp_path)
    for _ in range(5):
        _say(lg, 7, "gbuilder core dumped on the lab machine")
    assert lg.clusters(min_people=2) == []
    assert len(lg.clusters(min_people=1)) == 1


def test_the_report_orders_by_how_many_people_are_affected(tmp_path):
    lg = _log(tmp_path)
    for uid in (1, 2):
        _say(lg, uid, "how do I add a router to the canvas")
    for uid in (3, 4, 5):
        _say(lg, uid, "gbuilder core dumped on the lab machine")

    cl = lg.clusters(min_people=2)
    assert [c.people for c in cl] == [3, 2], "the thing more people hit must come first"


def test_chatter_is_recorded_but_never_reported_as_an_issue(tmp_path):
    """Everything is kept — the log is for reading — but a report of the pressing issues that
    included social chat would be unreadable."""
    lg = _log(tmp_path)
    for uid in (1, 2, 3):
        _say(lg, uid, "good morning everyone hope the assignment went well")
    assert lg.count() == 3
    assert lg.clusters(min_people=2) == []


def test_a_window_excludes_what_is_older_than_it(tmp_path):
    lg = _log(tmp_path)
    old = time.time() - 40 * 86400
    for uid in (1, 2):
        _say(lg, uid, "gbuilder core dumped on the lab machine", at=old)
    assert lg.clusters(since=time.time() - 14 * 86400, min_people=2) == []
    assert len(lg.clusters(since=0, min_people=2)) == 1


def test_the_report_reads_as_sentences_on_an_empty_log(tmp_path):
    assert "nothing recurring yet" in _log(tmp_path).report()


def test_the_report_names_the_problem_in_the_words_somebody_used(tmp_path):
    lg = _log(tmp_path)
    for uid in (1, 2):
        _say(lg, uid, "gbuilder core dumped on the lab machine")
    out = lg.report(min_people=2)
    assert "core dumped" in out
    assert "2 people" in out


def test_counts_by_kind_are_available_for_the_header(tmp_path):
    lg = _log(tmp_path)
    _say(lg, 1, "gbuilder core dumped")
    _say(lg, 2, "how do I add a router")
    assert lg.count() == 2
    assert lg.count(PROBLEM) == 1


def test_reading_the_same_message_twice_records_it_once(tmp_path):
    """Backfilling history overlaps live ingestion by design — the bot is usually running while the
    backfill reads the same channel. A message is identified by when it was sent, who sent it and
    what it said, so the two paths produce the same row and the second is ignored.

    Doing this with a message id would have been the obvious route and is the one thing
    `observations` will not store: an id is a way back to the message, and the message names its
    author."""
    lg = _log(tmp_path)
    at = 1789000000.123
    for _ in range(3):
        lg.append(observe(at=at, channel="help", user_id=1, salt=SALT,
                          text="gbuilder core dumped on the lab machine"))
    assert lg.count() == 1


def test_the_same_words_from_two_people_are_two_observations(tmp_path):
    """The dedup key must not collapse a genuine recurrence — which is the signal, not noise."""
    lg = _log(tmp_path)
    at = 1789000000.123
    for uid in (1, 2):
        lg.append(observe(at=at, channel="help", user_id=uid, salt=SALT,
                          text="gbuilder core dumped on the lab machine"))
    assert lg.count() == 2
    assert lg.clusters(min_people=2)[0].people == 2


def test_one_person_saying_the_same_thing_at_two_moments_is_two_observations(tmp_path):
    lg = _log(tmp_path)
    for at in (1789000000.1, 1789000600.4):
        lg.append(observe(at=at, channel="help", user_id=1, salt=SALT,
                          text="gbuilder core dumped on the lab machine"))
    assert lg.count() == 2


def test_a_log_written_before_the_rule_existed_is_deduplicated_on_open(tmp_path):
    """The unique index cannot be added over rows that already break it, and a log written by an
    earlier build may hold duplicates from a backfill that ran twice."""
    import sqlite3
    path = tmp_path / "observations.db"
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE observations (at REAL, channel TEXT, who TEXT, kind TEXT, text TEXT, terms TEXT);
      INSERT INTO observations VALUES (1.0,'help','abc','problem','it broke','broke it');
      INSERT INTO observations VALUES (1.0,'help','abc','problem','it broke','broke it');
      INSERT INTO observations VALUES (2.0,'help','def','problem','it broke','broke it');
    """)
    db.commit()
    db.close()

    lg = Log(path)
    assert lg.count() == 2, "the duplicate pair should have collapsed, the distinct row survived"

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

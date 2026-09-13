"""What the bot may write down, and what it must not.

The privacy rules here are not preferences. The Teaching Center's own docstring says "the portal
never learns who did the work", and a Discord bot is identity-bearing by nature — so every test in
the first half of this file is about keeping that sentence true from the other end.
"""
from __future__ import annotations

from gini_bot.observations import (CHATTER, PROBLEM, QUESTION, Observation, classify, observe,
                                   overlap, redact, terms, who)

SALT = "a-test-salt"


def test_the_same_person_hashes_the_same_way_and_two_people_do_not():
    """Recurrence counting rests entirely on this: "four different people hit it" is only true if
    one person cannot look like four, and four cannot look like one."""
    assert who(1234, SALT) == who(1234, SALT)
    assert who(1234, SALT) != who(1235, SALT)
    assert len(who(1234, SALT)) == 16


def test_a_different_salt_makes_the_past_unlinkable():
    """The intended way to forget. Rotating the salt is not a key change, it is an erasure."""
    assert who(1234, SALT) != who(1234, "another-salt")


def test_the_user_id_never_appears_in_the_hash():
    assert "1234" not in who(1234, SALT)


def test_a_mention_is_stripped_because_it_is_a_user_id_in_the_text():
    """The subtle one. Hashing the author while storing their friend's id verbatim in the body
    would leak from the other end exactly what the hash protects."""
    out = redact("hey <@987654321098765432> did you fix it")
    assert "987654321098765432" not in out
    assert "@someone" in out


def test_pasted_ids_addresses_and_invites_do_not_survive():
    out = redact("ping 987654321098765432 or mail me at student@mail.mcgill.ca "
                 "https://discord.gg/abcd1234")
    assert "987654321098765432" not in out
    assert "student@mail.mcgill.ca" not in out
    assert "discord.gg/abcd1234" not in out


def test_an_observation_can_only_be_built_through_the_one_door():
    """`observe` is the only place raw Discord data becomes a record, so no call site can assemble
    one that skipped redaction."""
    o = observe(at=1.0, channel="help", user_id=555, salt=SALT,
                text="<@111222333444555666> my gbuilder crashed")
    assert isinstance(o, Observation)
    assert "111222333444555666" not in o.text
    assert o.who == who(555, SALT)


def test_a_broken_thing_is_recognised_however_it_is_conjugated():
    """Whole-word matching under-counted a real pair — "core dumped" matched and "core dumps" did
    not, so one incident was reported by one fewer person than had hit it."""
    for t in ("gbuilder core dumped", "my gbuilder core dumps", "it keeps core dumping",
              "gbuilder crashed", "it crashes on launch", "the run failed", "it keeps failing"):
        assert classify(t) == PROBLEM, t


def test_an_ordinary_serial_dump_is_not_mistaken_for_a_crash():
    """"dump" is everyday GINI vocabulary — every /procs read is one — so it is deliberately not a
    signal on its own, or the log would read as a server in permanent crisis."""
    assert classify("the /procs dump shows four processes") == CHATTER
    assert classify("what does the traps dump say about scause") == QUESTION


def test_a_question_is_recognised_by_its_shape():
    assert classify("how do I add a router") == QUESTION
    assert classify("is there a way to see the routing table?") == QUESTION


def test_a_broken_thing_beats_a_question_when_a_message_is_both():
    """"how do I fix this traceback" is an occurrence of the traceback. Counting it as a question
    would scatter one recurring failure across a dozen differently-worded askings."""
    assert classify("how do I fix this traceback?") == PROBLEM


def test_two_people_describing_one_failure_are_recognised_as_one_thing():
    """The measured pair that killed the first design, kept as a test so it cannot come back."""
    a = terms("gbuilder core dumped on the lab machine, help?")
    b = terms("the lab machine core dumped gbuilder")
    assert overlap(a, b) >= 0.5


def test_two_different_complaints_that_share_a_word_stay_apart():
    a = terms("gbuilder core dumped on the lab machine")
    b = terms("gbuilder wont open the router lab window")
    assert overlap(a, b) < 0.5


def test_a_message_with_too_little_in_it_joins_nothing():
    """"same here" must not merge every open incident into one."""
    assert terms("same here") == ""
    assert overlap("", "core dump gbuild lab") == 0.0

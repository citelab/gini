"""Assignment codes: the thing a student types wrong at 9am in a lab.

Two properties matter and nothing else does. A code must survive being *read off paper and typed*
— which is why the alphabet has no I/L/O/U and why we fold the confusable characters instead of
rejecting them — and a mistyped code must be refused with a sentence the student can act on,
because "invalid" would just make them type it again.
"""
import pytest

from gini.domain.ticket import (
    ALPHABET, LENGTH, Ticket, TicketError, check_symbol, error_for, mint, normalize, parse, valid,
)


def _fixed(seed: int):
    """A deterministic byte source, so a test never depends on which code got minted."""
    return lambda n: bytes((seed * 7 + i * 13) % 256 for i in range(n))


def test_minted_codes_are_valid_and_pretty():
    tk = mint(_fixed(1))
    assert len(tk.code) == LENGTH
    assert set(tk.code) <= set(ALPHABET)
    assert parse(tk.pretty).code == tk.code       # the printed form round-trips
    assert tk.pretty.count("-") == 2
    assert tk.short == tk.code[:4]


def test_different_students_get_different_codes():
    assert mint(_fixed(1)).code != mint(_fixed(2)).code


def test_normalize_folds_the_confusable_characters():
    # A student who types O for 0 has not made a mistake worth a red error.
    assert normalize("o1-il0") == "01110"
    assert normalize(" a3k7 b2m9 ") == "A3K7B2M9"
    assert normalize("") == ""


def test_typed_variations_all_arm_the_same_chain():
    tk = mint(_fixed(3))
    spaced = " ".join(tk.code[i:i + 4] for i in range(0, LENGTH, 4))
    for typed in (tk.code, tk.pretty, tk.code.lower(), spaced):
        assert parse(typed).code == tk.code


def test_empty_code_asks_for_the_code():
    with pytest.raises(TicketError, match="instructor"):
        parse("   ")


def test_a_letter_outside_the_alphabet_names_itself():
    tk = mint(_fixed(4))
    bad = "U" + tk.code[1:]
    assert "U" in error_for(bad)


def test_wrong_length_says_how_many_characters():
    msg = error_for("A3K7B2M9")
    assert "12 characters" in msg and "8" in msg


def test_a_typo_is_caught_and_named_as_a_typo():
    tk = mint(_fixed(5))
    wrong = tk.code[:-1] + ("0" if tk.code[-1] != "0" else "1")
    assert not valid(wrong)
    assert "typo" in error_for(wrong)


def test_almost_every_single_character_typo_is_caught():
    """The check symbol's whole job. A hash-derived symbol lets exactly 1 in 32 errors through, of
    any shape — so over every possible single-character slip we expect ~97% detection."""
    tried = caught = 0
    for seed in range(40):                       # many codes: one code's 372 mutations is a sample
        tk = mint(_fixed(seed), questions=bool(seed % 2))
        for i in range(LENGTH):
            for ch in ALPHABET:
                if ch == tk.code[i]:
                    continue
                tried += 1
                caught += not valid(tk.code[:i] + ch + tk.code[i + 1:])
    # 30/32 = 0.9375. It was 31/32 until the check symbol gained a second salt to carry "this lab
    # asks questions" — a mistyped code now gets two chances to validate by accident. That is the
    # whole price of the bit, it is paid here, and the threshold names the real rate rather than
    # being nudged down to whatever passes.
    assert caught / tried > 0.92, f"only {caught / tried:.2%} of single-character typos refused"


def test_almost_every_transposition_is_caught():
    """The other everyday slip: two adjacent characters swapped while typing. A weighted-sum
    check digit has blind spots here; a hash does not."""
    tried = caught = 0
    for seed in range(120):                      # eleven swaps per code cannot measure a 6% miss
        tk = mint(_fixed(seed), questions=bool(seed % 2))
        for i in range(LENGTH - 1):
            a, b = tk.code[i], tk.code[i + 1]
            if a == b:
                continue                         # swapping a character with itself is not an error
            tried += 1
            caught += not valid(tk.code[:i] + b + a + tk.code[i + 2:])
    assert tried >= 800
    assert caught / tried > 0.92, f"only {caught / tried:.2%} of transpositions refused"


def test_check_symbol_is_stable_and_in_the_alphabet():
    assert check_symbol("A3K7B2M9QQ4") == check_symbol("A3K7B2M9QQ4")
    assert check_symbol("A3K7B2M9QQ4") in ALPHABET


def test_ticket_prints_as_it_is_read():
    assert str(Ticket("A3K7B2M9QQ4T")) == "A3K7-B2M9-QQ4T"


# ---- one bit, carried by the code itself ---------------------------------------- #
# Does the lab this code was issued for ask the student questions? gBuilder arms OFFLINE when the
# course server cannot be reached, and without this a student works the whole lab, hands in, and
# nobody discovers until a marker sees blanks caused by hotel wifi. It cannot be fetched: by the
# time you could fetch it you would not need it.
#
# The bit does not live in the eleven identity symbols. It lives in WHICH of two salts reproduces
# the check symbol, so identity stays at the full 55 bits.
def test_a_code_says_whether_its_lab_asks_questions():
    assert mint(_fixed(1), questions=True).questions is True
    assert mint(_fixed(1)).questions is False


def test_the_answer_survives_being_typed_back_in():
    """The whole point — it has to come back out of the twelve characters on the printout, with no
    server, no database and nothing else on hand."""
    for asks in (True, False):
        tk = mint(questions=asks)
        assert parse(tk.pretty).questions is asks
        assert parse(tk.code.lower()).questions is asks


def test_it_costs_no_identity():
    """55 bits, still. A count would have had to eat a symbol; one bit did not."""
    a, b = mint(_fixed(2), questions=True), mint(_fixed(2), questions=False)
    assert a.code[:11] == b.code[:11]              # the same eleven random symbols
    assert a.code[-1] != b.code[-1]                # only the check symbol moved


def test_a_code_can_never_be_read_two_ways():
    """One payload in thirty-two produces the same check symbol under both salts, and such a code
    would read as "has questions" whatever it was issued for. Minting draws again."""
    for i in range(300):
        for asks in (True, False):
            tk = mint(_fixed(i), questions=asks)
            other = check_symbol(tk.code[:11], questions=not asks)
            assert tk.code[-1] != other, f"{tk.code} reads both ways"


def test_a_code_minted_before_any_of_this_still_works(): 
    """Backward compatibility, and it needs no migration: an old code validates under the first
    salt and reads False, which is TRUE of it — there were no questions to ask."""
    old = "A3K7B2M9QQ4" + check_symbol("A3K7B2M9QQ4")
    assert parse(old).questions is False
    assert valid(old)


def test_a_mistyped_code_errs_towards_saying_there_are_questions():
    """When luck makes a typo validate, the safe way to be wrong is to claim a lab has questions
    when it has none: the student is told to connect, connects, and is told otherwise. The other
    way round is the silent failure this bit exists to prevent."""
    plain = mint(_fixed(3))
    assert plain.questions is False
    # Any code whose check symbol matches the questions salt must be read that way, whichever
    # salt was used to make it.
    payload = plain.code[:11]
    assert parse(payload + check_symbol(payload, questions=True)).questions is True


def test_check_symbol_differs_by_salt():
    p = "A3K7B2M9QQ4"
    assert check_symbol(p) == check_symbol(p)                     # still stable
    assert check_symbol(p, questions=True) in ALPHABET

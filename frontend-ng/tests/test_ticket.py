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
    tk = mint(_fixed(6))
    tried = caught = 0
    for i in range(LENGTH):
        for ch in ALPHABET:
            if ch == tk.code[i]:
                continue
            tried += 1
            caught += not valid(tk.code[:i] + ch + tk.code[i + 1:])
    assert tried == LENGTH * (len(ALPHABET) - 1)
    assert caught / tried > 0.93                 # 31/32 = 0.969, with room for hash luck


def test_almost_every_transposition_is_caught():
    """The other everyday slip: two adjacent characters swapped while typing. A weighted-sum
    check digit has blind spots here; a hash does not."""
    tk = mint(_fixed(7))
    tried = caught = 0
    for i in range(LENGTH - 1):
        a, b = tk.code[i], tk.code[i + 1]
        if a == b:
            continue                             # swapping a character with itself is not an error
        tried += 1
        caught += not valid(tk.code[:i] + b + a + tk.code[i + 2:])
    assert tried >= 8
    assert caught == tried or caught / tried > 0.85


def test_check_symbol_is_stable_and_in_the_alphabet():
    assert check_symbol("A3K7B2M9QQ4") == check_symbol("A3K7B2M9QQ4")
    assert check_symbol("A3K7B2M9QQ4") in ALPHABET


def test_ticket_prints_as_it_is_read():
    assert str(Ticket("A3K7B2M9QQ4T")) == "A3K7-B2M9-QQ4T"

"""Case codes: typed by hand, on a machine that is already misbehaving."""
from __future__ import annotations

import pytest

from gini_doctor.stage1 import casecode


def test_a_minted_code_reads_back_and_prints_in_threes():
    code = casecode.mint()
    assert len(code) == casecode.LENGTH
    assert casecode.valid(code) and casecode.error_for(code) == ""
    assert casecode.parse(code) == code
    assert casecode.pretty(code) == "-".join([code[0:4], code[4:8], code[8:12]])
    assert casecode.parse(casecode.pretty(code)) == code


def test_minting_is_deterministic_when_the_randomness_is_given():
    fixed = casecode.mint(lambda n: bytes(range(n)))
    assert fixed == casecode.mint(lambda n: bytes(range(n)))
    assert casecode.valid(fixed)


def test_the_characters_people_confuse_are_folded_not_refused():
    """Someone who typed O for 0 has not made a mistake worth a red error message."""
    code = casecode.mint()
    typed = casecode.pretty(code).lower().replace("0", "o").replace("1", "l")
    assert casecode.parse(" %s " % typed) == code
    assert casecode.parse(code.replace("1", "I")) == code


def test_a_single_mistyped_character_is_caught_nearly_always():
    """A hash-derived check symbol has no structure for a typo to hide in: any single-character
    error is caught with probability 31/32, whatever its shape. Measured here rather than
    asserted, over every substitution at every position."""
    code = casecode.mint(lambda n: bytes(range(7, 7 + n)))
    tried = caught = 0
    for i in range(casecode.LENGTH):
        for ch in casecode.ALPHABET:
            if ch == code[i]:
                continue
            tried += 1
            caught += not casecode.valid(code[:i] + ch + code[i + 1:])
    assert tried == casecode.LENGTH * (len(casecode.ALPHABET) - 1)
    assert caught / tried > 0.9, "%d of %d single-character typos went undetected" % (
        tried - caught, tried)


def test_a_character_no_code_contains_is_named():
    bad = casecode.error_for("ABCD-EFGH-JKMU")
    assert "U" in bad and "I, L, O and U" in bad


def test_the_wrong_length_says_so_rather_than_blaming_a_character():
    assert "12 characters; this one has 4" in casecode.error_for("ABCD")
    assert "Enter the case code" in casecode.error_for("")
    assert "Enter the case code" in casecode.error_for(None)


def test_a_typo_is_told_apart_from_something_that_is_not_a_case_code():
    """Both are refused, but for a person the difference is whether to look again at what they
    typed or to ask for the code again."""
    with pytest.raises(casecode.CaseCodeError) as e:
        casecode.parse("000000000000" if not casecode.valid("000000000000") else "000000000001")
    assert "typo" in str(e.value) and "not issued by a Health Center" in str(e.value)

"""What a receipt code can and cannot detect.

The Teaching Center leans on two different collision checks to catch two different cheats, and the
whole design rests on knowing which catches which. That distinction was asserted in a spec on the
strength of a one-off run; these tests make it a property of the code instead.

The short version: a receipt catches a copied *file*; only the artifact hash catches copied *work*.
"""
from __future__ import annotations

from gini.domain import proof as P
from gini.domain import ticket as T


def chain_with(code: str, t0: float = 1000.0) -> P.Chain:
    """Identical work, recorded under `code`: same events, same timestamps."""
    c = P.Chain.start(code, assignment="lab1", gini_version="1")
    for i, (kind, data) in enumerate([("place", {"n": "M1"}),
                                      ("place", {"n": "M2"}),
                                      ("connect", {"a": "M1", "b": "M2"})]):
        c.append(kind, data, t=t0 + i)
    return c


def test_the_same_proof_always_yields_the_same_receipt():
    """The premise of duplicate detection: hand your proof file to a friend and the collision is
    visible at submission."""
    code = T.mint().code
    a, b = chain_with(code), chain_with(code)
    assert P.receipt_code(P.build_proof(a)) == P.receipt_code(P.build_proof(b))


def test_identical_work_under_different_codes_yields_DIFFERENT_receipts():
    """The limit of duplicate detection, and the reason the artifact hash is also needed.

    The chain binds the ticket into genesis and the MAC covers the head, so two students who build
    exactly the same thing under their own codes collide on nothing. A receipt check alone would
    miss the collusion case that actually happens — importing a classmate's topology and doing your
    own run.
    """
    a = chain_with(T.mint().code)
    b = chain_with(T.mint().code)
    assert P.receipt_code(P.build_proof(a)) != P.receipt_code(P.build_proof(b))


def test_a_receipt_moves_when_any_entry_changes():
    """It is derived from the MAC over the chain head, so it is a fingerprint of the whole history,
    not of the outcome."""
    code = T.mint().code
    a = chain_with(code)
    b = chain_with(code)
    b.append("place", {"n": "M3"}, t=2000.0)
    assert P.receipt_code(P.build_proof(a)) != P.receipt_code(P.build_proof(b))


def test_a_receipt_is_eight_crockford_symbols_in_two_groups():
    """It is read aloud over a help desk and typed by a teacher, which is why it is short and why
    the alphabet excludes I, L, O and U."""
    r = P.receipt_code(P.build_proof(chain_with(T.mint().code)))
    assert len(r) == 9 and r[4] == "-"
    assert all(ch in T.ALPHABET for ch in r.replace("-", ""))


def test_no_receipt_without_a_mac():
    """A proof with no MAC has nothing to fingerprint, and must not be given a plausible-looking
    handle a teacher could mistake for a real one."""
    assert P.receipt_code({"head": "abc"}) == ""


def test_the_artifact_hash_is_what_catches_copied_work():
    """The complement to the receipt: the same topology built under two codes shares an artifact
    hash even though the receipts differ. This is the check the TC flags on."""
    topo = {"devices": [{"name": "M1", "type_key": "host"}], "links": []}
    a = P.artifact_summary(topo)
    b = P.artifact_summary(dict(topo))
    assert a["sha256"] == b["sha256"]

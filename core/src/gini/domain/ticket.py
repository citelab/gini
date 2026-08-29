"""Assignment codes — the thing a student types to start recording.

A code's only job is to be **unique per student** and typo-resistant. It is deliberately *not* a
key: Phase 1's integrity comes from the app MAC in ``proof.py``, and Phase 2 replaces that with a
server countersignature, at which point the client needs no secret at all. Keeping the code dumb
is what keeps it 12 characters instead of ~20 — the trade recorded in §5 of the design note.

Crockford base32, because this code is read off a printout and typed by hand: the alphabet has no
I, L, O or U, so ``0/O`` and ``1/I/l`` cannot be confused and no code can spell an obscenity. The
confusable characters are *normalised* on input rather than rejected — a student who types O for
0 has not made a mistake worth a red error message.

Pure Python (hashlib/secrets only), so the whole thing is unit-testable without Qt.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32 — no I, L, O, U
LENGTH = 12                                     # what the student types, hyphens excluded
PAYLOAD_LEN = 11                                # 11 identity symbols + 1 check symbol
GROUP = 4                                       # printed as XXXX-XXXX-XXXX

# Domain separation: the check symbol must not collide with any other hash we take of a code
# (the proof MAC also hashes the ticket), so the two can never be mistaken for one another.
_CHECK_SALT = b"gini-assignment-code/1"

# What a hand-typed character was almost certainly meant to be. Only these three: they are the
# pairs the Crockford alphabet was designed around.
_CONFUSABLE = {"O": "0", "I": "1", "L": "1"}


class TicketError(ValueError):
    """A code that cannot be accepted, carrying the reason to *show the student*.

    Every message here is written to be read by a first-year in a lab, not by a developer: it
    says what is wrong with the code in their hand and what to do about it.
    """


def normalize(code: str) -> str:
    """A typed code reduced to its canonical 12 symbols: upper-cased, confusables folded, and
    every separator dropped. Hyphens are cosmetic — the student may type them, paste them, or
    leave them out, and all three must arm the same chain."""
    return "".join(_CONFUSABLE.get(ch, ch) for ch in (code or "").upper() if ch.isalnum())


def check_symbol(payload: str) -> str:
    """The 12th character: a hash-derived check symbol over the first 11.

    A weighted-sum check digit is the usual choice, but it is only as good as its weights modulo
    32, and every scheme of that shape lets some class of error through — with odd weights, for
    instance, swapping two characters exactly 16 apart in the alphabet is invisible. A hash has no
    such structure: *any* typo, of any shape, is caught with probability 31/32. That is both
    stronger and far easier to reason about than a table of weights.
    """
    digest = hashlib.sha256(_CHECK_SALT + payload.encode("ascii")).digest()
    return ALPHABET[digest[0] % len(ALPHABET)]   # 256 = 8 x 32, so the symbol is unbiased


@dataclass(frozen=True)
class Ticket:
    """A validated assignment code. `code` is always the normalised 12 symbols."""
    code: str

    @property
    def pretty(self) -> str:
        """As printed and as shown back to the student: XXXX-XXXX-XXXX."""
        return "-".join(self.code[i:i + GROUP] for i in range(0, LENGTH, GROUP))

    @property
    def short(self) -> str:
        """The first group — enough to tell two students' codes apart in a one-line strip, and
        short enough that it does not turn the recording indicator into a wall of characters."""
        return self.code[:GROUP]

    def __str__(self) -> str:
        return self.pretty


def parse(code: str) -> Ticket:
    """A typed code → a Ticket, or TicketError with a reason fit to display.

    Order matters: the checks run from the most specific complaint to the least, so a student
    who mistyped one character is told exactly that rather than being handed a length count.
    """
    raw = normalize(code)
    if not raw:
        raise TicketError("Enter the assignment code your instructor gave you.")
    bad = sorted({ch for ch in raw if ch not in ALPHABET})
    if bad:
        raise TicketError(
            "An assignment code never contains " + ", ".join(bad)
            + " — check that character against the printout. (Codes skip I, L, O and U so "
              "nothing can be misread.)")
    if len(raw) != LENGTH:
        raise TicketError(
            f"An assignment code has {LENGTH} characters; this one has {len(raw)}.")
    if raw[-1] != check_symbol(raw[:PAYLOAD_LEN]):
        raise TicketError(
            "That code has a typo in it — check each character against the printout.")
    return Ticket(raw)


def valid(code: str) -> bool:
    try:
        parse(code)
        return True
    except TicketError:
        return False


def error_for(code: str) -> str:
    """The reason `code` is unacceptable, or "" when it is fine. For UI that wants to show the
    complaint without catching."""
    try:
        parse(code)
        return ""
    except TicketError as e:
        return str(e)


def mint(rand=None) -> Ticket:
    """Issue a fresh code. `rand(n)` returns n random bytes (defaults to `secrets.token_bytes`,
    injectable so tests can mint deterministically).

    11 symbols is 55 bits of identity, so an instructor can hand out a term's worth of codes
    without a collision worth thinking about — and, more to the point, a student cannot guess a
    classmate's."""
    rnd = rand or secrets.token_bytes
    payload = "".join(ALPHABET[b % len(ALPHABET)] for b in rnd(PAYLOAD_LEN))
    return Ticket(payload + check_symbol(payload))

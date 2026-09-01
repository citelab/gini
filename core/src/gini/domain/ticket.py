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
#
# TWO of them, and which one validates is one bit of payload carried for free: does the lab this
# code was issued for ask the student questions? The bit does not live in the eleven identity
# symbols — those stay fully random, at 55 bits — it lives in WHICH salt reproduces the check
# symbol. See `Ticket.questions` for why a code has to be able to say this on its own.
_CHECK_SALT = b"gini-assignment-code/1"        # a lab that asks nothing
_CHECK_SALT_Q = b"gini-assignment-code/2"      # a lab with questions in it

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


def check_symbol(payload: str, *, questions: bool = False) -> str:
    """The 12th character: a hash-derived check symbol over the first 11.

    A weighted-sum check digit is the usual choice, but it is only as good as its weights modulo
    32, and every scheme of that shape lets some class of error through — with odd weights, for
    instance, swapping two characters exactly 16 apart in the alphabet is invisible. A hash has no
    such structure: *any* typo, of any shape, is caught with probability 31/32. That is both
    stronger and far easier to reason about than a table of weights.

    `questions` picks the salt, which is how the code carries that fact. It costs one thirty-second
    of the typo detection — a mistyped code now gets two chances to validate by accident, so 31/32
    becomes 30/32 — and nothing else. A code that slips through offline is still refused by the
    course server the moment one is reachable.
    """
    salt = _CHECK_SALT_Q if questions else _CHECK_SALT
    digest = hashlib.sha256(salt + payload.encode("ascii")).digest()
    return ALPHABET[digest[0] % len(ALPHABET)]   # 256 = 8 x 32, so the symbol is unbiased


@dataclass(frozen=True)
class Ticket:
    """A validated assignment code. `code` is always the normalised 12 symbols."""
    code: str

    #: Does the lab this code was issued for ask the student questions?
    #:
    #: Read from the code ITSELF, so it is known before any network call and survives having none.
    #: That is the whole reason it is here: gBuilder arms offline when the course server cannot be
    #: reached, and without this a student would work a whole lab, hand in, and only afterwards
    #: would anyone discover they were never shown the questions. It is not something the panel can
    #: fetch and then act on — by the time you could fetch it you would not need it.
    #:
    #: The server decides it at MINT time from the same activity row it uses to choose the
    #: questions, in the same request, so the two cannot disagree for a given code even if the lab
    #: is edited afterwards.
    #:
    #: A code minted before this existed validates under the first salt and reads False, which is
    #: true of it: there were no questions to ask.
    questions: bool = False

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
    # The QUESTIONS salt first. A correctly-typed code validates under exactly one of the two —
    # `mint` refuses to issue one that does not — so the order does not decide anything for a code
    # this system issued. It decides for a MISTYPED one, where either salt may match by luck, and
    # the safe way to be wrong is to say a lab has questions when it has none: the student is told
    # to connect, connects, and is told otherwise. The other way round is the silent failure this
    # bit exists to prevent.
    payload = raw[:PAYLOAD_LEN]
    for asks in (True, False):
        if raw[-1] == check_symbol(payload, questions=asks):
            return Ticket(raw, questions=asks)
    raise TicketError(
        "That code has a typo in it — check each character against the printout.")


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


def mint(rand=None, *, questions: bool = False) -> Ticket:
    """Issue a fresh code. `rand(n)` returns n random bytes (defaults to `secrets.token_bytes`,
    injectable so tests can mint deterministically).

    11 symbols is 55 bits of identity, so an instructor can hand out a term's worth of codes
    without a collision worth thinking about — and, more to the point, a student cannot guess a
    classmate's. `questions` does not spend any of them: it selects the salt (see `check_symbol`).

    AMBIGUITY IS REFUSED. One payload in thirty-two produces the same check symbol under both
    salts, and such a code would read as "has questions" to `parse` no matter which lab it was
    issued for. Drawing again is one retry in thirty-two and buys an absolute property: no code
    this system issues can be read two ways.
    """
    rnd = rand or secrets.token_bytes
    for _ in range(64):
        payload = "".join(ALPHABET[b % len(ALPHABET)] for b in rnd(PAYLOAD_LEN))
        check = check_symbol(payload, questions=questions)
        if check != check_symbol(payload, questions=not questions):
            return Ticket(payload + check, questions=questions)
    # 64 ambiguous draws running is (1/32)**64. A caller passing a fixed `rand` for a test can
    # reach it, though, and silently returning an ambiguous code would be the worse answer.
    raise TicketError("Could not mint an unambiguous code.")

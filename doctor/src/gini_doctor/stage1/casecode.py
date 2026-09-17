"""Case codes — the twelve characters that are the consent.

A case is the unit of work at the Health Center, and its code is what admits a machine to one:
opening a case vends a code, and running ``gini-doctor run --case CODE`` is what sends anything at
all. So the code is read off a screen or out of a chat message and typed by hand, on two machines,
by two different people. That is the same job an assignment code does in ``gini.domain.ticket``,
and it is done the same way: Crockford base32 (no I, L, O or U, so ``0/O`` and ``1/I/l`` cannot be
confused), twelve symbols printed ``XXXX-XXXX-XXXX``, the twelfth a hash-derived check symbol so a
typo is caught **on the machine, offline**, before anything leaves it.

**Why this lives in the doctor and not in the Health Center.** Both sides need the rule: the server
mints a code, the doctor has to tell a typo from a wrong case before it opens a connection. Only
one of the two may not take a dependency — the doctor depends on nothing by design — so the shared
rule lives here and the Health Center imports it. One implementation, no drift to test for.

**Its own salt, deliberately.** ``ticket.py``'s own comment gives the reason: the check symbol must
not collide with any other hash taken of a code. The consequence of sharing one would be worse
here than there — an assignment code would validate as a case code, so a student pasting their lab
code into ``--case`` would be told it is fine and then be told by the server that no such case
exists. With separate salts the answer is "that is not a case code", said locally and at once.
"""
from __future__ import annotations

import hashlib
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32 — no I, L, O, U
LENGTH = 12                                     # what a person types, hyphens excluded
PAYLOAD_LEN = 11                                # 11 identity symbols + 1 check symbol
GROUP = 4                                       # printed as XXXX-XXXX-XXXX

# Domain separation. See the module docstring: sharing ticket.py's salt would make the two kinds of
# code mutually acceptable, which turns a local "that is not a case code" into a round trip and a
# confusing answer from the server.
_CHECK_SALT = b"gini-case-code/1"

# What a hand-typed character was almost certainly meant to be — the three pairs the Crockford
# alphabet was designed around. Folded rather than rejected: someone who typed O for 0 has not
# made a mistake worth an error message.
_CONFUSABLE = {"O": "0", "I": "1", "L": "1"}


class CaseCodeError(ValueError):
    """A code that cannot be accepted, carrying the reason to *show the person*.

    Every message is written for whoever is typing, at a terminal, on a machine that is already
    misbehaving — it says what is wrong with the code in front of them and what to do next.
    """


def normalize(code: str) -> str:
    """A typed code reduced to its canonical 12 symbols: upper-cased, confusables folded, every
    separator dropped. Hyphens are cosmetic, so all three of typed, pasted and run-together work."""
    return "".join(_CONFUSABLE.get(ch, ch) for ch in (code or "").upper() if ch.isalnum())


def check_symbol(payload: str) -> str:
    """The 12th character: a hash-derived check symbol over the first 11.

    A weighted-sum check digit is the usual choice and is only as good as its weights modulo 32;
    every scheme of that shape lets some class of error through. A hash has no such structure, so
    *any* typo, of any shape, is caught with probability 31/32 — stronger, and easier to reason
    about than a table of weights.
    """
    digest = hashlib.sha256(_CHECK_SALT + payload.encode("ascii")).digest()
    return ALPHABET[digest[0] % len(ALPHABET)]   # 256 = 8 x 32, so the symbol is unbiased


def pretty(code: str) -> str:
    """As the portal prints it and as it is echoed back: XXXX-XXXX-XXXX."""
    raw = normalize(code)
    return "-".join(raw[i:i + GROUP] for i in range(0, len(raw), GROUP))


def parse(code: str) -> str:
    """A typed code → its normalised 12 symbols, or ``CaseCodeError`` with a reason fit to show.

    The checks run from the most specific complaint to the least, so someone who mistyped one
    character is told exactly that rather than being handed a length count.
    """
    raw = normalize(code)
    if not raw:
        raise CaseCodeError("Enter the case code the Health Center gave you.")
    bad = sorted({ch for ch in raw if ch not in ALPHABET})
    if bad:
        raise CaseCodeError(
            "A case code never contains " + ", ".join(bad) + " — check that character against the "
            "one you were given. (Case codes skip I, L, O and U so nothing can be misread.)")
    if len(raw) != LENGTH:
        raise CaseCodeError("A case code has %d characters; this one has %d." % (LENGTH, len(raw)))
    if raw[-1] != check_symbol(raw[:PAYLOAD_LEN]):
        raise CaseCodeError("That code has a typo in it — check each character. (If it was copied "
                           "whole and is still refused, ask for the case code again: this one was "
                           "not issued by a Health Center.)")
    return raw


def valid(code: str) -> bool:
    try:
        parse(code)
        return True
    except CaseCodeError:
        return False


def error_for(code: str) -> str:
    """The reason ``code`` is unacceptable, or ``""`` when it is fine — for a caller that wants to
    show the complaint without catching."""
    try:
        parse(code)
        return ""
    except CaseCodeError as e:
        return str(e)


def mint(rand=None) -> str:
    """Issue a fresh code. ``rand(n)`` returns n random bytes (defaults to
    ``secrets.token_bytes``, injectable so a test can mint deterministically).

    11 symbols is 55 bits of identity. A case code is short-lived and scoped to one case, so the
    number that matters is not how many can exist but that nobody can guess a case they were not
    invited to — and 55 bits is far past the point where guessing beats asking.
    """
    rnd = rand or secrets.token_bytes
    payload = "".join(ALPHABET[b % len(ALPHABET)] for b in rnd(PAYLOAD_LEN))
    return payload + check_symbol(payload)

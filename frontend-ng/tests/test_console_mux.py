"""The two-byte console multiplexer (backend/xv6/console_mux.py) — proven before it is wired in.

The bar: (1) every command round-trips; (2) ordinary human typing — including Enter and every
control byte that is NOT the prefix — passes through untouched; (3) a human keystroke interleaved
into an agent sequence never fires a wrong command and never loses the human's byte; (4) the
existing single-byte commands still fire. The two fuzzers are what would catch anything the
hand-written cases miss.

`Decoder` here is the executable spec for the kernel's `consoleintr` state machine — the C is
checked against it. Loaded by path (like test_xv6_agent.py) because the module ships in the
container, not on the frontend import path.
"""
import importlib.util
import random
import sys
from pathlib import Path

import pytest

MUX = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "console_mux.py"
pytestmark = pytest.mark.skipif(not MUX.exists(), reason="backend/xv6 not checked out")


def _load():
    spec = importlib.util.spec_from_file_location("console_mux", MUX)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # @dataclass resolves __module__ via sys.modules
    spec.loader.exec_module(mod)
    return mod


cm = _load()
COMMANDS, Decoder, encode, encode_demoted = (
    cm.COMMANDS, cm.Decoder, cm.encode, cm.encode_demoted)

PREFIX = 0x17
# a realistic set of existing single-byte commands (control bytes), to prove coexistence
SINGLE = frozenset({0x14, 0x16, 0x06, 0x12})   # Ctrl-T Ctrl-V Ctrl-F Ctrl-R


def dec(**kw):
    return Decoder(prefix=PREFIX, single=SINGLE, **kw)


def cmds(events):
    return [(e[1], e[2]) for e in events if e[0] == "cmd"]


def console(events):
    return bytes(e[1] for e in events if e[0] == "console")


def singles(events):
    return [e[1] for e in events if e[0] == "single"]


# -- round trip -------------------------------------------------------------- #
def test_every_command_round_trips():
    for sel, (name, takes_arg) in COMMANDS.items():
        d = dec()
        data = encode(name, 2 if takes_arg else None, prefix=PREFIX)
        ev = d.feed_bytes(data)
        assert cmds(ev) == [(name, 2 if takes_arg else None)], name
        assert console(ev) == b"", f"{name} leaked bytes to the console"


def test_arg_value_is_carried():
    d = dec()
    assert cmds(d.feed_bytes(encode("arm_trap", 9, prefix=PREFIX))) == [("arm_trap", 9)]
    d = dec()
    assert cmds(d.feed_bytes(encode("arm_trap", 0, prefix=PREFIX))) == [("arm_trap", 0)]


def test_self_escape_fires_the_demoted_shadowdump():
    """Ctrl-W's old job. PREFIX PREFIX (Ctrl-W Ctrl-W) = shadowdump, its new binding."""
    d = dec(demoted="shadow_dump")
    ev = d.feed_bytes(encode_demoted(PREFIX))
    assert cmds(ev) == [("shadow_dump", None)] and console(ev) == b""


# -- ordinary human typing is untouched -------------------------------------- #
def test_plain_text_passes_through_verbatim():
    d = dec()
    line = b"ls -la /home\n"
    ev = d.feed_bytes(line)
    assert console(ev) == line and cmds(ev) == []


def test_every_non_prefix_control_byte_passes_or_fires_its_own_command():
    """A control byte is either an existing single-byte command or plain console — never eaten."""
    for b in range(0x01, 0x20):
        if b == PREFIX:
            continue
        d = dec()
        ev = d.feed(b)
        if b in SINGLE:
            assert singles(ev) == [b] and console(ev) == b""
        else:
            assert console(ev) == bytes([b]), f"control byte {b:#x} was swallowed"


def test_enter_is_never_the_prefix():
    for enter in (0x0a, 0x0d):
        assert enter != PREFIX
        d = dec()
        assert console(d.feed(enter)) == bytes([enter])


# -- existing single-byte commands still work -------------------------------- #
def test_single_byte_commands_still_fire():
    d = dec()
    ev = d.feed_bytes(bytes([0x14, ord("x"), 0x12]))   # Ctrl-T, 'x', Ctrl-R
    assert singles(ev) == [0x14, 0x12]
    assert console(ev) == b"x"


# -- interleave: a human keystroke lands inside an agent sequence ------------- #
def test_human_byte_between_prefix_and_selector_aborts_cleanly():
    d = dec()
    # agent: PREFIX 'r' (board_reset). human 'q' (not a selector) slips in after the prefix.
    ev = d.feed_bytes(bytes([PREFIX, ord("q"), ord("r")]))
    # no wrong command; the human 'q' reaches console; the bare 'r' is just a letter.
    assert cmds(ev) == []
    assert console(ev) == b"qr"


def test_human_digit_mid_argument_aborts_without_firing():
    d = dec()
    # PREFIX a 2  then a human 'z' instead of newline: abort, no arm, 'z' to console.
    ev = d.feed_bytes(bytes([PREFIX, ord("a"), ord("2"), ord("z")]))
    assert cmds(ev) == []
    assert console(ev) == b"z"


def test_a_bare_selector_is_just_text():
    """The core safety lock: a selector separated from its prefix can't fire a single-byte command
    because selectors are PRINTABLE and single-byte commands are CONTROL bytes."""
    d = dec()
    ev = d.feed_bytes(b"ar")     # the selector letters, with no prefix
    assert cmds(ev) == [] and singles(ev) == [] and console(ev) == b"ar"


def test_orphaned_prefix_before_real_typing():
    """A lone prefix (e.g. a retransmit that lost its selector) followed by a real command line:
    the prefix is dropped, the line is intact."""
    d = dec()
    ev = d.feed_bytes(bytes([PREFIX]) + b"echo hi\n")
    assert console(ev) == b"echo hi\n" and cmds(ev) == []


# -- the fuzz: ordinary streams NEVER produce a command ---------------------- #
def test_fuzz_streams_without_the_prefix_are_pure_passthrough():
    """The property that matters most: as long as the agent is not injecting, nothing a student
    can type produces a command or loses a byte. ~100k random bytes, prefix excluded."""
    rng = random.Random(1)
    non_prefix = [b for b in range(256) if b != PREFIX]
    for _ in range(2000):
        data = bytes(rng.choice(non_prefix) for _ in range(rng.randint(0, 100)))
        d = dec()
        ev = d.feed_bytes(data)
        assert cmds(ev) == [], "a command fired from prefix-free input"
        got = bytes(e[1] for e in ev if e[0] in ("console", "single"))
        assert got == data, "a byte was lost or reordered"


def test_fuzz_never_crashes_on_arbitrary_bytes():
    rng = random.Random(3)
    for _ in range(2000):
        d = dec()
        d.feed_bytes(bytes(rng.randrange(256) for _ in range(rng.randint(0, 200))))


def test_fuzz_human_bytes_interleaved_ANYWHERE_never_fire_a_wrong_command():
    """The hard case. Splice a random human keystroke at a random position INSIDE a real command's
    byte sequence. Invariant, whatever happens: no command other than the intended one ever fires;
    no spurious single-byte command fires; and the foreign human byte is never lost."""
    rng = random.Random(7)
    intended = "arm_trap"
    foreign = [b for b in range(0x20, 0x7f)
               if chr(b) not in "0123456789" and chr(b) not in COMMANDS]
    for _ in range(20000):
        seq = list(encode(intended, rng.randint(0, 9), prefix=PREFIX))
        hb = rng.choice(foreign)
        pos = rng.randint(0, len(seq))
        stream = bytes(seq[:pos] + [hb] + seq[pos:])
        ev = dec().feed_bytes(stream)
        assert all(name == intended for name, _ in cmds(ev)), (stream, cmds(ev))
        assert singles(ev) == [], ("a spurious single-byte command fired", stream)
        assert hb in (e[1] for e in ev if e[0] == "console"), ("human byte eaten", stream, ev)

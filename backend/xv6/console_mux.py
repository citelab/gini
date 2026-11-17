"""Two-byte console command multiplexer for the xv6 machine's serial console.

The kernel console is one serial stream shared with the human, and GINI signals the kernel with
control bytes — of which the whole space is exhausted (every Ctrl-A..Ctrl-Z that the human does not
type is already bound). This multiplexes a fresh command space behind ONE reserved **prefix** byte:
`PREFIX <letter>` is a command, so every future console command has a home without a new byte.

**This module is both sides of the bridge in pure Python.** `Decoder` is the executable spec for the
kernel's `consoleintr` state machine — the C must behave byte-for-byte identically, and its shape
mirrors the existing `gini_ctl_op` / `gini_shidx` digit-entry machines (all of which already run
under `cons.lock`, so the console path is serialised and this single-threaded model is faithful).
`encode()` is the agent side. See `docs/design/xv6-rebuild-batch.md` §11.

**Status: proven, INERT.** Nothing imports this yet. It lands here first (step 1 of the trap-capture
integration) so the protocol is in the tree and under CI before the agent and kernel are wired to
it. `test_console_mux.py` pins it (round-trip, passthrough, self-escape, interleave-abort, and two
fuzzers — 44k prefix-free and 20k interleave-anywhere).

Two safety locks make it robust against a human typing into the same stream:

  1. **PREFIX is a CONTROL byte; a sub-command SELECTOR is a PRINTABLE letter.** A selector that gets
     separated from its prefix by an interleaved keystroke can never fire a single-byte command — a
     bare 'a' is just text.
  2. **ABORT-AND-REPROCESS.** PREFIX followed by anything unrecognised drops the orphaned prefix and
     handles that byte as if the prefix had never come. The human never types PREFIX, so a dropped
     prefix is always noise; a stray interleave costs one abandoned command (the agent retries),
     never a wrong command fired. (This is the discipline the Ctrl-G shadow-index bug lacked.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The prefix. Ctrl-W (0x17), demoted from shadowdump — a rare diagnostic, low-risk to move. Chosen
#: (not a placeholder): NOT Enter/Tab/LF (CR/LF/TAB — the human types those), NOT ESC (0x1b, the
#: lead of arrow/paste sequences), NOT 0x1e/0x1f (the dump-frame delimiters). Shadowdump's new
#: binding is `PREFIX PREFIX` (Ctrl-W Ctrl-W) — see `demoted` on the Decoder.
DEFAULT_PREFIX = 0x17

#: Sub-command selectors are printable letters. Each maps to (name, takes_int_arg):
#:   `PREFIX <sel>`            -> zero-arg command
#:   `PREFIX <sel> <digits>\n` -> command with one integer argument
#:   `PREFIX PREFIX`           -> the prefix byte's demoted original command (self-escape)
#: New console commands are added HERE and given a matching `case` in the kernel — that is the whole
#: point of the prefix: one edit each side, no new byte.
COMMANDS = {
    "a": ("arm_trap", True),      # PREFIX a <kind>\n   — arm the one-shot trap capture (§11)
    "r": ("board_reset", False),  # PREFIX r            — #4 boardreset, homeless until now
    "s": ("sleep_dump", False),   # PREFIX s            — the Sleep Lab's dump (gini_sleepdump)
    "x": ("sleep_reset", False),  # PREFIX x            — zero the Sleep Lab's counters
}

NEWLINE = 0x0a


def encode(name: str, arg: int | None = None, *, prefix: int = DEFAULT_PREFIX) -> bytes:
    """Agent side: the bytes for one command. Raises on an unknown name or a missing/spurious arg."""
    sel = next((s for s, (n, _) in COMMANDS.items() if n == name), None)
    if sel is None:
        raise KeyError(f"unknown command {name!r}")
    takes_arg = COMMANDS[sel][1]
    out = bytes([prefix, ord(sel)])
    if takes_arg:
        if arg is None:
            raise ValueError(f"{name} needs an integer argument")
        out += str(int(arg)).encode() + bytes([NEWLINE])
    elif arg is not None:
        raise ValueError(f"{name} takes no argument")
    return out


def encode_demoted(prefix: int = DEFAULT_PREFIX) -> bytes:
    """The prefix byte's demoted original command (PREFIX PREFIX). For Ctrl-W this is shadowdump."""
    return bytes([prefix, prefix])


@dataclass
class Decoder:
    """Kernel side: the `consoleintr` state machine, one byte at a time.

    `feed(byte)` / `feed_bytes(data)` return a list of events, each either:
      ("cmd", name, arg)   — a multiplexed command fired (arg is None for a zero-arg command)
      ("single", byte)     — an existing single-byte command fired (still works; the mux is ADDED)
      ("console", byte)    — ordinary console input, passed through to the human
    """
    prefix: int = DEFAULT_PREFIX
    single: frozenset = field(default_factory=frozenset)   # existing single-byte command bytes
    demoted: str = "shadow_dump"    # PREFIX PREFIX fires the prefix byte's OLD single-byte command

    def __post_init__(self):
        self._state = "idle"          # idle | sel | arg
        self._sel = ""
        self._digits = ""

    def feed(self, b: int) -> list:
        out: list = []
        self._step(b, out)
        return out

    def feed_bytes(self, data: bytes) -> list:
        out: list = []
        for b in data:
            self._step(b, out)
        return out

    # -- the state machine (this maps 1:1 to the C) -------------------------- #
    def _step(self, b: int, out: list) -> None:
        st = self._state
        if st == "idle":
            if b == self.prefix:
                self._state = "sel"
            elif b in self.single:
                out.append(("single", b))
            else:
                out.append(("console", b))
            return

        if st == "sel":
            # just consumed a prefix; this byte selects a sub-command.
            if b == self.prefix:                       # PREFIX PREFIX -> demoted original command
                self._state = "idle"
                out.append(("cmd", self.demoted, None))
                return
            sel = chr(b) if 0x20 <= b < 0x7f else None
            if sel in COMMANDS:
                name, takes_arg = COMMANDS[sel]
                if takes_arg:
                    self._state, self._sel, self._digits = "arg", sel, ""
                else:
                    self._state = "idle"
                    out.append(("cmd", name, None))
                return
            # unrecognised selector -> ABORT: drop the orphaned prefix, reprocess this byte as idle.
            self._state = "idle"
            self._step(b, out)
            return

        if st == "arg":
            if 0x30 <= b <= 0x39:                      # a digit
                self._digits += chr(b)
                return
            if b == NEWLINE:                           # clean terminator -> fire
                name = COMMANDS[self._sel][0]
                arg = int(self._digits) if self._digits else 0
                self._state, self._sel, self._digits = "idle", "", ""
                out.append(("cmd", name, arg))
                return
            # any other byte mid-argument -> ABORT: fire nothing, reprocess this byte as idle.
            # (Interleave-robust: a human keystroke landing here does not corrupt into a command.)
            self._state, self._sel, self._digits = "idle", "", ""
            self._step(b, out)
            return

"""What the student ran in gBuilder's terminal, and what it printed.

The gap this closes is one the report named itself: a chain could read "Started the lab" and then
"Witnessed on the running network — none", while the student had in fact pinged across their
subnets and watched it work. The evidence existed and vanished.

Scope, because it is the whole justification: this taps gBuilder's OWN terminal into a container
gBuilder started, opened as part of the assignment with a loud REC indicator running. Not a
machine, not a screen. `proof_events.open_console` carries the same distinction.
"""
from __future__ import annotations

from gini.services.console_tap import ConsoleTap, clean


def _run(tap, keys: bytes, out: bytes = b""):
    tap.key(keys)
    if out:
        tap.output(out)


def test_it_records_what_was_typed_not_what_the_shell_echoed():
    """Reading the output stream back would pick up the prompt, the echo and any redraw — and would
    record a command that was never run when the student edited the line before pressing Enter."""
    t = ConsoleTap("M3")
    t.output(b"lab$ ")                                   # prompt, before anything was run
    t.key(b"pingg")
    t.key(b"\x7f")                                       # they fixed the typo
    t.key(b" -c 1 10.0.2.10\r")
    t.output(b"64 bytes from 10.0.2.10: seq=0 time=0.8 ms\r\n")
    t.flush()
    (rec,) = t.take()
    assert rec == {"device": "M3", "cmd": "ping -c 1 10.0.2.10",
                   "out": ["64 bytes from 10.0.2.10: seq=0 time=0.8 ms"]}


def test_output_before_the_first_command_belongs_to_nothing():
    """The banner and the prompt are not evidence of anything the student did."""
    t = ConsoleTap("M1")
    t.output(b"Welcome to the lab\r\nlab$ ")
    t.flush()
    assert t.take() == []


def test_a_cancelled_line_is_not_a_command():
    """Ctrl-C discards, exactly as the shell does — recording it would put a command in the chain
    that never ran."""
    t = ConsoleTap("M1")
    t.key(b"rm -rf /\x03")
    t.key(b"ls\r")
    t.flush()
    assert [r["cmd"] for r in t.take()] == ["ls"]


def test_a_bare_enter_is_not_a_command():
    t = ConsoleTap("M1")
    t.key(b"\r\r\r")
    t.flush()
    assert t.take() == []


def test_output_is_attributed_to_the_command_that_caused_it():
    t = ConsoleTap("R1")
    _run(t, b"ip route\r", b"default via 10.0.2.1\r\n")
    _run(t, b"ip addr\r", b"inet 10.0.2.1/24\r\n")
    t.flush()
    a, b = t.take()
    assert a["cmd"] == "ip route" and a["out"] == ["default via 10.0.2.1"]
    assert b["cmd"] == "ip addr" and b["out"] == ["inet 10.0.2.1/24"]


def test_long_output_is_truncated_and_says_how_much_was_dropped():
    """`ping -t` says what a marker needs in its first lines and then repeats itself. Truncated
    rather than summarised — guessing which later line mattered would be inventing evidence, and
    hiding the truncation would let a chain imply the output was short."""
    t = ConsoleTap("M2", max_lines=3)
    t.key(b"ping -c 40 10.0.1.1\r")
    t.output(b"".join(b"line %d\r\n" % i for i in range(40)))
    t.flush()
    (rec,) = t.take()
    assert rec["out"][:3] == ["line 0", "line 1", "line 2"]
    assert rec["out"][-1] == "… 37 more line(s)"


def test_escape_sequences_and_control_characters_do_not_reach_the_chain():
    """A recorded line should read as a person saw it, not as the terminal drew it."""
    assert clean(b"\x1b[1;32mok\x1b[0m\r\n") == "ok\n"
    assert clean(b"\x1b]0;title\x07hello") == "hello"
    assert "\x00" not in clean(b"a\x00b")


def test_an_unfinished_command_is_closed_when_the_session_ends():
    """Otherwise the last thing a student ran — often the one that proves it worked — is the one
    that never gets recorded."""
    t = ConsoleTap("M1")
    _run(t, b"ping -c 1 10.0.1.1\r", b"1 packets received\r\n")
    assert t.take() == []                        # not finished: output may still be coming
    t.flush()
    assert [r["cmd"] for r in t.take()] == ["ping -c 1 10.0.1.1"]


def test_a_command_with_no_output_is_still_a_command():
    t = ConsoleTap("M1")
    t.key(b"true\r")
    t.flush()
    (rec,) = t.take()
    assert rec["cmd"] == "true" and rec["out"] == []


# -- escapes that used to survive into a student's proof --------------------- #
#
# Found while chasing a different bug: `[>c[>q` appeared as literal text in a recorded session.
# The parameter class was `[0-9;?]`, which does not cover a PRIVATE parameter — `< = > ?` — so
# tmux's startup queries went unmatched, the ESC was eaten by the control pass, and the rest was
# recorded as though the student had typed it.

def test_a_private_parameter_sequence_is_not_left_as_text():
    """tmux sends both of these on every session: device attributes and XTVERSION."""
    assert clean(b"\x1b[>c\x1b[>qM1:/app# ") == "M1:/app# "
    assert clean(b"\x1b[?1000h\x1b[?1006l") == ""
    assert clean(b"\x1b[<0;1;1M") == "", "an SGR mouse report is not output either"


def test_a_charset_designation_is_not_left_as_text():
    """`ESC ( B` — select ASCII — left `(B` behind."""
    assert clean(b"\x1b(Bhello") == "hello"
    assert clean(b"\x1b)0\x1b*Bworld") == "world"


def test_the_keypad_and_cursor_save_escapes_go_too():
    """The old single-character class ran @ to _, missing these."""
    assert clean(b"\x1b=on\x1b>off") == "onoff"
    assert clean(b"\x1b7saved\x1b8") == "saved"


def test_a_device_control_string_is_removed_whole():
    """tmux answers XTVERSION with a DCS. Only the introducer was stripped before, so the
    version banner landed in the transcript."""
    assert clean(b"\x1bP>|tmux 3.3a\x1b\\ready") == "ready"


def test_ordinary_text_is_untouched():
    """The widening must not eat output. ESC is only ever an escape; a bare `>` is not."""
    assert clean(b"64 bytes from M2 (10.0.4.10): icmp_seq=1 ttl=61 time=2.50 ms") == (
        "64 bytes from M2 (10.0.4.10): icmp_seq=1 ttl=61 time=2.50 ms")
    assert clean(b"cat < in > out; test 2>&1 [ok]") == "cat < in > out; test 2>&1 [ok]"
    assert clean(b" 1  R1 (10.0.3.1)  1.271 ms") == " 1  R1 (10.0.3.1)  1.271 ms"

"""Setting up a GINI32 board over USB from inside gBuilder.

These tests drive the real console driver against a *fake board on a real pty*,
so the serial path — raw termios, echo, prompts, the reset noise a board emits
when the port is opened — is genuinely exercised rather than mocked away.
"""
import os
import pty
import re
import threading
import time

import pytest

from gini.services import boardsetup as bs


class FakeBoard:
    """A scripted gBridge console on the far end of a pty.

    Mimics the firmware closely on the details that trip parsers up: it echoes
    what it receives, prints a boot log before the banner (a real board reboots
    when the port is opened), and answers `show` in the firmware's exact layout.
    """

    SHOW = ("  ssid    {ssid}\r\n"
            "  pass    {pw}\r\n"
            "  server  {server}\r\n"
            "  id      {bid}\r\n"
            "  apssid  GINI32\r\n"
            "  appass  (set)\r\n"
            "  apchan  6\r\n")

    def __init__(self, *, boot_noise=True, respond=True, ssid="lab-wifi",
                 has_pw=True, bid="gini32-1", server="auto",
                 reject: str | None = None, save_fails=False):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        # A fresh pty has ECHO on, so the line discipline would bounce everything we
        # write straight back at us and the fake would read its own boot log as a
        # command. Real serial hardware does no such thing. (The driver also sets raw
        # mode, but only once it opens — this covers the window before that.)
        import termios
        a = termios.tcgetattr(self.slave)
        a[3] &= ~(termios.ECHO | termios.ECHOE | termios.ECHONL | termios.ICANON)
        a[0] &= ~(termios.ICRNL | termios.INLCR)
        a[1] &= ~termios.OPOST
        termios.tcsetattr(self.slave, termios.TCSANOW, a)
        self.boot_noise = boot_noise
        self.respond = respond
        self.state = {"ssid": ssid, "pw": "(set)" if has_pw else "(empty)",
                      "bid": bid, "server": server}
        self.reject = reject          # a `set <key>` the firmware refuses
        self.save_fails = save_fails
        self.saved = False
        self.rebooted = False
        self.commands: list[str] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _w(self, s: str) -> None:
        try:
            os.write(self.master, s.encode())
        except OSError:
            pass

    def _run(self) -> None:
        if self.boot_noise:
            self._w("\r\nrst:0x1 (POWERON_RESET),boot:0x13\r\n"
                    "I (312) gbridge: GINI32 gBridge starting\r\n")
        if not self.respond:
            return                                     # a non-GINI serial device
        self._w("\r\ngBridge console -- type 'help'\r\ngini> ")
        buf = ""
        while not self._stop.is_set():
            try:
                data = os.read(self.master, 1024)
            except OSError:
                return
            if not data:
                return
            text = data.decode("utf-8", "replace")
            self._w(text)                              # boards echo
            buf += text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self._handle(line.strip("\r "))

    def _handle(self, line: str) -> None:
        if not line:
            self._w("gini> ")
            return
        self.commands.append(line)
        if line == "show":
            self._w("\r\n" + self.SHOW.format(**self.state))
        elif line.startswith("set "):
            bits = line.split(None, 2)            # "set", key, value
            key = bits[1] if len(bits) > 1 else ""
            val = bits[2] if len(bits) > 2 else ""
            if key == self.reject:
                self._w("\r\nunknown key: %s\r\n" % key)
            else:
                if key == "ssid":
                    self.state["ssid"] = val
                elif key == "pass":
                    self.state["pw"] = "(set)"
                elif key == "id":
                    self.state["bid"] = val
                elif key == "server":
                    self.state["server"] = val
                self._w("\r\nok (use 'save' to persist)\r\n")
        elif line == "save":
            if self.save_fails:
                self._w("\r\nsave FAILED\r\n")
            else:
                self.saved = True
                self._w("\r\nsaved (reboot to apply Wi-Fi changes)\r\n")
        elif line == "reboot":
            self.rebooted = True
            self._w("\r\nrebooting\r\n")
            return                                     # no prompt: it is gone
        else:
            self._w("\r\nunknown command: %s (try 'help')\r\n" % line)
        self._w("gini> ")

    def close(self) -> None:
        self._stop.set()
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def board():
    b = FakeBoard()
    yield b
    b.close()


# ------------------------------------------------------------------ discovery

def test_list_ports_never_raises_and_skips_noise():
    """Runs on whatever machine CI happens to be; it must degrade, not explode."""
    ports = bs.list_ports()
    assert isinstance(ports, list)
    assert not [p for p in ports if "Bluetooth" in p.device]


def test_bluetooth_and_debug_nodes_are_filtered():
    assert bs._NOISE.search("/dev/cu.Bluetooth-Incoming-Port")
    assert bs._NOISE.search("/dev/cu.debug-console")
    assert not bs._NOISE.search("/dev/cu.usbserial-0001")


def test_candidate_patterns_cover_the_common_esp32_bridges():
    """CP210x, CH340, FTDI and native USB-JTAG are what these boards actually ship."""
    joined = " ".join(bs._PATTERNS)
    for frag in ("usbserial", "SLAB_USBtoUART", "wchusbserial", "usbmodem",
                 "ttyUSB", "ttyACM"):
        assert frag in joined
    # macOS: never the tty.* twin — opening it blocks forever waiting for carrier
    assert not any(p.startswith("/dev/tty.") for p in bs._PATTERNS)


# -------------------------------------------------------------------- talking

def test_identify_reads_the_boards_settings_through_the_boot_noise(board):
    with bs.BoardConsole(board.port) as con:
        info = con.identify()
    assert info is not None
    assert info.board_id == "gini32-1"
    assert info.ssid == "lab-wifi"
    assert info.has_password is True
    assert info.server == "auto"
    assert info.ap_ssid == "GINI32"
    assert info.configured is True


def test_a_board_with_no_password_reads_as_unconfigured():
    b = FakeBoard(has_pw=False, ssid="")
    try:
        with bs.BoardConsole(b.port) as con:
            info = con.identify()
        assert info is not None and info.configured is False
    finally:
        b.close()


def test_a_serial_device_that_is_not_a_board_identifies_as_none():
    """A USB-serial adapter is not evidence of firmware — we must ask, not assume."""
    b = FakeBoard(respond=False)
    try:
        with bs.BoardConsole(b.port) as con:
            con.wait_for_prompt = lambda timeout=0.4: False   # keep the test quick
            assert con.identify() is None
    finally:
        b.close()


def test_apply_writes_every_setting_saves_and_reboots(board):
    with bs.BoardConsole(board.port) as con:
        assert con.wait_for_prompt()
        ok, msg = con.apply(ssid="dept-wifi", password="hunter2", board_id="gini-7")
    assert ok, msg
    assert board.saved and board.rebooted
    assert "set ssid dept-wifi" in board.commands
    assert "set pass hunter2" in board.commands
    assert "set id gini-7" in board.commands
    assert board.commands.index("save") > board.commands.index("set id gini-7")
    assert board.state["ssid"] == "dept-wifi" and board.state["bid"] == "gini-7"


def test_apply_reports_failure_when_the_board_rejects_a_setting():
    """Silently 'succeeding' would send a student debugging the network instead."""
    b = FakeBoard(reject="id")
    try:
        with bs.BoardConsole(b.port) as con:
            assert con.wait_for_prompt()
            ok, msg = con.apply("lab", "pw", "gini-2")
        assert not ok
        assert "set id" in msg
        assert not b.saved and not b.rebooted
    finally:
        b.close()


def test_apply_reports_failure_when_the_save_does_not_stick():
    b = FakeBoard(save_fails=True)
    try:
        with bs.BoardConsole(b.port) as con:
            assert con.wait_for_prompt()
            ok, msg = con.apply("lab", "pw", "gini-2")
        assert not ok and "save" in msg.lower()
        assert not b.rebooted
    finally:
        b.close()


def test_settings_survive_a_reopen(board):
    """What we wrote must be what the next conversation reads back."""
    with bs.BoardConsole(board.port) as con:
        assert con.wait_for_prompt()
        ok, _ = con.apply("newnet", "pw", "gini-9", reboot=False)
    assert ok
    with bs.BoardConsole(board.port) as con:
        info = con.identify()
    assert info.ssid == "newnet" and info.board_id == "gini-9"


def test_command_strips_the_echo_and_the_prompt(board):
    with bs.BoardConsole(board.port) as con:
        assert con.wait_for_prompt()
        out = con.command("show")
    assert bs.PROMPT not in out
    assert not out.startswith("show")
    assert "ssid" in out


def test_wait_for_prompt_gives_up_rather_than_hanging():
    """A dead device must fail fast; the UI thread cannot afford an unbounded wait."""
    b = FakeBoard(respond=False, boot_noise=False)
    try:
        with bs.BoardConsole(b.port) as con:
            t0 = time.monotonic()
            assert con.wait_for_prompt(timeout=1.0) is False
            assert time.monotonic() - t0 < 4.0
    finally:
        b.close()


def test_detect_boards_separates_boards_from_other_devices():
    good, bad = FakeBoard(bid="gini-1"), FakeBoard(respond=False, boot_noise=False)
    try:
        boards, others = bs.detect_boards([bs.PortInfo(good.port), bs.PortInfo(bad.port)])
        assert [b.board_id for b in boards] == ["gini-1"]
        assert [o.device for o in others] == [bad.port]
    finally:
        good.close()
        bad.close()


# ---------------------------------------------------------------- naming help

@pytest.mark.parametrize("existing,expected", [
    ([], "gini-1"),
    (["gini-1"], "gini-2"),
    (["gini-1", "gini-2", "gini-3"], "gini-4"),
    (["gini-2"], "gini-1"),                      # fills the gap
    (["something-else"], "gini-1"),              # ignores foreign names
    (["gini-1", "GINI-1 "], "gini-2"),           # tolerant of stray whitespace
])
def test_suggest_board_id_never_repeats(existing, expected):
    assert bs.suggest_board_id(existing) == expected


def test_port_label_is_readable():
    assert bs.PortInfo("/dev/cu.usbserial-1").label == "/dev/cu.usbserial-1"
    assert "CP2102" in bs.PortInfo("/dev/cu.x", "CP2102 USB to UART").label

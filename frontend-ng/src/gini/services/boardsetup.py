"""Set up a GINI32 board over USB, from inside gBuilder.

A board needs exactly one thing it cannot get over the air: the lab Wi-Fi
credentials. Everything else — its address, gateway, hotspot SSID, physical
subnet — comes from the canvas once it is online. So this module exists to carry
that one secret across the wire, and then get out of the way: a student touches
USB once per board, ever.

The transport is the board's own serial console (`gini> `), driven exactly as a
human would drive it. That means no second protocol to keep in sync with the
firmware — if a command works when typed, it works here.

Standard library only. `pyserial` is used *if* it happens to be importable
because it enumerates ports more informatively, but it is not required and is
not a declared dependency: gBuilder is installed by students, and this must work
on a machine with nothing extra on it.
"""
from __future__ import annotations

import glob
import os
import re
import sys
import time
from dataclasses import dataclass, field

BAUD = 115200
PROMPT = "gini> "
BANNER = "gBridge console"

# How long to wait for the prompt after opening. Opening the port pulls DTR/RTS,
# which on most dev boards is wired to EN/BOOT and reboots the ESP32 — so the
# first thing we see is a boot log, and the prompt only arrives after it.
BOOT_WAIT = 6.0
CMD_WAIT = 3.0

# Serial devices that are worth *asking* whether they are a board. On macOS we
# must use the `cu.*` ("call-up") node: opening `tty.*` blocks until carrier
# detect, which for a USB adapter never comes, and the app would hang on open.
_PATTERNS = (
    "/dev/cu.usbserial*",      # FTDI
    "/dev/cu.SLAB_USBtoUART*", # CP210x (many ESP32 devkits)
    "/dev/cu.wchusbserial*",   # CH340 (very common on cheap boards)
    "/dev/cu.usbmodem*",       # native USB-JTAG (ESP32-S3/C3)
    "/dev/ttyUSB*",            # Linux
    "/dev/ttyACM*",            # Linux
)

# Never a board, and present in numbers: macOS ships Bluetooth/debug nodes, and a
# Linux box typically exposes 32 legacy motherboard UARTs (/dev/ttyS0..31). Listing
# those would bury the one device the student actually plugged in.
_NOISE = re.compile(r"(Bluetooth|debug-console|wlan-debug)", re.I)
_NEVER = re.compile(r"^/dev/ttyS\d+$")


def _plausible(device: str) -> bool:
    """Could this device be a board? Cheap name test, before we try talking to it."""
    import fnmatch
    if _NOISE.search(device) or _NEVER.match(device):
        return False
    return any(fnmatch.fnmatch(device, pat) for pat in _PATTERNS)


@dataclass
class PortInfo:
    device: str
    description: str = ""

    @property
    def label(self) -> str:
        return f"{self.device}  —  {self.description}" if self.description else self.device


@dataclass
class BoardInfo:
    """What a board says about itself, from `show`."""
    port: str
    board_id: str = ""
    ssid: str = ""
    has_password: bool = False
    server: str = ""
    ap_ssid: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        """Has this board been set up before? (A fresh one still has the default.)"""
        return bool(self.ssid) and self.has_password


def list_ports() -> list[PortInfo]:
    """Serial devices that could plausibly be a board, best-effort and never raising.

    Uses pyserial when available (it knows the USB descriptor strings, which make
    the picker much friendlier); otherwise falls back to globbing the device names.
    """
    try:
        from serial.tools import list_ports as _lp        # type: ignore
        out = []
        for p in _lp.comports():
            if sys.platform == "darwin" and "/tty." in p.device:
                continue                                   # prefer the cu.* twin
            if not _plausible(p.device):
                continue                                   # ttyS0..31, Bluetooth, …
            out.append(PortInfo(p.device, (p.description or "").strip()))
        if out:
            return sorted(out, key=lambda p: p.device)
    except Exception:
        pass                                               # fall through to globbing

    seen: list[PortInfo] = []
    for pat in _PATTERNS:
        for dev in glob.glob(pat):
            if _plausible(dev):
                seen.append(PortInfo(dev))
    return sorted({p.device: p for p in seen}.values(), key=lambda p: p.device)


class BoardConsole:
    """A conversation with one board over its serial console.

    Deliberately dumb: write a line, read until the prompt comes back. The board
    echoes what it receives, so every reply contains the command itself; callers
    get the text with that echo stripped.
    """

    def __init__(self, port: str, baud: int = BAUD) -> None:
        self.port = port
        self.baud = baud
        self.fd = -1
        self.transcript = ""       # everything read, for diagnostics

    # -- lifecycle -- #

    def open(self) -> None:
        # O_NONBLOCK so the open itself cannot hang even on a misbehaving device;
        # O_NOCTTY so this never becomes our controlling terminal.
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure()
        except Exception:
            self.close()
            raise

    def _configure(self) -> None:
        """Raw mode at the board's baud rate: no echo, no line editing, 8N1."""
        import termios
        attrs = termios.tcgetattr(self.fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        # no translation, no flow control, no parity/canonical/echo
        iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY | termios.ICRNL
                   | termios.INLCR | termios.IGNCR | termios.ISTRIP | termios.INPCK)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHOE | termios.ECHONL
                   | termios.ICANON | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL   # CLOCAL: ignore carrier
        speed = getattr(termios, f"B{self.baud}", termios.B115200)
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1

    def __enter__(self) -> "BoardConsole":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- raw io -- #

    def _read(self, timeout: float) -> str:
        import select
        try:
            r, _, _ = select.select([self.fd], [], [], timeout)
        except (OSError, ValueError):
            return ""
        if not r:
            return ""
        try:
            data = os.read(self.fd, 4096)
        except (BlockingIOError, OSError):
            return ""
        text = data.decode("utf-8", "replace")
        self.transcript += text
        return text

    def _write(self, text: str) -> None:
        data = text.encode("utf-8")
        while data:
            try:
                n = os.write(self.fd, data)
                data = data[n:]
            except BlockingIOError:
                time.sleep(0.01)

    def read_until(self, needle: str, timeout: float) -> str:
        """Accumulate until `needle` appears. Returns what was read (may lack it)."""
        buf = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            buf += self._read(min(0.25, max(0.01, deadline - time.monotonic())))
            if needle in buf:
                break
        return buf

    # -- protocol -- #

    def wait_for_prompt(self, timeout: float = BOOT_WAIT) -> bool:
        """Get the board to a prompt.

        Opening the port usually reset it, so first just listen for the banner;
        if the board was already up (no reset) a bare newline draws the prompt.
        """
        if PROMPT in self.read_until(PROMPT, min(2.0, timeout)):
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._write("\r\n")
            if PROMPT in self.read_until(PROMPT, 1.0):
                return True
        return False

    def command(self, line: str, timeout: float = CMD_WAIT) -> str:
        """Send one command; return its output without the echo or the next prompt."""
        self._write(line + "\r\n")
        out = self.read_until(PROMPT, timeout)
        # the board echoes what we typed; drop that first line
        if line in out:
            out = out.split(line, 1)[1]
        return out.replace(PROMPT, "").strip("\r\n \t")

    def identify(self) -> BoardInfo | None:
        """Confirm this really is a GINI32 board and read its current settings.

        Returns None for anything that does not answer like one — a USB-serial
        adapter is not evidence of firmware, so we ask rather than assume.
        """
        if not self.wait_for_prompt():
            return None
        out = self.command("show")
        if not out:
            return None
        info = BoardInfo(port=self.port)
        found = 0
        for raw in out.splitlines():
            parts = raw.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, val = parts[0].strip(), parts[1].strip()
            found += 1
            if key == "id":
                info.board_id = val
            elif key == "ssid":
                info.ssid = val
            elif key == "pass":
                info.has_password = val != "(empty)"
            elif key == "server":
                info.server = val
            elif key == "apssid":
                info.ap_ssid = val
            else:
                info.extra[key] = val
        # `show` always prints several keys; anything less is not our console
        if found < 3 or (not info.board_id and not info.ssid):
            return None
        return info

    def apply(self, ssid: str, password: str, board_id: str,
              server: str = "auto", reboot: bool = True) -> tuple[bool, str]:
        """Write the settings a board cannot discover, persist them, and restart.

        Returns (ok, message). Every step is checked: a `set` that the firmware
        rejected must not be reported as success, or a student ends up hunting a
        network fault that is really a typo.
        """
        steps = [("ssid", ssid), ("pass", password), ("id", board_id)]
        if server:
            steps.append(("server", server))
        for key, val in steps:
            if val is None:
                continue
            out = self.command(f"set {key} {val}")
            if "ok" not in out.lower():
                return False, f"the board did not accept `set {key}`: {out.strip()[:120]}"
        out = self.command("save", timeout=5.0)
        if "saved" not in out.lower():
            return False, f"the board did not save its settings: {out.strip()[:120]}"
        if reboot:
            self._write("reboot\r\n")
            time.sleep(0.3)          # it is going away; nothing useful to read back
        return True, f"'{board_id}' set up for network '{ssid}'"


def detect_boards(ports: list[PortInfo] | None = None) -> tuple[list[BoardInfo], list[PortInfo]]:
    """Ask every candidate port whether it is a board.

    Returns (boards, others) so the UI can both offer the real boards and say
    something useful about the serial devices that did not answer.
    """
    boards: list[BoardInfo] = []
    others: list[PortInfo] = []
    for p in (ports if ports is not None else list_ports()):
        try:
            with BoardConsole(p.device) as con:
                info = con.identify()
        except (OSError, ImportError):
            info = None
        if info is not None:
            boards.append(info)
        else:
            others.append(p)
    return boards, others


def suggest_board_id(existing: list[str]) -> str:
    """Next free `gini-N`, so a TA setting up a stack of boards never repeats one."""
    used = set()
    for e in existing:
        m = re.fullmatch(r"gini-(\d+)", (e or "").strip())
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"gini-{n}"

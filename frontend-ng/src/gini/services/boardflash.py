"""Put firmware on a GINI32 board over USB, from inside gBuilder.

This closes a gap that made the Hardware menu untrue. `boardsetup` drives the board's
`gini> ` console — which only exists once firmware is already on the board. So a student
holding a *virgin* board could not start from gBuilder at all: they needed a terminal,
a 2 GB ESP-IDF install and the `gini32` CLI. Flashing is the missing first step.

**Why flashing can live here but building cannot.** Building needs ESP-IDF: the whole
toolchain, `export.sh` sourced into the environment, a per-chip `set-target`, and network
access for the managed components. That is an instructor-grade install and no UI hides
it. Flashing needs only `esptool` — pure Python, no toolchain — plus ~870 KB of prebuilt
images. It is also the right split for the architecture: the firmware is built ONCE and
is identical on every board, so building is a rare developer act while flashing is a
routine student one.

**The offsets are load-bearing — do not merge the images.** The flash layout is::

    0x00000  bootloader.bin
    0x08000  partition-table.bin
    0x09000  nvs            <-- the board's identity: id, lab Wi-Fi, owner, LED pin
    0x10000  gbridge.bin    (the app)

`esptool merge_bin` would produce one image from 0x0 and pad the gaps with 0xFF, which
runs straight over NVS at 0x9000 and silently unpairs the board, forgets its id and
forgets the lab Wi-Fi. Writing the three files at their own offsets leaves 0x9000
untouched, so re-flashing a board that is already set up keeps it set up. For the same
reason there is deliberately no `erase_flash` here.

Standard library plus `esptool`, invoked as a subprocess rather than imported: esptool's
Python API has changed shape across major versions, while its command line has been
stable for years, and a subprocess cannot take the GUI down with it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# (offset, filename) — see the module docstring on why these are separate writes.
IMAGES: tuple[tuple[int, str], ...] = (
    (0x0000, "bootloader.bin"),
    (0x8000, "partition-table.bin"),
    (0x10000, "gbridge.bin"),
)

# NVS lives here. Nothing in this module may write at or across it.
NVS_OFFSET = 0x9000

# esptool reports chips in human form ("ESP32-S3"); the build tree names them the way
# `idf.py set-target` does ("esp32s3"). One canonical direction, computed not hardcoded.
def _canonical_target(chip: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (chip or "").strip().lower())


def firmware_root() -> Path:
    """Where prebuilt images live: ``<repo>/backend/gini32/firmware``.

    Same repo-relative convention the orchestrator uses to find the backend, so a source
    checkout works with no configuration. Override with GINI_FIRMWARE_DIR (a packaged
    build, or an instructor pointing a lab at a freshly built image).
    """
    env = os.environ.get("GINI_FIRMWARE_DIR")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[4] / "backend" / "gini32" / "firmware"


@dataclass
class Firmware:
    """A complete, flashable set of images for one chip."""
    target: str
    directory: Path
    files: list[tuple[int, Path]] = field(default_factory=list)
    build: str = ""            # the GB_BUILD marker read out of the app image

    @property
    def total_bytes(self) -> int:
        return sum(p.stat().st_size for _, p in self.files if p.exists())


def read_build_marker(app_bin: Path) -> str:
    """Pull the GB_BUILD string out of a built app image.

    A shipped binary can drift from the source beside it, and a stale flash masquerading
    as a fresh one has already cost this project two debugging sessions. The firmware
    stamps GB_BUILD into its boot log; finding the same string here lets the UI say
    exactly what it is about to install, and lets a human compare the two.
    """
    try:
        blob = app_bin.read_bytes()
    except OSError:
        return ""
    m = re.search(rb"gbridge-\d+ \([^)]{0,40}\)", blob)
    return m.group(0).decode("ascii", "replace") if m else ""


def available(target: str, root: Path | None = None) -> Firmware | None:
    """The flashable image set for `target`, or None if it is not shipped."""
    root = root or firmware_root()
    d = root / _canonical_target(target)
    if not d.is_dir():
        return None
    files = [(off, d / name) for off, name in IMAGES]
    if not all(p.is_file() for _, p in files):
        return None                       # a partial set is worse than none
    app = d / IMAGES[-1][1]
    return Firmware(target=_canonical_target(target), directory=d, files=files,
                    build=read_build_marker(app))


def available_targets(root: Path | None = None) -> list[str]:
    root = root or firmware_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and available(d.name, root) is not None)


# --------------------------------------------------------------------- esptool

def esptool_argv() -> list[str]:
    """How to invoke esptool. ``-m esptool`` uses the SAME interpreter running gBuilder,
    so a virtualenv install is found without depending on PATH."""
    return [sys.executable, "-m", "esptool"]


def esptool_available(run=None) -> bool:
    run = run or _run
    try:
        rc, _ = run(esptool_argv() + ["version"], timeout=20)
        return rc == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run(argv: list[str], timeout: float = 300.0) -> tuple[int, str]:
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace")


_CHIP_RE = re.compile(r"(ESP32(?:-[A-Z0-9]+)?)\b")


def detect_chip(port: str, run=None) -> str:
    """Ask the board what it is, so we flash an image built for that chip.

    Returns a canonical target ("esp32s3") or "" if nothing answered. Flashing an image
    built for the wrong chip produces a board that is bricked-looking rather than
    obviously wrong — it boots into a reset loop with no console — so this is checked
    rather than assumed.
    """
    run = run or _run
    try:
        rc, out = run(esptool_argv() + ["--port", port, "chip_id"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    if rc != 0:
        return ""
    # "Detecting chip type... ESP32-S3" / "Chip is ESP32-S3 (revision v0.2)"
    for line in out.splitlines():
        if "chip" not in line.lower():
            continue
        m = _CHIP_RE.search(line)
        if m:
            return _canonical_target(m.group(1))
    return ""


@dataclass
class FlashResult:
    ok: bool
    message: str
    output: str = ""
    build: str = ""


def flash(port: str, firmware: Firmware, baud: int = 460800,
          run=None, on_progress=None) -> FlashResult:
    """Write the three images at their own offsets. NVS is not touched.

    `on_progress` is called with short human sentences; the caller decides where they go.
    """
    run = run or _run
    say = on_progress or (lambda _m: None)

    missing = [str(p) for _, p in firmware.files if not p.is_file()]
    if missing:
        return FlashResult(False, f"firmware image missing: {', '.join(missing)}")

    # Belt and braces: if anyone ever edits IMAGES, catch an overlap with NVS here
    # rather than discovering it as boards mysteriously forgetting who they are.
    for off, path in firmware.files:
        size = path.stat().st_size
        if off < NVS_OFFSET < off + size:
            return FlashResult(
                False,
                f"{path.name} at 0x{off:x} would run over NVS at 0x{NVS_OFFSET:x} — "
                f"that would erase the board's identity, so refusing to flash.")

    argv = esptool_argv() + ["--chip", firmware.target, "--port", port,
                            "--baud", str(baud), "write_flash"]
    for off, path in firmware.files:
        argv += [hex(off), str(path)]

    say(f"writing {firmware.total_bytes // 1024} KB to {port} …")
    try:
        rc, out = run(argv, timeout=600)
    except subprocess.TimeoutExpired:
        return FlashResult(False, "esptool did not finish — is the board still plugged in?")
    except FileNotFoundError:
        return FlashResult(False, "esptool is not installed (pip install esptool)")
    except (OSError, subprocess.SubprocessError) as exc:
        return FlashResult(False, f"could not run esptool: {exc}")

    if rc != 0:
        return FlashResult(False, _explain_failure(out), output=out)
    return FlashResult(True, f"flashed {firmware.build or 'firmware'} — "
                             f"the board keeps its id and lab Wi-Fi",
                       output=out, build=firmware.build)


_HINTS = (
    ("Failed to connect", "the board did not answer. Hold BOOT, tap RESET, release BOOT, "
                          "then try again — some boards cannot be reset over USB alone."),
    ("Permission denied", "no permission for the serial port. On Linux: "
                          "`sudo usermod -aG dialout $USER`, then log out and back in."),
    ("could not open port", "the port is busy — close any serial monitor "
                            "(including gBuilder's own Set Up a Board) and retry."),
    ("Resource busy", "the port is busy — close any serial monitor and retry."),
    ("does not match", "this image was built for a different chip than the board reports."),
)


def _explain_failure(out: str) -> str:
    """Turn esptool's output into one sentence a student can act on.

    Every failure here is physical — a cable, a button, a permission — and the raw output
    buries that under a stack trace.
    """
    for needle, hint in _HINTS:
        if needle.lower() in out.lower():
            return hint
    tail = [l for l in out.strip().splitlines() if l.strip()]
    return tail[-1] if tail else "esptool failed with no output"

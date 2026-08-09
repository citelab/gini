"""Flashing a GINI32 board from inside gBuilder.

The property that matters most here is NEGATIVE: flashing must not erase the board's
identity. Everything else is recoverable by trying again; wiping NVS silently unpairs
the board, forgets its id and forgets the lab Wi-Fi, and the student is left with
hardware that looks broken.
"""
import subprocess

import pytest

from gini.services import boardflash as bf


@pytest.fixture
def firmware(tmp_path):
    """A complete, plausible image set — sizes chosen to match the real build."""
    d = tmp_path / "esp32s3"
    d.mkdir()
    (d / "bootloader.bin").write_bytes(b"\x00" * 21008)
    (d / "partition-table.bin").write_bytes(b"\x00" * 3072)
    (d / "gbridge.bin").write_bytes(b"x" * 4096 + b"gbridge-17 (blink+ssid)" + b"y" * 4096)
    return tmp_path


# ------------------------------------------------------------------ discovery

def test_finds_a_complete_image_set_and_reads_its_build_marker(firmware):
    fw = bf.available("esp32s3", firmware)
    assert fw is not None
    assert fw.target == "esp32s3"
    # The marker is what stops a stale shipped binary masquerading as a fresh one.
    assert fw.build == "gbridge-17 (blink+ssid)"
    assert bf.available_targets(firmware) == ["esp32s3"]


def test_a_partial_image_set_is_not_offered(firmware):
    """Half a firmware is worse than none: it flashes, then bricks the board."""
    (firmware / "esp32s3" / "bootloader.bin").unlink()
    assert bf.available("esp32s3", firmware) is None
    assert bf.available_targets(firmware) == []


def test_unknown_chip_is_simply_absent(firmware):
    assert bf.available("esp32c6", firmware) is None


@pytest.mark.parametrize("reported,expected", [
    ("ESP32-S3", "esp32s3"), ("esp32s3", "esp32s3"),
    ("ESP32-C3", "esp32c3"), ("ESP32", "esp32"),
])
def test_chip_names_normalise_to_build_targets(reported, expected):
    """esptool says "ESP32-S3"; the build tree says "esp32s3". One canonical direction."""
    assert bf._canonical_target(reported) == expected


# ------------------------------------------------- the thing that must not break

def test_the_images_never_span_the_nvs_partition(firmware):
    """THE load-bearing test.

    NVS lives at 0x9000, between the partition table and the app. A merged single-image
    flash from 0x0 pads the gaps with 0xFF and runs straight over it — which silently
    unpairs the board and forgets its id and lab Wi-Fi. Writing the three files at their
    own offsets is what keeps 0x9000 intact, and this asserts the offsets still do.
    """
    fw = bf.available("esp32s3", firmware)
    for off, path in fw.files:
        end = off + path.stat().st_size
        assert not (off < bf.NVS_OFFSET < end), (
            f"{path.name} at 0x{off:x}..0x{end:x} covers NVS at 0x{bf.NVS_OFFSET:x}")


def test_flash_refuses_when_an_image_would_reach_nvs(firmware):
    """If anyone ever edits the offsets, fail loudly instead of eating boards."""
    fw = bf.available("esp32s3", firmware)
    # Grow the partition table until it runs into NVS.
    off, path = fw.files[1]
    path.write_bytes(b"\x00" * 0x2000)          # 0x8000 + 0x2000 = 0xA000 > 0x9000
    res = bf.flash("/dev/null", fw, run=lambda *a, **k: (0, ""))
    assert not res.ok
    assert "NVS" in res.message and "refusing" in res.message


def test_flash_does_not_erase(firmware):
    """`erase_flash` would take NVS with it, so it must never appear."""
    fw = bf.available("esp32s3", firmware)
    seen = {}

    def fake_run(argv, timeout=0):
        seen["argv"] = argv
        return 0, "Hash of data verified."

    assert bf.flash("/dev/ttyUSB0", fw, run=fake_run).ok
    argv = seen["argv"]
    assert "erase_flash" not in argv
    assert "write_flash" in argv
    # each image at its own offset, in order
    assert argv[argv.index("write_flash") + 1] == "0x0"
    assert "0x8000" in argv and "0x10000" in argv


def test_flash_reports_the_build_it_installed(firmware):
    fw = bf.available("esp32s3", firmware)
    res = bf.flash("/dev/ttyUSB0", fw, run=lambda *a, **k: (0, "ok"))
    assert res.ok and res.build == "gbridge-17 (blink+ssid)"


# ------------------------------------------------------------------- failures

@pytest.mark.parametrize("output,needle", [
    ("A fatal error occurred: Failed to connect to ESP32-S3", "BOOT"),
    ("could not open port /dev/ttyUSB0: Permission denied", "dialout"),
    ("serial.serialutil.SerialException: could not open port", "busy"),
])
def test_esptool_failures_become_something_a_student_can_act_on(firmware, output, needle):
    """Every failure here is physical — a cable, a button, a permission. The raw
    traceback buries that, and a student cannot act on a stack trace."""
    fw = bf.available("esp32s3", firmware)
    res = bf.flash("/dev/ttyUSB0", fw, run=lambda *a, **k: (1, output))
    assert not res.ok
    assert needle.lower() in res.message.lower()


def test_a_missing_esptool_is_reported_not_raised(firmware):
    fw = bf.available("esp32s3", firmware)

    def boom(*a, **k):
        raise FileNotFoundError("no esptool")

    res = bf.flash("/dev/ttyUSB0", fw, run=boom)
    assert not res.ok and "pip install esptool" in res.message


def test_a_hung_esptool_is_reported_not_raised(firmware):
    fw = bf.available("esp32s3", firmware)

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("esptool", 600)

    res = bf.flash("/dev/ttyUSB0", fw, run=hang)
    assert not res.ok and "still plugged in" in res.message


def test_detect_chip_survives_a_port_that_says_nothing():
    assert bf.detect_chip("/dev/ttyUSB0", run=lambda *a, **k: (1, "")) == ""
    assert bf.detect_chip("/dev/ttyUSB0", run=lambda *a, **k: (0, "no idea")) == ""


def test_detect_chip_reads_esptools_usual_wording():
    out = ("esptool.py v4.7.0\nSerial port /dev/ttyUSB0\nConnecting....\n"
           "Detecting chip type... ESP32-S3\nChip is ESP32-S3 (revision v0.2)\n")
    assert bf.detect_chip("/dev/ttyUSB0", run=lambda *a, **k: (0, out)) == "esp32s3"


def test_esptool_is_invoked_through_this_interpreter():
    """`-m esptool` finds a virtualenv install without depending on PATH."""
    assert bf.esptool_argv()[1:] == ["-m", "esptool"]


# ------------------------------------------------------ what we actually ship

def test_the_repo_ships_a_flashable_image_for_the_supported_board():
    """The whole point is that a student needs no ESP-IDF. If this set goes missing,
    Flash a Board silently degrades to 'no firmware available'."""
    fw = bf.available("esp32s3")
    assert fw is not None, "backend/gini32/firmware/esp32s3 is missing or incomplete"
    assert fw.build.startswith("gbridge-"), "shipped image has no GB_BUILD marker"
    for off, path in fw.files:
        assert not (off < bf.NVS_OFFSET < off + path.stat().st_size)

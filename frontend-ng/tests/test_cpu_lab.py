"""CPU & Registers face — the hardware/privilege view, distinct from the Process Scheduler."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.xv6 import CpuState, DemoScheduler, Snapshot, parse_procdump

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield a


class _Dev:
    type_key = "xv6"
    name = "M1"
    properties = {"Timeslice": "1"}


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


# ---- pure decoders -------------------------------------------------------- #
def test_mode_split_and_csr_decoders():
    from gini.domain.xv6 import (interrupt_sources, mode_split, scause_str, sstatus_flags)
    # delta between two cumulative samples -> last-window fractions
    s = mode_split({"user": 100, "kernel": 20, "idle": 10}, {"user": 170, "kernel": 30, "idle": 20})
    assert round(s["user"], 2) == 0.78 and round(s["kernel"], 2) == 0.11
    assert mode_split({"user": 5}, {"user": 5}) == {"user": 0.0, "kernel": 0.0, "idle": 0.0}
    # sie=0x222 -> software/timer/external all enabled; sip=0x20 -> timer pending
    src = {d["name"]: d for d in interrupt_sources(0x222, 0x20)}
    assert src["timer"]["enabled"] and src["timer"]["pending"]
    assert src["external"]["enabled"] and not src["external"]["pending"]
    assert sstatus_flags(0x22)["SPP"] == "U"            # SPP clear -> came from user
    assert sstatus_flags(0x122)["SPP"] == "S"           # SPP set -> came from kernel
    assert scause_str(0x8000000000000005) == "timer int"
    assert scause_str(15) == "store page fault"


def test_decode_satp():
    from gini.ui.cpu_lab import _decode_satp
    assert "Sv39" in _decode_satp("0x8000000000087fff")
    assert "0x87fff000" in _decode_satp("0x8000000000087fff")
    assert _decode_satp("—") == ""


# ---- the view ------------------------------------------------------------- #
def _smp_state():
    from gini.domain.machine_state import MachineState

    class Smp:
        timeslice = 1
        def snapshot(self):
            regs = {
                0: CpuState(regs={"pid": "5", "pc": "0x1234", "sp": "0x80005000",
                                  "s0": "0x80005f00", "ra": "0x1d3c", "a0": "0x5", "a7": "0x7",
                                  "satp": "0x8000000000087fff", "sz": "0x3000"}),
                1: CpuState(regs={"pid": "6", "pc": "0x2000", "sp": "0x80006000",
                                  "satp": "0x8000000000088abc", "sz": "0x4000"}),
            }
            return Snapshot(procs=parse_procdump("5 run spin\n6 run spin"),
                            running_pid=5, ticks=1, cpus={0: 5, 1: 6}, cpu_regs=regs,
                            modetime={"user": 70, "kernel": 20, "idle": 10},
                            csr={"sstatus": 0x22, "sie": 0x222, "sip": 0x20,
                                 "stvec": 0x80001bb4, "scause": 0x8000000000000005, "sepc": 0x14})
        def refresh(self): return self.snapshot()
        def step(self): return self.snapshot()

    ms = MachineState(Smp(), device_id="d", vm=object(), fs=object())
    ms.refresh()
    return ms


def _tiles(lab):
    """Every register-tile name label text across all per-core sections."""
    from PySide6.QtWidgets import QLabel
    return [w.text() for w in lab._tiles_box.findChildren(QLabel)]


def test_cpu_lab_renders_modebar_csr_and_tiles(app):
    from gini.ui.cpu_lab import CpuLab
    lab = CpuLab(None, _theme(app), _Dev(), _smp_state(), live=False)
    # mode-time bar has three segments (since-boot ratio on the first frame)
    assert len(lab._bar._segs) == 3
    assert "user" in lab._bar_note.text()
    # CSR strip built chips + an honest note about the interrupt state
    from PySide6.QtWidgets import QLabel
    chips = [w.text() for w in lab._csr_box.findChildren(QLabel)]
    assert any("timer" in c for c in chips) and any("came from user" in c for c in chips)
    assert "scause" in lab._csr_note.text() and "timer int" in lab._csr_note.text()
    # per-core register tiles: two cores, satp decoded in its tile subtitle
    names = _tiles(lab)
    assert "PC" in names and "SATP" in names and "S0" in names
    assert any("Sv39" in n for n in names)                 # satp tile shows the decoded root
    lab.close()


def test_cpu_lab_degrades_without_new_kernel_fields(app):
    # older kernel (no MODETIME/CSR) -> honest "needs rebuild" notes, no crash, tiles still render
    from gini.domain.machine_state import MachineState
    from gini.ui.cpu_lab import CpuLab

    class Old:
        timeslice = 1
        def snapshot(self):
            return Snapshot(procs=parse_procdump("5 run spin"), running_pid=5,
                            cpu=CpuState(regs={"pid": "5", "pc": "0x10", "satp": "0x0"}))
        def refresh(self): return self.snapshot()
    ms = MachineState(Old(), device_id="d"); ms.refresh()
    lab = CpuLab(None, _theme(app), _Dev(), ms, live=False)
    assert lab._bar._segs == [] and "rebuild" in lab._bar_note.text()
    assert "rebuild" in lab._csr_note.text()
    assert "PC" in _tiles(lab)                              # register tiles still show
    lab.close()


def test_cpu_card_opens_the_cpu_lab_not_the_scheduler(app):
    # regression: the CPU & Registers card used to route to the scheduler page (a shortcut)
    from gini.domain.machine_state import MachineState
    from gini.ui.cpu_lab import CpuLab
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d")
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    lab._ov_cards["cpu"].clicked.emit()                    # click the CPU & Registers card
    assert isinstance(lab._cpulab, CpuLab)
    assert lab._stack.currentWidget() is lab._overview      # did NOT drill into the scheduler page
    lab._cpulab.close()
    lab.close()


def test_cpu_lab_demo_bar_animates(app):
    # the DemoScheduler carries an advancing mode-time so the offline bar isn't empty
    from gini.domain.machine_state import MachineState
    from gini.ui.cpu_lab import CpuLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d")
    for _ in range(3):
        ms.step()
    lab = CpuLab(None, _theme(app), _Dev(), ms, live=False)
    assert len(lab._bar._segs) == 3                        # user/kernel/idle all present
    lab.close()

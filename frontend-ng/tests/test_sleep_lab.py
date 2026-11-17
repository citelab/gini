"""The Sleep Lab: the dump's parser, the panel's rules, the kernel hooks, and the face.

The dumps below are real: printed by a kernel built from the pinned xv6 with this patcher and
booted in QEMU. LOST is a scratch build whose piperead() releases the pipe lock and yields before
the reader arms itself (a lost wakeup on purpose, in a scratch tree only), with a program whose
writer writes one byte and then blocks for ever: the reader slept on a byte that was already there.
OLD_PROC is the same image with the proc.c, trap.c and defs.h of an older image, as an A-Lab 01 or
A-Lab 02 student's folder has them: it builds, boots, and has no hooks.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain.xv6 import parse_sleep

from gini.ui import sleep_lab as SL

ROOT = Path(__file__).resolve().parents[2]
PATCHER = ROOT / "backend" / "xv6" / "gini_patch.py"
AGENT = ROOT / "backend" / "xv6" / "gini_agent.py"

LOST = """SLEEPNOW 60766597 10000000
SLEEPHOOKS 1
SLEEPER 1 asleep 0x0000000080020100 20481895 -1 2 init
SLEEPER 2 asleep 0x0000000080014870 20527850 -1 0 sh
SLEEPER 5 asleep 0x0000000087f25220 20580507 -1 15 lostw
SLEEPER 4 asleep 0x0000000087f0b220 20580404 1353 15 lostw
SLEEPCH 0 2 1 0 1 0 10140361 console input
SLEEPCH 1 0 26 4 22 19 0 room in the UART buffer
SLEEPCH 2 3 2 0 2 1 10296066 a child to exit
SLEEPCH 3 0 11 11 0 0 0 the next timer tick
SLEEPCH 4 45 683 638 45 4 46526 a disk block
SLEEPCH 5 0 14 14 0 0 0 an inode
SLEEPCH 6 0 135 135 0 0 0 a free disk descriptor
SLEEPCH 7 0 9 9 0 0 0 the log
SLEEPCH 15 2 21 21 0 0 0 other
"""

#: A quiet machine just after `ls`: the shell waits at the prompt, init waits for it.
QUIET = """SLEEPNOW 45879500 10000000
SLEEPHOOKS 1
SLEEPER 1 asleep 0x0000000080020100 25463145 -1 2 init
SLEEPER 2 asleep 0x0000000080014870 25440321 -1 0 sh
SLEEPCH 0 2 1 0 1 0 20377445 console input
SLEEPCH 1 0 44 4 40 34 0 room in the UART buffer
SLEEPCH 2 4 3 0 3 1 20548618 a child to exit
SLEEPCH 3 0 9 9 0 0 0 the next timer tick
SLEEPCH 4 41 723 682 41 4 22843 a disk block
SLEEPCH 15 2 38 36 2 0 13616 other
"""

OLD_PROC = """SLEEPNOW 17869867 10000000
SLEEPHOOKS 0
SLEEPER 1 asleep 0x0000000080020100 0 -1 15 init
SLEEPER 2 asleep 0x0000000080014870 0 -1 15 sh
SLEEPCH 15 0 0 0 0 0 0 other
"""


# --------------------------------------------------------------------------- #
# 1. parse_sleep
# --------------------------------------------------------------------------- #

def test_a_dump_becomes_sleepers_and_channels_in_seconds():
    v = parse_sleep(QUIET)
    assert v.ok and v.hooks
    assert [(s.pid, s.name, s.channel) for s in v.sleepers] == [
        (2, "sh", "console input"), (1, "init", "a child to exit")]
    sh = v.sleepers[0]
    assert sh.asleep and sh.seconds == pytest.approx((45879500 - 25440321) / 1e7)
    assert sh.gap is None
    uart = next(c for c in v.channels if c.name == "room in the UART buffer")
    assert (uart.wakeups, uart.empty, uart.woken, uart.resleeps) == (44, 4, 40, 34)
    assert uart.resleep_ratio == pytest.approx(34 / 40)
    assert next(c for c in v.channels if c.name == "console input").longest == \
        pytest.approx(2.0377445)


def test_an_unregistered_channel_is_shown_by_its_address():
    v = parse_sleep(LOST)
    reader = next(s for s in v.sleepers if s.pid == 4)
    assert reader.channel == "0x87f0b220" and reader.index == 15
    assert reader.gap == pytest.approx(1353 / 1e7)


def test_an_armed_process_is_listed_after_the_sleepers_and_has_no_duration():
    v = parse_sleep(QUIET + "SLEEPER 9 armed 0x0000000080014870 0 -1 0 cat\n")
    assert [s.pid for s in v.sleepers] == [2, 1, 9]
    cat = v.sleepers[-1]
    assert not cat.asleep and cat.seconds is None


def test_a_kernel_without_the_hooks_still_lists_who_is_asleep_but_times_nothing():
    v = parse_sleep(OLD_PROC)
    assert v.ok and not v.hooks
    assert {s.name for s in v.sleepers} == {"sh", "init"}
    assert all(s.seconds is None and s.gap is None for s in v.sleepers)


def test_an_older_build_gives_an_empty_view():
    v = parse_sleep("LOCKCPU 2\nLOCK proc 10 0\n")
    assert not v.ok and v.sleepers == [] and v.channels == []
    assert not parse_sleep("").ok and not parse_sleep(None).ok


# --------------------------------------------------------------------------- #
# 2. the panel's rules
# --------------------------------------------------------------------------- #

def test_the_lost_wakeup_hint_marks_only_the_stuck_reader():
    rows = SL.sleeper_rows(parse_sleep(LOST))
    hinted = [r[0] for r in rows if r[3]]
    assert hinted == ["lostw (pid 4)"]
    assert "worth a look" in rows[[r[0] for r in rows].index("lostw (pid 4)")][3]


@pytest.mark.parametrize("seconds,gap,hint", [
    (5.0, 0.0001, True),     # long sleep, tiny gap: the hint
    (0.5, 0.0001, False),    # a short sleep is not stuck
    (5.0, 0.01, False),      # a gap of 10 ms is an ordinary sleep after an ordinary post
    (5.0, None, False),      # no empty wakeup before it at all
])
def test_the_hint_needs_a_long_sleep_and_a_tiny_gap(seconds, gap, hint):
    from gini.domain.xv6 import Sleeper
    s = Sleeper(pid=3, name="x", asleep=True, chan=1, index=15, channel="0x1",
                seconds=seconds, gap=gap)
    assert bool(SL.lost_wakeup_hint(s)) is hint


def test_the_verdict_names_the_lost_wakeup_and_calls_it_a_hint():
    line = SL.verdict(parse_sleep(LOST))
    assert line.startswith("pid 4 (lostw) has slept")
    assert "lost wakeup" in line and "hint, not a verdict" in line


def test_the_verdict_on_a_quiet_machine_explains_the_shell():
    line = SL.verdict(parse_sleep(QUIET))
    assert line.startswith("2 processes asleep; the longest, sh (pid 2)")
    assert "console input (that is just the shell waiting for you)." in line


def test_the_verdict_without_hooks_says_why_nothing_is_counted():
    line = SL.verdict(parse_sleep(OLD_PROC))
    assert "without GINI's sleep hooks" in line and "2 processes asleep" in line


def test_the_verdict_on_an_old_image_and_on_an_idle_one():
    assert "rebuild the xv6 image" in SL.verdict(parse_sleep(""))
    assert SL.verdict(parse_sleep("SLEEPNOW 1 10000000\nSLEEPHOOKS 1\n")) == "Nothing is asleep."


def test_channel_rows_are_busiest_first_scaled_to_the_busiest_and_skip_idle_ones():
    v = parse_sleep(LOST + "SLEEPCH 9 0 0 0 0 0 0 a semaphore\n")
    rows = SL.channel_rows(v.channels)
    names = [r[0] for r in rows]
    assert names[0] == "a disk block" and rows[0][-1] == 1.0
    assert "a semaphore" not in names
    assert len(rows) == 8
    assert all(0 < r[-1] <= 1 for r in rows)
    assert [r[1] + r[2] for r in rows] == sorted((r[1] + r[2] for r in rows), reverse=True)


def test_a_channel_line_leaves_out_what_has_nothing_to_say():
    assert SL.channel_text(0, 135, 135, 0, 0, 0.0) == "0 sleeps · 135 wakeups · 135 found nobody"
    assert SL.channel_text(4, 26, 4, 22, 19, 0.0042) == (
        "4 sleeps · 26 wakeups · 4 found nobody · 19 of 22 woken slept again · longest 4.2 ms")
    assert SL.channel_text(1, 1, 0, 1, 0, 0.0).startswith("1 sleep · 1 wakeup · 0 found nobody")


def test_durations_read_in_the_unit_a_person_would_use():
    assert SL.fmt_duration(0.000031) == "31 µs"
    assert SL.fmt_duration(0.0042) == "4.2 ms"
    assert SL.fmt_duration(4.2) == "4.2 s"
    assert SL.fmt_duration(125) == "2 min 5 s"
    assert SL.fmt_duration(None) == "—"


# --------------------------------------------------------------------------- #
# 3. the kernel side
# --------------------------------------------------------------------------- #

# The pinned tree's text for the three functions the hooks go into (xv6-riscv 35b0884).
SLEEP_FUNCS = """
// Register current process as waiting for wakeups on chan.
void
sleep_prepare(void *chan)
{
  struct proc *p = myproc();

  acquire(&p->lock);
  if (chan == 0)
    panic("sleep_prepare: zero chan");
  p->chan = chan;
  release(&p->lock);
}

// Put the thread to sleep.  Assumes sleep_prepare() was called before.
// If the channel registered by sleep_prepare() has been woken up in
// the meantime, do not go to sleep, and instead return immediately.
void
sleep(void)
{
  struct proc *p = myproc();

  acquire(&p->lock);
  if (p->chan != 0) {
    p->state = SLEEPING;
    sched();
  }
  release(&p->lock);
}

// Wake up all processes sleeping on channel chan.
void
wakeup(void *chan)
{
  struct proc *p;

  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->chan == chan) {
      // If the process is waiting for wakeups on this channel,
      // signal that the wakeup happened by clearing p->chan.
      p->chan = 0;

      // If this waiting process has gotten so far as to actually
      // go to sleep, also set it back to RUNNING.
      if (p->state == SLEEPING) {
        p->state = RUNNABLE;
      }
    }
    release(&p->lock);
  }
}
"""


def _tree(tmp_path, with_proc_o=True):
    import test_xv6_patch as T
    k = tmp_path / "kernel"
    k.mkdir()
    (tmp_path / "user").mkdir()
    files = {"trap.c": T.TRAP_C, "proc.c": T.PROC_C + SLEEP_FUNCS, "proc.h": T.PROC_H,
             "defs.h": T.DEFS_H,
             "console.c": '#include "types.h"\n#include "defs.h"\n' + T.CONSOLE_C,
             "param.h": T.PARAM_H, "syscall.c": T.SYSCALL_C}
    for name, text in files.items():
        (k / name).write_text(text)
    mk = T.MAKEFILE
    if with_proc_o:
        mk = mk.replace("OBJS = \\\n", "OBJS = \\\n  $K/proc.o \\\n")
    (tmp_path / "Makefile").write_text(mk)
    return k


def _patch(root):
    return subprocess.run([sys.executable, str(PATCHER), str(root)], capture_output=True,
                          text=True, check=False)


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    root = tmp_path_factory.mktemp("xv6")
    k = _tree(root)
    r = _patch(root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root, k


def test_the_hooks_go_where_sleep_and_wakeup_happen(patched):
    _root, k = patched
    proc = (k / "proc.c").read_text()
    assert "  p->chan = chan;\n  gini_sleep_prepare(p, chan);" in proc
    assert ("    p->state = SLEEPING;\n    gini_sleep_begin(p);   // GINI: the Sleep Lab\n"
            "    sched();\n    gini_sleep_end(p);") in proc
    assert "      p->chan = 0;\n      gini_woke(p, chan);" in proc
    assert "    release(&p->lock);\n  }\n  gini_wakeup(chan, gn);   // GINI\n}" in proc


def test_the_hooks_are_declared_in_proc_c_not_in_defs_h(patched):
    """An A-Lab 01 folder keeps its own defs.h, so nothing GINI's proc.c needs may live there."""
    _root, k = patched
    proc = (k / "proc.c").read_text()
    assert "void gini_sleep_prepare(struct proc *, void *);" in proc
    assert "gini_sleep" not in (k / "defs.h").read_text()


def test_no_field_is_added_to_struct_proc(patched):
    """A-Lab 02 students own proc.h: a new field would break every A-Lab 02 folder."""
    import test_xv6_patch as T
    _root, k = patched
    assert "gini_sleep" not in (k / "proc.h").read_text()
    assert (k / "proc.h").read_text().startswith(T.PROC_H.split("};")[0])


def test_gini_sleep_c_is_built_and_reaches_the_registry_weakly(patched):
    root, k = patched
    src = (k / "gini_sleep.c").read_text()
    assert "$K/gini_sleep.o" in (root / "Makefile").read_text()
    assert "int gini_chan_index(void *chan) __attribute__((weak));" in src
    assert "char *gini_chan_nth(int i) __attribute__((weak));" in src
    assert "struct gini_sleeper gini_sleepers[NPROC];" in src
    trap = (k / "trap.c").read_text()
    assert "\ngini_chan_index(void *chan)\n" in trap and "\ngini_chan_nth(int i)\n" in trap


def test_the_dump_and_reset_sit_behind_the_command_mux(patched):
    _root, k = patched
    con = (k / "console.c").read_text()
    assert "if(c == 's'){ gini_obs_begin(); gini_sleepdump(); gini_obs_end();" in con
    assert "if(c == 'x'){ gini_sleepreset();" in con
    assert "void gini_sleepdump(void);" in con


def test_the_agent_and_the_mux_spec_agree_on_the_bytes():
    sys.path.insert(0, str(ROOT / "backend" / "xv6"))
    try:
        import console_mux as cm
    finally:
        sys.path.pop(0)
    assert cm.encode("sleep_dump") == b"\x17s"
    assert cm.encode("sleep_reset") == b"\x17x"
    agent = AGENT.read_text()
    assert 'path == "/sleep"' in agent and '_SERIAL.dump(b"\\x17s")' in agent
    assert 'u.path == "/sleep/reset"' in agent and '_SERIAL.write("\\x17x")' in agent


def test_running_the_patcher_again_adds_nothing_twice(tmp_path):
    k = _tree(tmp_path)
    assert _patch(tmp_path).returncode == 0
    assert _patch(tmp_path).returncode == 0
    proc, con = (k / "proc.c").read_text(), (k / "console.c").read_text()
    for needle in ("gini_sleep_prepare(p, chan);", "gini_sleep_begin(p);", "gini_sleep_end(p);",
                   "gini_woke(p, chan);", "gini_wakeup(chan, gn);", "int gn = 0;"):
        assert proc.count(needle) == 1, needle
    assert con.count("gini_sleepdump();") == 1 and con.count("gini_sleepreset();") == 1
    assert (k / "trap.c").read_text().count("\ngini_chan_index(void *chan)\n") == 1
    assert (tmp_path / "Makefile").read_text().count("$K/gini_sleep.o") == 1


def test_without_the_object_file_no_hook_is_applied(tmp_path):
    """A hook without gini_sleep.o does not link, so the two come together or not at all; and a
    telemetry edit that cannot apply skips rather than stopping the image build."""
    k = _tree(tmp_path, with_proc_o=False)
    r = _patch(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "gini_sleep" not in (k / "proc.c").read_text()
    assert not (k / "gini_sleep.c").exists()


# --------------------------------------------------------------------------- #
# 4. the face
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def test_the_face_sits_in_the_kernel_band_next_to_the_lock_lab():
    from gini.ui.machine_lab import LAYERS, MachineLab
    kernel = next(b for b in LAYERS if b.name == "KERNEL")
    keys = [f.key for f in kernel.faces]
    assert keys[keys.index("locks") + 1] == "sleep"
    face = kernel.faces[keys.index("sleep")]
    assert face.title == "Sleep & Wakeup" and face.opens == "_open_sleep_lab"
    assert hasattr(MachineLab, "_open_sleep_lab")
    assert "_sleeplab" in MachineLab._FACES


def test_the_face_has_its_own_icon(app):
    from gini.ui.theme import icons
    assert "sleep" in icons._BODY
    assert not icons.render_pixmap("sleep", "#a070ff", 22).isNull()


def test_offline_the_panel_says_so_and_polls_nothing(theme):
    lab = SL.SleepLab(None, theme, live=False)
    try:
        assert "offline" in lab._note.text()
        assert not lab._poll.isActive()
        assert not lab._reset_btn.isEnabled()
    finally:
        lab.close()


def test_a_view_fills_the_table_and_the_verdict(theme):
    lab = SL.SleepLab(None, theme, live=False)
    try:
        lab.show_view(parse_sleep(LOST))
        assert lab._table.rowCount() == 4
        cells = [[lab._table.item(r, c).text() for c in range(4)] for r in range(4)]
        reader = next(row for row in cells if row[0] == "lostw (pid 4)")
        assert reader[1] == "0x87f0b220" and "worth a look" in reader[3]
        assert lab._note.text().startswith("pid 4 (lostw)")
        assert lab._bars._rows and lab._bars._rows[0][0] == "a disk block"
    finally:
        lab.close()


class _Agent:
    def __init__(self, text):
        self.text, self.posts = text, []

    def get_text(self, path):
        assert path == "/sleep"
        return self.text

    def post(self, path):
        self.posts.append(path)


class _Provider:
    def __init__(self, text):
        self.agent = _Agent(text)


def test_live_it_reads_sleep_and_reset_posts_the_reset(theme, qtbot):
    prov = _Provider(QUIET)
    lab = SL.SleepLab(None, theme, provider=prov, live=True)
    try:
        qtbot.waitUntil(lambda: "2 processes asleep" in lab._note.text(), timeout=3000)
        lab._reset()
        assert prov.agent.posts == ["/sleep/reset"]
    finally:
        lab.close()
    assert not lab._poll.isActive()

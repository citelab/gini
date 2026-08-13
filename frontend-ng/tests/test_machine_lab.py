"""Machine Lab scheduler face — renders offscreen against a fake xv6 provider."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.xv6 import DemoScheduler, Snapshot, parse_procdump

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield a


class _Dev:
    type_key = "xv6"
    name = "xv6-1"
    properties = {"Timeslice": "1"}


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _tree_items(tree):
    """Every QTreeWidgetItem in the process tree, flattened."""
    out = []

    def walk(it):
        out.append(it)
        for i in range(it.childCount()):
            walk(it.child(i))
    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return out


def _tree_count(tree):
    return len(_tree_items(tree))


def _tree_item(tree, pid):
    return next(it for it in _tree_items(tree) if it.text(1) == str(pid))


def test_demo_scheduler_round_robins_and_switches():
    sched = DemoScheduler(timeslice=1)
    pids = [sched.snapshot().running_pid]
    for _ in range(4):
        pids.append(sched.step().running_pid)
    assert len(set(pids)) > 1                 # the CPU moves between processes
    assert all(p in (3, 4, 5) for p in pids)  # only the runnable CPU-bound procs run


def test_lab_opens_and_steps(app):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    assert _tree_count(lab._proc_tree) == 5       # process tree rendered
    before = lab.state.timeline.switches()
    for _ in range(6):
        lab._on_step()
    assert lab.state.timeline.switches() > before  # stepping produces context switches
    assert "sw" in lab._switch_lbl.text() and "/s" in lab._switch_lbl.text()
    # per-CPU register table populated (pc is the first row, CPU 0 the first column)
    assert lab._reg_tbl.columnCount() >= 1
    assert lab._reg_tbl.item(0, 0).text().startswith("0x")
    lab.close()


def test_lab_opens_on_the_layered_overview(app):
    # the front door is now the layered OS overview, NOT the dense scheduler view
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    assert lab._stack.currentWidget() is lab._overview
    assert lab._back_btn.isHidden() is True      # hidden on the hub (never .show()n dialog)
    # the layers expose the expected drill-down cards
    for key in ("programs", "syscalls", "builder", "scheduler", "memory", "storage",
                "journey", "cpu"):
        assert key in lab._ov_cards
    lab.close()


def test_scheduler_card_opens_its_own_window(app):
    # the scheduler now opens as its OWN window (like every other card) so the hub stays up and
    # subsystems can be open concurrently
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    lab._ov_cards["scheduler"].clicked.emit()          # click the Scheduler card
    assert lab._sched_win is not None                  # opened as a separate window
    assert lab._sched_page.parent() is lab._sched_win  # the page was reparented into it
    assert lab._stack.currentWidget() is lab._overview  # the hub is still shown (concurrent)
    lab._sched_win.close()
    lab.close()


def test_overview_cards_carry_live_mini_stats(app):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    assert "procs" in lab._ov_cards["scheduler"].stat.text()      # e.g. "3 procs · 0.0 sw/s"
    assert "switches" in lab._ov_cards["journey"].stat.text()
    assert "core" in lab._ov_cards["cpu"].stat.text()
    lab.close()


def test_policy_combo_switches_policy(app):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    lab._policy_combo.setCurrentText("lottery")          # user picks a policy
    assert lab.state.policy == "lottery"                 # drives MachineState -> provider
    assert lab.state.provider.policy == "lottery"        # demo honours it (offline)
    lab.close()


def test_scheduler_page_has_ready_queue_and_share(app):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    for _ in range(6):                                   # build a timeline so shares populate
        lab._on_step()
    tbl = lab._sched_panel._tbl
    assert tbl.rowCount() >= 1
    shares = [tbl.item(r, 5).text() for r in range(tbl.rowCount()) if tbl.item(r, 5)]
    assert any("%" in s for s in shares)                 # a CPU-share bar rendered
    lab.close()


def test_starvation_flag_badges_the_process_tree(app):
    from gini.domain.machine_state import MachineState, StateWatcher
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(), device_id="d")
    ms.watcher = StateWatcher(starve=2)
    snap = Snapshot(procs=parse_procdump("3 running spin 1\n4 runnable spin 1"), running_pid=3)
    for _ in range(3):                                   # drive a starvation condition for pid 4
        ms._ingest(snap)
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    lab._render()
    it = _tree_item(lab._proc_tree, 4)
    assert "⚠" in it.text(2) or "starving" in it.text(2)  # pid 4 is badged as starving
    lab.close()


def test_data_mode_toggle_swaps_source(app):
    # the Real/Demo toggle is a user action that swaps the state's data plane
    from gini.domain.machine_state import MachineState
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d1", mode="demo")
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    assert lab.state.mode == "demo" and lab._mode_btns["demo"].isChecked()
    lab._set_data_mode("real")                       # click "Real"
    assert lab.state.mode == "real" and lab._mode_btns["real"].isChecked()
    assert lab.live is True
    lab._set_data_mode("demo")                       # ...back to Demo
    assert lab.state.mode == "demo" and lab.live is False
    lab.close()


def test_real_mode_without_data_shows_banner_not_fakes(app):
    # Real selected with nothing running -> an explicit banner, never demo data in the panels
    from gini.domain.machine_state import MachineState
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d1", mode="demo")
    ms.set_mode("real")                              # user asked for Real; no live plane attached
    assert ms.provider is None and ms.latest is None
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    assert lab._banner.isVisibleTo(lab._sched_page)  # the "no live data" banner is shown
    assert "No live data" in lab._banner_lbl.text()
    # opening a data face is guarded (no crash on a null provider) — it reveals the banner instead
    # (which lives on the scheduler page, now opened as its own window)
    lab._open_storage_lab()
    assert lab._sched_win is not None
    lab._sched_win.close()
    lab.close()


def test_lab_timeslice_slider_feeds_state(app):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    lab._slice.setValue(2)                      # dragging updates the label only, not the kernel
    assert "~1.0s" in lab._slice_lbl.text()     # 2 ticks * 0.5s = 1.0s slice
    lab._apply_slice()                          # committed on release
    assert lab.state.timeslice == 2
    lab.close()


def test_lab_renders_from_shared_state(app):
    # the Lab and the agent read ONE MachineState — steps taken via the state show in the Lab
    from gini.domain.machine_state import MachineState
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d1")
    lab = MachineLab(None, _theme(app), _Dev(), state=ms, live=True)
    assert lab.live is True
    assert lab.state is ms
    lab.close()


class _FakeLive:
    """A live-shaped provider: procs incl. a killable user process, + program controls."""
    timeslice = 1
    def __init__(self):
        self.ran, self.killed = [], []
    def snapshot(self):
        procs = parse_procdump("1 sleeping init\n2 sleeping sh\n5 running spin")
        return Snapshot(procs=procs, running_pid=5, ticks=1)
    def refresh(self):
        return self.snapshot()
    def step(self):
        return self.snapshot()
    def set_timeslice(self, v):
        self.timeslice = v
    def run(self, prog):
        self.ran.append(prog); return True
    def kill(self, pid):
        self.killed.append(pid)
    def console(self):
        return "$ "


def test_smp_shows_a_gantt_strip_per_cpu(app):
    from gini.domain.machine_state import MachineState
    from gini.ui.machine_lab import MachineLab

    class Smp:
        timeslice = 1
        def snapshot(self):
            procs = parse_procdump("5 run spin\n6 run spin\n7 runble spin")
            return Snapshot(procs=procs, running_pid=5, ticks=1, cpus={0: 5, 1: 6})
        def refresh(self): return self.snapshot()
        def step(self): return self.snapshot()
        def set_timeslice(self, v): self.timeslice = v

    ms = MachineState(Smp(), device_id="d1", vm=object(), fs=object())
    lab = MachineLab(None, _theme(app), _Dev(), state=ms, live=True)
    assert set(lab._gantts) == {0, 1}                    # one strip per CPU
    assert lab._gantts[0].label == "CPU 0" and lab._gantts[1].label == "CPU 1"
    lab.close()


def test_live_lab_has_launcher_and_kill_buttons(app):
    from gini.domain.machine_state import MachineState
    from gini.ui.machine_lab import MachineLab
    prov = _FakeLive()
    ms = MachineState(prov, device_id="d1", vm=object(), fs=object())
    lab = MachineLab(None, _theme(app), _Dev(), state=ms, live=True)
    # the launcher dropdown is present with the long-running programs
    assert "spin" in [lab._prog_combo.itemText(i) for i in range(lab._prog_combo.count())]
    # a kill button exists for the user process (pid 5) but NOT for init/sh
    tree = lab._proc_tree
    assert tree.itemWidget(_tree_item(tree, 5), 3) is not None   # pid 5 -> kill button
    assert tree.itemWidget(_tree_item(tree, 1), 3) is None       # init -> no kill
    lab.close()


def test_live_lab_launch_and_kill_call_provider(app):
    from gini.domain.machine_state import MachineState
    from gini.ui.machine_lab import MachineLab
    prov = _FakeLive()
    ms = MachineState(prov, device_id="d1", vm=object(), fs=object())
    lab = MachineLab(None, _theme(app), _Dev(), state=ms, live=True)
    lab._prog_combo.setCurrentText("alloc")
    lab._launch()
    lab._kill(5)
    import time
    for _ in range(50):                                    # let the daemon threads run
        app.processEvents(); time.sleep(0.005)
        if prov.ran and prov.killed:
            break
    assert prov.ran == ["alloc"] and prov.killed == [5]
    lab.close()


def test_machine_lab_has_no_console_button(app):
    # the console is now a peripheral (the Terminal), not a button baked into the Lab
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    labels = [b.text().strip() for b in lab.findChildren(QtWidgets.QPushButton)]
    assert "Console" not in labels
    lab.close()


class _Term(_FakeLive):
    """A streaming console provider: records input and serves an append-only console."""
    def __init__(self):
        super().__init__()
        self.sent = []
        self.cleared = 0
        self.interrupts = 0
        self.since_calls = 0
        self._log = ""
    def send_input(self, s):
        self.sent.append(s)
        self._log += s                            # xv6 echoes input; model that here
    def console_since(self, since):
        self.since_calls += 1
        return self._log[since:], len(self._log)
    def clear_console(self):
        self.cleared += 1
        self._log = ""
    def interrupt(self):
        self.interrupts += 1


def _wait(app, cond, n=200):        # ~1s budget: absorbs cold-start jitter (font aliasing, first
    import time                      # poll tick); early-exits the instant cond() is true, so warm
    for _ in range(n):              # runs stay fast. Guards against first-run timing flakes.
        app.processEvents(); time.sleep(0.005)
        if cond():
            return


def test_shadow_bar_load_shows_result_and_mirrors_to_console(app):
    from gini.domain.machine_state import MachineState
    from gini.domain.xv6 import ShadowStatus
    from gini.ui.machine_lab import MachineLab

    logs = []

    class Prov(_FakeLive):
        def __init__(self):
            super().__init__(); self.loaded = 0; self.reverted = 0; self.toggled = []
        def shadows(self):
            return {"prio_sched": ShadowStatus("prio_sched", present=True, enabled=True,
                                               active=True, faults=0, hash="abc123")}
        def load(self):
            self.loaded += 1; return True, "loaded"
        def revert(self):
            self.reverted += 1; return False, "kernel/shadows/gini_sched.c:22: error: expected ';'"
        def set_shadow(self, name, on):
            self.toggled.append((name, on))

    prov = Prov()
    ms = MachineState(prov, device_id="d", vm=object(), fs=object())
    lab = MachineLab(None, _theme(app), _Dev(), state=ms, live=True,
                     on_log=lambda lvl, msg: logs.append((lvl, msg)))
    lab._show_scheduler()
    assert hasattr(lab, "_load_btn") and hasattr(lab, "_shadow_status")   # the shadow bar built

    lab._load_shadow()                                     # success path
    _wait(app, lambda: prov.loaded > 0 and "Loaded" in lab._shadow_result.toPlainText())
    assert prov.loaded == 1
    assert "✓ Loaded" in lab._shadow_result.toPlainText()
    assert any(lvl == "info" for lvl, _ in logs)           # mirrored to the console

    lab._revert_shadow()                                   # failure path -> compile error inline
    _wait(app, lambda: prov.reverted > 0 and "error:" in lab._shadow_result.toPlainText())
    assert "error:" in lab._shadow_result.toPlainText()    # gcc error shown inline, not just console
    assert any(lvl == "error" for lvl, _ in logs)
    lab.close()


def test_terminal_sends_command_with_args(app):
    from gini.ui.peripherals import TerminalView
    prov = _Term()
    term = TerminalView(None, _theme(app), prov, _Dev())
    term.input.setText("spin 10 &")               # a real shell command WITH an argument
    term._submit()                                # Enter
    _wait(app, lambda: bool(prov.sent))
    assert prov.sent == ["spin 10 &\n"]           # args flow straight to xv6's sh
    term.close()


def test_terminal_streams_output_append_only(app):
    from gini.ui.peripherals import TerminalView
    prov = _Term()
    term = TerminalView(None, _theme(app), prov, _Dev())
    prov._log = "$ ls\nREADME cat echo\n"          # kernel output arrives
    _wait(app, lambda: "README" in term.view.toPlainText())
    assert "README cat echo" in term.view.toPlainText()
    term.close()


def test_terminal_history_recall(app):
    from gini.ui.peripherals import TerminalView
    term = TerminalView(None, _theme(app), _Term(), _Dev())
    for c in ("ls", "spin &"):
        term.input.setText(c); term._submit()
    term._recall(-1)                              # up-arrow
    assert term.input.text() == "spin &"
    term._recall(-1)
    assert term.input.text() == "ls"
    term._recall(+1)
    assert term.input.text() == "spin &"
    term.close()


def test_terminal_break_interrupts_foreground(app):
    from gini.ui.peripherals import TerminalView
    prov = _Term()
    term = TerminalView(None, _theme(app), prov, _Dev())
    term._break()                                 # the Break ⌃C button
    _wait(app, lambda: prov.interrupts > 0)
    assert prov.interrupts == 1                    # kernel-side break, works even while sh blocks
    assert prov.sent == []                         # not sent as shell input
    term.close()


def test_terminal_break_button_is_not_default(app):
    # regression: a dialog push button is auto-default, so Enter in the input would ALSO click
    # Break and kill a process on every command. The Break button must opt out of default, so
    # Enter only submits (proven separately by _submit not touching interrupt()).
    from gini.ui.peripherals import TerminalView
    term = TerminalView(None, _theme(app), _Term(), _Dev())
    assert term._break_btn.autoDefault() is False and term._break_btn.isDefault() is False
    term.close()


def test_terminal_submit_never_interrupts(app):
    # submitting any command must NOT fire a break (only the Break button does)
    from gini.ui.peripherals import TerminalView
    prov = _Term()
    term = TerminalView(None, _theme(app), prov, _Dev())
    for c in ("spin &", "ls", "ps"):
        term.input.setText(c); term._submit()
    _wait(app, lambda: len(prov.sent) >= 2)
    assert prov.interrupts == 0
    term.close()


def test_terminal_refresh_never_overlaps(app):
    # regression: a slow agent + fixed timer stacked reads from the SAME cursor -> doubled output.
    # While a read is in flight (_fetching), a second _refresh must be a no-op.
    from gini.ui.peripherals import TerminalView
    prov = _Term()
    term = TerminalView(None, _theme(app), prov, _Dev())
    term._fetching = True                          # pretend a read is in flight
    before = prov.since_calls
    term._refresh()                                # must NOT start another read
    assert prov.since_calls == before
    term.close()


def test_terminal_builtins_help_clear_ps(app):
    from gini.ui.peripherals import TerminalView
    prov = _Term()
    term = TerminalView(None, _theme(app), prov, _Dev())
    term.input.setText("help"); term._submit()
    assert "xv6 programs" in term.view.toPlainText()
    assert prov.sent == []                         # help is terminal-side, never sent to xv6
    term.input.setText("ps"); term._submit()       # ps -> Ctrl-P to the kernel
    _wait(app, lambda: "\x10" in prov.sent)
    assert "\x10" in prov.sent
    term.input.setText("clear"); term._submit()
    _wait(app, lambda: prov.cleared > 0)
    assert prov.cleared == 1
    term.close()

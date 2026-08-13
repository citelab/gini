"""Trap Lab — histogram + live feed render offscreen against a fake /traps source."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

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


def _wait(app, cond, n=200):
    import time
    for _ in range(n):
        app.processEvents(); time.sleep(0.005)
        if cond():
            return


_FEED = ("TC 0 syscall 20\nTC 1 pagefault 3\nTC 2 timer 90\n"
         "TR 5 2 0x8000000000000005 0x1050 0x0\n"
         "TR 7 1 0x000000000000000f 0x1080 0x0000000000004000\n")


def test_trap_lab_renders_histogram_and_feed(app):
    from gini.ui.trap_lab import TrapLab
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: _FEED)
    # the bars widget gets all six kinds (biggest first); feed shows the recent traps
    _wait(app, lambda: bool(lab._bars._rows))
    kinds = [k for k, _l, _v in lab._bars._rows]
    assert set(kinds) == {0, 1, 2, 3, 4, 5}                 # every kind present
    assert lab._bars._rows[0][0] == 2                       # timer is the biggest bucket
    _wait(app, lambda: "timer" in lab._feed.toPlainText())
    assert "pagefault" in lab._feed.toPlainText()
    assert "0x0000000000004000" in lab._feed.toPlainText() # the store-fault address shows
    lab.close()


def test_trap_lab_step_button_invokes_callback(app):
    from gini.ui.trap_lab import TrapLab
    fired = []
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: "",
                  on_step=lambda fr=None: fired.append(1))   # on_step receives the frame (or None)
    lab._step()
    assert fired == [1]                                     # "Step a trap" opens the journey
    lab.close()


def test_trap_lab_empty_source_is_safe(app):
    # Real mode with nothing running -> empty feed, no crash, journey still reachable via _step
    from gini.ui.trap_lab import TrapLab
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=None)
    _wait(app, lambda: True, n=4)
    assert not any(v for _k, _l, v in lab._bars._rows)      # nothing charted, no exception
    lab.close()


def test_hub_journey_card_opens_trap_lab(app):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(None, _theme(app), _Dev(), state=None)
    lab._ov_cards["journey"].clicked.emit()                # the "Traps & Interrupts" card
    assert hasattr(lab, "_traplab")                        # opened the Trap Lab, not the raw journey
    lab._traplab.close()
    lab.close()


def test_trap_lab_catch_seeds_the_journey(app):
    # "Step a trap" with a live catch source freezes a trap (off-thread) then opens the journey
    # seeded with the returned frame.
    from gini.domain.xv6 import DemoScheduler
    from gini.ui.trap_lab import TrapLab
    got = []
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: "",
                  catch_source=DemoScheduler().catch_trap, on_step=lambda fr: got.append(fr))
    lab._step()                                            # spawns a worker, emits `caught`
    _wait(app, lambda: bool(got))
    assert got and got[0].ok and got[0].kind == 1          # a frozen store page fault reached on_step
    lab.close()


def test_trap_lab_step_without_catch_opens_authored(app):
    # no live catch source -> step passes None (the authored journey), no freezing
    from gini.ui.trap_lab import TrapLab
    got = []
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: "",
                  catch_source=None, on_step=lambda fr: got.append(fr))
    lab._step()
    assert got == [None]
    lab.close()


def test_journey_seeded_frame_shows_live_banner_and_scause(app):
    from gini.domain.cpu_journey import JOURNEYS
    from gini.domain.xv6 import DemoScheduler
    from gini.ui.cpu_journey import CpuJourney
    fr = DemoScheduler().catch_trap()                      # a real-looking store page fault
    j = CpuJourney(None, _theme(app), _Dev(), frame=fr)
    assert "store page fault" in j._live.text() and "stval" in j._live.text()   # live banner
    # at the usertrap stage the caption carries the REAL decoded scause, not just the template
    j._i = next(i for i, s in enumerate(JOURNEYS["syscall"]) if s.title == "usertrap")
    j._render()
    assert "scause" in j._caption.text() and "store page fault" in j._caption.text()
    j.close()


def test_journey_without_frame_still_works(app):
    # no frozen trap -> no banner, authored captions only (backwards compatible)
    from gini.ui.cpu_journey import CpuJourney
    j = CpuJourney(None, _theme(app), _Dev())
    assert j._live.isHidden() or not j._live.text()        # banner hidden when there's no frame
    j._render()                                            # renders without a frame, no crash
    j.close()


def test_trap_lab_alarm_strip_renders_countdown(app):
    # Phase 3: the sigalarm strip shows an active alarm's countdown when an alarm source is wired
    from gini.domain.xv6 import DemoScheduler
    from gini.ui.trap_lab import TrapLab
    d = DemoScheduler()
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: "", alarm_source=d.alarms)
    assert lab._alarms.isVisibleTo(lab)                    # the strip shows (alarm source present)
    _wait(app, lambda: "pid 5" in lab._alarms.text())
    assert "every 10 ticks" in lab._alarms.text() and "fires in" in lab._alarms.text()
    lab.close()


def test_trap_lab_no_alarm_source_hides_strip(app):
    from gini.ui.trap_lab import TrapLab
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: "", alarm_source=None)
    assert lab._alarms.isHidden()                          # no sigalarm strip when unavailable
    lab.close()


def test_trap_lab_kind_selector_passes_kind_to_catch(app):
    # Phase 4: the "catch" selector feeds the chosen kind into the freeze call
    from gini.ui.trap_lab import TrapLab
    seen = []
    lab = TrapLab(None, _theme(app), _Dev(), traps_source=lambda: "",
                  catch_source=lambda kind: seen.append(kind) or _mk_frame(),
                  on_step=lambda fr: None)
    lab._kind.setCurrentText("timer")
    lab._step()
    _wait(app, lambda: bool(seen))
    assert seen == ["timer"]
    lab.close()


def _mk_frame():
    from gini.domain.xv6 import TrapFrame
    return TrapFrame(scause="0x8000000000000005", kind=2, kind_name="timer", ok=True)

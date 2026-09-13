"""GINI Source serves two element families, and switching between them used to crash.

    ValueError: invalid literal for int() with base 10:
                '/Users/…/.gini/scripts/rip_reference.lua'

The jump list carries a LINE for kernel source and a FILE PATH for router modules. Both lived in
Qt.UserRole and were told apart by a `_mode` flag on the widget — and the flag can disagree with
the list:

  * `show_none()` sets the mode and THEN clears the list. QListWidget reassigns "current" to
    surviving rows as it removes them, so `currentItemChanged` fires with path-bearing items
    while the mode already reads "none".
  * `show_block()` is worse: its load is asynchronous, so the stale script list outlives the mode
    change until the reply lands.

The fix is that the ITEM says what it is. These tests drive the transitions that broke.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


@pytest.fixture()
def scripts(tmp_path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir()
    for n in ("rip_reference.lua", "mcast_tree.lua"):
        (d / n).write_text("-- a module\nfunction init() end\n")
    import gini.ui.source_browser as sb
    monkeypatch.setattr(sb, "scripts_dir", lambda: d)
    return d


def _browser(theme):
    from gini.ui.source_browser import SourceBrowser
    b = SourceBrowser(theme, fetch_fn=lambda: None)
    b.resize(420, 620)
    return b


def test_router_then_something_with_no_source(app, theme, scripts):
    """THE crash. show_scripts fills the list with paths; show_none flips the mode and clears,
    and the clear itself emits currentItemChanged with the surviving path items."""
    b = _browser(theme)
    b.show_scripts("R1")
    assert b._jump.count() == 2
    b.show_none("S1 (Switch)")                 # must not raise
    assert b._jump.count() == 0


def test_a_path_item_is_never_read_as_a_line_whatever_the_mode(app, theme, scripts):
    """THE invariant, stated directly.

    In the app the window opens because `show_block()` flips the mode to "kernel" and then loads
    asynchronously, so the path-bearing list from `show_scripts()` is still on screen when the
    reply has not arrived. That timing is awkward to stage here, so the mode is forced instead —
    which tests the same thing more sharply: an item's payload must be interpreted by ITS OWN
    kind, never by a flag that lives on the widget.

    The count assertion matters: an earlier version of this test looped over an already-cleared
    list and passed without clicking anything at all.
    """
    b = _browser(theme)
    b.show_scripts("R1")
    assert b._jump.count() == 2, "the list must hold path items for this test to mean anything"
    for mode in ("kernel", "none", ""):
        b._mode = mode                          # the stale-flag window
        for i in range(b._jump.count()):
            b._on_jump(b._jump.item(i))         # must not raise ValueError


def test_a_line_item_is_never_read_as_a_path(app, theme):
    """The mirror image: kernel entries clicked while the mode says scripts."""
    b = _browser(theme)
    b._on_loaded("kernel/bio.c", "// c\nstruct buf*\nbread(uint d)\n{\n  return 0;\n}\n")
    assert b._jump.count() >= 1
    b._mode = "scripts"
    for i in range(b._jump.count()):
        b._on_jump(b._jump.item(i))             # must not try to open a line number as a file


def test_clicking_a_script_opens_it(app, theme, scripts):
    b = _browser(theme)
    b.show_scripts("R1")
    b._jump.setCurrentRow(1)
    b._on_jump(b._jump.item(1))
    assert ".lua" in b._sub.text()
    assert b._view.toPlainText().startswith("-- a module")


def test_clicking_a_kernel_entry_jumps_to_its_line(app, theme):
    b = _browser(theme)
    b._on_loaded("kernel/bio.c",
                 "// c\nstruct buf*\nbread(uint dev)\n{\n  GINI_SUB(GSUB_BCACHE);"
                 "  // GINI-xv6: board probe bread\n  return 0;\n}\n")
    assert b._jump.count() >= 1
    b._on_jump(b._jump.item(0))
    assert b._view.textCursor().blockNumber() >= 0


def test_an_item_with_no_kind_is_ignored_not_fatal(app, theme):
    """Defence for a future mode that forgets to tag its payload."""
    from PySide6.QtWidgets import QListWidgetItem
    b = _browser(theme)
    b._jump.addItem(QListWidgetItem("untagged"))
    b._on_jump(b._jump.item(0))                # must not raise


def test_bouncing_between_modes_repeatedly(app, theme, scripts):
    """Every transition, several times — the crash needed a specific order to show up."""
    b = _browser(theme)
    for _ in range(3):
        b.show_scripts("R1")
        b.show_block("bcache")
        b.show_none("X")
        b.show_scripts("R2")
        b.show_none("Y")
    assert b._jump.count() == 0


# -- apps: the xv6 user programs, read from the running container ------------------------------- #
# Nothing is shipped in the wheel; `user/<name>.c` comes from the machine's own checkout through
# the agent, and WHICH apps exist is asked of the machine rather than hardcoded. The mode joins the
# dispatch this file exists to guard, so it gets the same stale-flag treatment as the others.

class _FakeAgent:
    """Stands in for the in-container agent: names from /programs, source from /source."""

    def __init__(self, names=("spin", "walker"), text="// an app\nint\nmain(void)\n{\n  return 0;\n}\n"):
        self.names, self.text, self.asked = list(names), text, []

    def get_json(self, path):
        self.asked.append(path)
        return {"programs": self.names}

    def get_text(self, path):
        self.asked.append(path)
        return self.text


def _apps_browser(theme, agent):
    from gini.ui.source_browser import SourceBrowser
    b = SourceBrowser(theme, fetch_fn=lambda: agent)
    b.resize(420, 620)
    return b


def _settle(app, b, want, n=200):
    import time
    for _ in range(n):
        app.processEvents()
        if want():
            return
        time.sleep(0.005)


def test_apps_mode_lists_the_machines_own_programs(app, theme):
    agent = _FakeAgent(names=("walker", "spin", "alloc"))
    b = _apps_browser(theme, agent)
    b.show_apps("xv6-1")
    _settle(app, b, lambda: b._jump.count() >= 3)
    assert [b._jump.item(i).text() for i in range(b._jump.count())] == ["alloc", "spin", "walker"]
    assert "/programs" in agent.asked, "the list must come from the machine, not a hardcoded one"
    # top pane IS the file list in this mode, so the header combo gets out of the way
    assert not b._files.isVisible()


def test_apps_mode_opens_the_first_app_once(app, theme):
    agent = _FakeAgent(names=("spin",))
    b = _apps_browser(theme, agent)
    b.show_apps("xv6-1")
    _settle(app, b, lambda: bool(b._view.toPlainText()))
    assert b._view.toPlainText().startswith("// an app")
    # exactly one fetch: selecting row 0 must not ALSO fire the signal and fetch it again
    assert agent.asked.count("/source?file=user/spin.c") == 1


def test_picking_an_app_does_not_wipe_the_app_list(app, theme):
    """The trap this mode had to avoid. `_on_loaded` repopulates the top pane with the file's
    FUNCTIONS — right when the file list lives in the header combo, fatal here where the top pane
    IS the list. Apps therefore load through their own path."""
    agent = _FakeAgent(names=("spin", "walker"))
    b = _apps_browser(theme, agent)
    b.show_apps("xv6-1")
    _settle(app, b, lambda: b._jump.count() >= 2)
    before = [b._jump.item(i).text() for i in range(b._jump.count())]
    b._on_jump(b._jump.item(1))                       # walker
    _settle(app, b, lambda: "walker" in b._sub.text())
    assert [b._jump.item(i).text() for i in range(b._jump.count())] == before


def test_an_app_item_is_never_read_as_a_line_or_a_path(app, theme):
    """The invariant this file is about, extended to the new kind."""
    agent = _FakeAgent(names=("spin", "walker"))
    b = _apps_browser(theme, agent)
    b.show_apps("xv6-1")
    _settle(app, b, lambda: b._jump.count() >= 2)
    assert b._jump.count() == 2, "the list must hold app items for this test to mean anything"
    for mode in ("kernel", "scripts", "none", ""):
        b._mode = mode                                # the stale-flag window
        for i in range(b._jump.count()):
            b._on_jump(b._jump.item(i))               # must not raise


def test_apps_mode_then_no_source_clears_without_crashing(app, theme):
    agent = _FakeAgent(names=("spin", "walker"))
    b = _apps_browser(theme, agent)
    b.show_apps("xv6-1")
    _settle(app, b, lambda: b._jump.count() >= 2)
    b.show_none("S1 (Switch)")                        # must not raise
    assert b._jump.count() == 0


def test_apps_mode_with_no_machine_says_so_rather_than_showing_nothing(app, theme):
    from gini.ui.source_browser import SourceBrowser
    b = SourceBrowser(theme, fetch_fn=lambda: None)
    b.show_apps("xv6-1")
    assert "No running xv6 machine" in b._sub.text()
    assert b._jump.count() == 0


# --- the intermittent one ------------------------------------------------------------------- #

def test_the_agent_is_found_even_when_the_lab_is_showing_demo_data(app, theme, monkeypatch):
    """The race behind "sometimes the apps appear and sometimes they do not".

    `MachineState.provider` is the plane matching the DISPLAY mode, not the live bridge. A state
    created before the bridge existed is a Demo state, and `attach_real` deliberately leaves a demo
    user in Demo — so the bridge sits in `_real`, and a lookup through `.provider` finds a
    DemoScheduler with no `.agent`. GINI Source then said "No running xv6 machine" about a machine
    that was running. Whether it happened depended on whether anything had touched the state first,
    which is why it looked random.
    """
    from PySide6.QtWidgets import QApplication

    from gini.domain.machine_state import MachineState
    from gini.domain.xv6 import DemoScheduler
    from gini.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow(app if hasattr(app, "processEvents") else QApplication.instance())

    class _Dev:
        id, name, type_key, properties = "d1", "M1", "xv6", {}

    class _Agent:
        pass

    class _Bridge:
        agent = _Agent()
        vm = fs = None

    w.ctx.topology.devices["d1"] = _Dev()
    w._running = True
    w._xv6_providers = {"d1": _Bridge()}

    # The state exists ALREADY and is in demo mode — the situation that made this intermittent.
    ms = MachineState(DemoScheduler(), device_id="d1", mode="demo")
    ms.attach_real(_Bridge(), vm=None, fs=None)
    w.ctx.machine_states["d1"] = ms
    assert getattr(ms.provider, "agent", None) is None, "the demo plane must have no agent"

    assert w._xv6_agent() is not None, (
        "the live bridge was in the registry and was not found: this is the flaky-apps bug")
    assert w._xv6_agent("M1") is not None


def test_no_agent_is_offered_once_the_topology_has_stopped(app, theme):
    """The registry is never cleared on Stop, so it must not be consulted afterwards — otherwise
    the panel talks to a container that no longer exists instead of saying nothing is running."""
    from PySide6.QtWidgets import QApplication

    from gini.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow(QApplication.instance())

    class _Dev:
        id, name, type_key, properties = "d1", "M1", "xv6", {}

    class _Bridge:
        agent = object()

    w.ctx.topology.devices["d1"] = _Dev()
    w._xv6_providers = {"d1": _Bridge()}
    w._running = False
    assert w._xv6_agent() is None


def test_a_named_machine_never_returns_another_machines_agent(app, theme):
    """Two xv6 machines on one canvas. Showing M1's source under a heading that says M2 is worse
    than showing nothing, so a named machine with no live bridge returns None."""
    from PySide6.QtWidgets import QApplication

    from gini.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow(QApplication.instance())

    class _Dev:
        def __init__(self, i, n):
            self.id, self.name, self.type_key, self.properties = i, n, "xv6", {}

    class _Bridge:
        agent = object()

    w.ctx.topology.devices["d1"] = _Dev("d1", "M1")
    w.ctx.topology.devices["d2"] = _Dev("d2", "M2")
    w._running = True
    w._xv6_providers = {"d1": _Bridge()}            # only M1 is live
    assert w._xv6_agent("M1") is not None
    assert w._xv6_agent("M2") is None, "M2 was handed M1's agent"


# --- the one that lied ---------------------------------------------------------------------- #

def test_a_machine_that_cannot_be_reached_is_not_reported_as_having_no_apps(app, theme):
    """It said "That machine reported no apps" when the machine had said nothing at all — which
    sent people looking through the image for programs that were in it the whole time."""
    class _Refusing:
        def __init__(self):
            self.tries = 0

        def get_json(self, path):
            self.tries += 1
            raise ConnectionRefusedError("connection refused")

        def get_text(self, path):
            return ""

    from gini.ui import source_browser as sb
    from gini.ui.source_browser import SourceBrowser

    agent = _Refusing()
    b = SourceBrowser(theme, fetch_fn=lambda: agent)
    b.show_apps("xv6-1")
    _settle(app, b, lambda: "Could not reach" in b._sub.text())
    assert "Could not reach" in b._sub.text()
    assert "reported no apps" not in b._sub.text()
    assert agent.tries == sb._ASK_TRIES, "a refused connection must be retried, not believed"


def test_an_agent_that_comes_up_late_is_still_asked(app, theme):
    """The actual shape on a real machine: the container is up, the panel asks in the same second,
    and the agent socket is not accepting yet. One refusal is not an answer."""
    class _LateAgent:
        def __init__(self):
            self.tries = 0

        def get_json(self, path):
            self.tries += 1
            if self.tries < 2:
                raise ConnectionRefusedError("connection refused")
            return {"programs": ["spin", "walker"]}

        def get_text(self, path):
            return "// an app\nint main(void){return 0;}\n"

    b = SourceBrowser_for(theme, _LateAgent())
    b.show_apps("xv6-1")
    _settle(app, b, lambda: b._jump.count() >= 2)
    assert [b._jump.item(i).text() for i in range(b._jump.count())] == ["spin", "walker"]


def SourceBrowser_for(theme, agent):
    from gini.ui.source_browser import SourceBrowser
    return SourceBrowser(theme, fetch_fn=lambda: agent)


def test_the_panel_asks_for_the_machine_it_is_pointed_at(app, theme):
    """A fetch function that accepts a machine name gets one. Without this the panel shows the
    first xv6 on the canvas under a heading naming a different one."""
    asked = []

    class _Agent:
        def get_json(self, path):
            return {"programs": ["spin"]}

        def get_text(self, path):
            return "// an app\n"

    from gini.ui.source_browser import SourceBrowser

    def fetch(machine=""):
        asked.append(machine)
        return _Agent()

    b = SourceBrowser(theme, fetch_fn=fetch)
    b.show_apps("M2")
    _settle(app, b, lambda: b._jump.count() >= 1)
    assert asked and asked[0] == "M2", f"asked for {asked}"


def test_the_panel_asks_again_once_the_bridges_are_wired(app, theme):
    """The other half of "sometimes". A student who selects the machine while the topology is still
    coming up gets "No running xv6 machine", and nothing tells the panel that the answer changed a
    second later — so it sits there being wrong until they think to click again."""
    from PySide6.QtWidgets import QApplication

    from gini.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow(QApplication.instance())
    sb = w.source_browser

    sb.show_apps("M1")                              # asked too early: nothing is running
    assert "No running xv6 machine" in sb._sub.text()

    class _Agent:
        def get_json(self, path):
            return {"programs": ["spin", "walker"]}

        def get_text(self, path):
            return "// an app\n"

    class _Bridge:
        agent = _Agent()
        vm = fs = None

    class _Dev:
        id, name, type_key, properties = "d1", "M1", "xv6", {}

    w.ctx.topology.devices["d1"] = _Dev()
    w._running = True
    w._xv6_providers = {"d1": _Bridge()}
    w._last_services = []                           # nothing new to wire; the re-ask is the point
    w._wire_xv6_providers()

    _settle(app, sb, lambda: sb._jump.count() >= 2)
    assert [sb._jump.item(i).text() for i in range(sb._jump.count())] == ["spin", "walker"], (
        "the panel never re-asked after the bridges came up")

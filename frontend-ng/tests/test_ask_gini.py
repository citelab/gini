"""Ask GINI rework: 3-pill toolbar (Mode · Model · Activity), red errors, model moved off
the panel, deterministic modes still work with no model."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from gini.ui.mode_indicator import ModeIndicator
from gini.ui.theme.manager import ThemeManager


def _app():
    return QApplication.instance() or QApplication([])


# --- toolbar indicator ------------------------------------------------------ #
def test_four_pills_mode_model_activity_user():
    tm = ThemeManager(_app(), "Dark")
    assert [p[0] for p in ModeIndicator(tm)._pills()] == ["mode", "model", "activity", "user"]


def test_model_pill_shows_name_or_none():
    tm = ThemeManager(_app(), "Dark")
    mi = ModeIndicator(tm)
    mi.set_model("", False)
    assert mi._pills()[1][1] == "no model"
    mi.set_model("llama3.1", True)
    assert mi._pills()[1][1] == "llama3.1"


def test_clicking_the_model_pill_emits_only_there():
    """Hit boxes come from the layout, so no paint is needed for the pills to be clickable."""
    tm = ThemeManager(_app(), "Dark")
    mi = ModeIndicator(tm); mi.set_status("Chat mode", False); mi.set_model("gemma", True)
    mi.set_enrolment("mahesh", True, 2)
    mi.resize(mi.sizeHint().width(), 26)
    model_fired, user_fired = [], []
    mi.model_clicked.connect(lambda: model_fired.append(1))
    mi.user_clicked.connect(lambda: user_fired.append(1))

    def click(x):
        mi.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(x, 13),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    def mid(kind):
        x0, x1 = mi._ranges()[kind]
        return (x0 + x1) / 2

    click(mid("model"))
    assert model_fired == [1] and user_fired == []      # model pill -> Settings
    click(mid("user"))
    assert user_fired == [1] and model_fired == [1]     # user pill -> sign in / my missions
    click(2.0)                                          # mode pill (far left) -> nothing
    assert model_fired == [1] and user_fired == [1]


# --- assistant panel -------------------------------------------------------- #
def _win():
    from gini.ui.main_window import MainWindow
    return MainWindow(_app())


def test_model_indicator_moved_off_the_panel():
    w = _win()
    assert not hasattr(w.assistant, "_model_lbl")           # now in the toolbar


def test_gini_error_message_is_red():
    w = _win(); a = w.assistant
    danger = getattr(w.theme.theme, "danger", "#ff5555")
    assert danger in a._msg_html("GINI", "boom", error=True)
    assert danger not in a._msg_html("GINI", "ok", error=False)   # normal uses text colour


def test_deterministic_commands_work_with_no_model():
    w = _win(); a = w.assistant
    a.set_loop(None); a._llm = None                          # offline, no model
    reply = a._handle("add a router")
    assert reply and "router" in reply.lower()               # command still runs


def test_offline_freeform_points_at_the_model():
    w = _win(); a = w.assistant
    a.set_loop(None); a._llm = None
    reply = a._handle("what is the meaning of the universe")  # not a known command
    assert reply and "model" in reply.lower()                # nudges to connect a model

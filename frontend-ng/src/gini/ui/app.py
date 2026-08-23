"""The QApplication gBuilder runs on.

It exists for ONE reason: to catch QEvent.Quit (macOS ⌘Q and the app-menu Quit) WITHOUT installing
an application-wide event filter.

MainWindow used to do that with `QApplication.instance().installEventFilter(self)`. That reads
like a narrow guard and is not: a filter installed on the *application* receives every event for
every QObject in the process, and PySide marshals each sender into a Python wrapper before the
filter runs. It worked for a long time — until the OS Zoo and the headful Desktop screen embedded
a QWebEngineView, which is internally a QQuickWidget. Moving the mouse over that view delivers
QtQuick hover events to QtQuick's own internal items; PySide has no bindings loaded for those
types (nothing in gBuilder ever imports PySide6.QtQuick), so wrapping them dereferences null:

    EXC_BAD_ACCESS (SIGSEGV) at 0x0000000000000008
      0  PySide::typeName(QObject const*)
      1  PySide::getWrapperForQObject(QObject*, _typeobject*)
      2  QWidgetWrapper::sbk_o_eventFilter(...)
      3  QMainWindowWrapper::eventFilter(QObject*, QEvent*)          <- our Python filter
      4  QCoreApplicationPrivate::sendThroughApplicationEventFilters(...)
      ...
     18  QQuickDeliveryAgentPrivate::sendHoverEvent(...)
     30  QQuickWidget::event(QEvent*)
     31  QtWebEngineWidgets

The Python filter never ran. The crash is in BUILDING ITS ARGUMENT, which is why no amount of
care inside the filter could have prevented it, and why it presented as an unexplained segfault
with a Python traceback that stopped at `app.exec()`. The only fix is to stop tapping every event.

`QApplication.event()` receives only events sent to the application OBJECT — Quit, FileOpen,
ApplicationStateChange, a handful per session — and its argument is a QEvent, never a foreign
QObject. Same guard, none of the exposure. It also takes a Python callable off the hot path
entirely: profiling one late test window measured 355,701 eventFilter dispatches.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


class GiniApplication(QApplication):
    """QApplication that can refuse an application-level Quit.

    `quit_guard` is a callable returning True to CONSUME the quit (and, by convention, to tell the
    user why). MainWindow installs itself here in its constructor. Left as None the class behaves
    exactly like QApplication, so nothing depends on the guard existing.
    """

    def __init__(self, argv=None) -> None:
        super().__init__(argv if argv is not None else [])
        self.quit_guard = None

    def event(self, e: QEvent) -> bool:  # noqa: N802 - Qt naming
        if e.type() == QEvent.Type.Quit and self.quit_guard is not None:
            try:
                if self.quit_guard():
                    return True                  # consumed: stay running
            except Exception:                    # noqa: BLE001
                pass                             # a broken guard must never trap the user in the app
        return super().event(e)

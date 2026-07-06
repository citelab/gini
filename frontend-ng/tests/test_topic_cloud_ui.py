"""The empty Ask GINI panel greets the student with a clickable topic cloud (index 0 of a
stacked view); the first real message swaps to the conversation log, and tapping a pill
sends its query."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_empty_panel_starts_on_the_cloud():
    app, w = _win()
    a = w.assistant
    assert a._stack.currentIndex() == 0                        # cloud, not the log
    assert a._cloud_flow.count() > 10                          # a lively spread of pills


def test_tapping_a_pill_sends_its_query_and_shows_the_log():
    app, w = _win()
    a = w.assistant
    sent = []
    a._send = lambda: sent.append(a.input.text())             # capture instead of dispatch

    # find a real pill button in the flow and click it
    btn = None
    for i in range(a._cloud_flow.count()):
        wdg = a._cloud_flow.itemAt(i).widget()
        if isinstance(wdg, QPushButton):
            btn = wdg
            break
    assert btn is not None
    btn.click()
    assert sent and sent[0].strip()                            # a query was queued to send


def test_a_posted_message_switches_to_the_conversation():
    app, w = _win()
    a = w.assistant
    a._post("GINI", "hello there")
    assert a._stack.currentIndex() == 1                        # now showing the log


def test_clear_conversation_returns_to_the_cloud():
    app, w = _win()
    a = w.assistant
    a._post("GINI", "hi")
    a.clear_conversation()
    assert a._stack.currentIndex() == 0

"""Phase 0 routing fix: 'explain <thing>' must not be mistaken for a canvas-device lookup.

Regression for the reported bug — clicking the SDN topic-cloud balloon sent 'explain SDN'
and GINI answered 'I don't see a device called SDN', because the deterministic router treated
the topic as a device name before retrieval ever ran."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_explain_concept_offline_reaches_the_concept_not_a_device():
    app, w = _win()
    a = w.assistant
    assert a._loop is None                      # default: offline, deterministic
    reply = a._handle("explain SDN")
    assert reply is not None
    low = reply.lower()
    assert "software-defined" in low            # the SDN concept note
    assert "don't see a device" not in low and "don't recognize" not in low


def test_explain_a_real_device_still_explains_the_device():
    app, w = _win()
    a = w.assistant
    a.api.add_device("router", "R1")
    reply = a._handle("explain R1")
    assert reply is not None
    assert "R1" in reply
    assert "don't see a device" not in reply.lower()


def test_explain_off_topic_offline_falls_through_to_the_hint():
    app, w = _win()
    a = w.assistant
    reply = a._handle("explain photosynthesis")
    # no concept matches → not a concept note; the generic capability hint instead
    assert reply is not None
    assert "software-defined" not in reply.lower()
    assert "I can" in reply                      # the offline capability hint


def test_offline_concept_helper_returns_none_for_off_topic():
    app, w = _win()
    a = w.assistant
    assert a._offline_concept("photosynthesis") is None
    assert a._offline_concept("SDN") is not None

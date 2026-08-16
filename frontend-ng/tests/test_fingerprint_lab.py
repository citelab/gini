"""Process Fingerprints view — radar/scatter panel + the classify game (offscreen)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.xv6 import DemoScheduler

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Dev:
    type_key = "xv6"
    name = "M1"
    properties = {"Timeslice": "1"}


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _lab(app):
    from gini.domain.machine_state import MachineState
    from gini.ui.fingerprint_lab import FingerprintLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d")
    return FingerprintLab(None, _theme(app), _Dev(), ms, live=False)


def test_panel_populates_radar_scatter_and_list(app):
    lab = _lab(app)
    assert lab._list.count() == 6                          # the six shipped programs (demo)
    assert len(lab._board._points) == 6                    # each plotted on the behavior map
    assert len(lab._radar._series) == 1                    # the selected process's signature
    lab.close()


def test_classify_game_scores_into_the_confusion_matrix(app):
    from gini.domain.diagnose import Case
    from gini.domain.fingerprint import true_class
    lab = _lab(app)
    g = lab._game                                          # the shared DiagnoseGameWidget
    g._session.current = Case(id="w", signature={}, truth="io-bound", subtitle="writer")
    g._guess("io-bound")                                  # correct
    g._session.current = Case(id="s", signature={}, truth="cpu-bound", subtitle="spin")
    g._guess("io-bound")                                  # wrong (spin is cpu-bound)
    assert g._session.pairs == [("io-bound", "io-bound"), ("cpu-bound", "io-bound")]
    m = g._session.matrix()
    assert m[("io-bound", "io-bound")] == 1               # diagonal (correct)
    assert m[("cpu-bound", "io-bound")] == 1              # off-diagonal (confusion)
    assert "1 / 2" in g._score.text()
    assert true_class("writer") == "io-bound"             # oracle ground truth, used to grade
    lab.close()


def test_reset_clears_game_and_features(app):
    from gini.domain.diagnose import Case
    lab = _lab(app)
    g = lab._game
    g._session.current = Case(id="w", signature={}, truth="memory", subtitle="alloc")
    g._guess("memory")
    assert g._session.pairs
    lab._reset()                                          # the lab's Reset clears the game too
    assert g._session.pairs == [] and "0 / 0" in g._score.text()
    lab.close()


def test_hub_card_opens_the_fingerprint_lab(app):
    from gini.domain.machine_state import MachineState
    from gini.ui.fingerprint_lab import FingerprintLab
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d")
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    assert "fingerprints" in lab._ov_cards                 # the hub card exists
    lab._ov_cards["fingerprints"].clicked.emit()
    assert isinstance(lab._fplab, FingerprintLab)
    lab._fplab.close()
    lab.close()

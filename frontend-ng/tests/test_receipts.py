"""Course → My Receipts: a receipt shown once can be read again.

Students closed the Handed-in dialog without writing the receipt down, and the instructor finds work
by receipt — so the work was handed in and nobody could find it. Every proof stays on disk, and the
receipt is computed from it, so the list is read back from there rather than kept separately.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gini.domain import proof as P

from gini.services import outbox
from gini.services import receipts as R


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini"))
    return tmp_path / "gini"


def _hand_in(code: str, t: float) -> dict:
    """What a hand-in leaves behind: a sealed proof whose last entry is the submit."""
    proof = {"format": "gini-proof", "ticket": code, "assignment": "Experiment-01",
             "entries": [{"seq": 0, "t": t - 60, "kind": P.GENESIS},
                         {"seq": 1, "t": t, "kind": P.SUBMIT}],
             "mac": P.compute_mac(f"head-{code}", code)}
    d = P.proofs_root() / code
    d.mkdir(parents=True, exist_ok=True)
    (d / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    return proof


def test_every_hand_in_is_listed_newest_first_with_the_receipt_it_was_given(home):
    a, b = "A" * 12, "B" * 12
    pa, pb = _hand_in(a, 1_000.0), _hand_in(b, 2_000.0)
    rows = R.handed_in()
    assert [r["receipt"] for r in rows] == [P.receipt_code(pb), P.receipt_code(pa)]
    assert rows[0]["when"] == 2_000.0 and rows[0]["sent"] is True


def test_a_hand_in_still_in_the_outbox_says_it_has_not_been_sent(home):
    p = _hand_in("C" * 12, 1_000.0)
    outbox.queue(p)
    (row,) = R.handed_in()
    assert row["sent"] is False


def test_a_recording_that_was_never_handed_in_has_no_receipt(home):
    d = P.proofs_root() / ("D" * 12)
    d.mkdir(parents=True)
    (d / "chain.jsonl").write_text("", encoding="utf-8")
    (P.proofs_root() / "E" / "proof.json").parent.mkdir(parents=True)
    (P.proofs_root() / "E" / "proof.json").write_text("not json", encoding="utf-8")
    assert R.handed_in() == []


def test_no_proofs_folder_is_an_empty_list(home):
    assert R.handed_in() == []


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_the_latest_receipt_is_selected_and_one_click_copies_it(home, app):
    from gini.ui.receipts_dialog import WAITING, ReceiptsDialog
    _hand_in("A" * 12, 1_000.0)
    latest = _hand_in("B" * 12, 2_000.0)
    outbox.queue(latest)
    w = ReceiptsDialog()
    assert w.table.rowCount() == 2
    assert w.table.item(0, 3).text() == WAITING
    assert w.copy() == P.receipt_code(latest)
    assert app.clipboard().text() == P.receipt_code(latest)
    w.close()


def test_with_nothing_handed_in_it_says_so_instead_of_an_empty_table(home, app):
    from gini.ui.receipts_dialog import ReceiptsDialog
    w = ReceiptsDialog()
    assert not w.table.isVisibleTo(w) and w.empty.isVisibleTo(w)
    assert not w.copy_btn.isEnabled() and w.copy() == ""
    w.close()


def test_the_course_menu_offers_it(home, app):
    from gini.ui.main_window import MainWindow
    win = MainWindow(app)
    from PySide6.QtGui import QAction
    act = next(a for a in win.findChildren(QAction) if a.text() == "My R&eceipts…")
    act.trigger()
    assert win._receipts_dialog.isVisible()
    win._receipts_dialog.close()
    win.close()

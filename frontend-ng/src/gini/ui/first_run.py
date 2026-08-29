"""The first-run panel: get this machine ready to run topologies.

Replaces the `gini-setup` command. The old design asked a student who had just installed the app to
discover and type a second command; the ones who did not got a gBuilder that opened, looked
healthy, and then could not start anything.

Three principles, each one a mistake this project has already made once:

* **Never block the launch.** The window opens first and this appears over it. Somebody with no
  network, on a train, still gets to build and read topologies.
* **Never freeze the GUI.** Pulling images is minutes of work, so it runs on a worker thread and
  reports back through a Signal — the same pattern as `proof_strip` and `fragment_manager`.
* **Ask once before a large download.** "Automatic" should not mean several GB arriving unannounced
  on someone's tethered phone connection. One button, then it remembers.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from ..services import bootstrap


class FirstRunDialog(QDialog):
    """Explains what is missing, does it on request, and stays out of the way otherwise."""

    stepped = Signal(str)
    finished_setup = Signal(dict)

    def __init__(self, plan: dict, parent=None, on_tour=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self._on_tour = on_tour
        self.setWindowTitle("Set up GINI")
        self.setModal(False)              # the canvas stays usable while images download
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        head = QLabel(self._headline())
        head.setStyleSheet("font-size:17px; font-weight:700;")
        root.addWidget(head)

        why = QLabel(plan.get("why", ""))
        why.setWordWrap(True)
        root.addWidget(why)

        # The runtime case is the one we cannot fix for them, so it gets the instructions verbatim
        # rather than a button that would only fail.
        if plan["state"] == bootstrap.NEEDS_RUNTIME:
            rp = plan.get("runtime_plan") or {}
            # A stopped engine needs the START command; an absent one needs the INSTALL steps.
            hint = (rp.get("start", "") if plan.get("runtime_state") == "stopped"
                    else rp.get("manual", "") or rp.get("needs", ""))
            if hint:
                man = QLabel(hint)
                man.setWordWrap(True)
                man.setTextInteractionFlags(Qt.TextSelectableByMouse)
                man.setStyleSheet("font-family:monospace; font-size:12px;")
                root.addWidget(man)

        self.detail = QLabel(f"{plan.get('os','')} · {plan.get('arch','')} · "
                             f"images tagged {plan.get('image_tag','')}")
        self.detail.setStyleSheet("color:palette(mid); font-size:12px;")
        root.addWidget(self.detail)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)           # indeterminate: docker gives us no usable percentage
        self.bar.hide()
        root.addWidget(self.bar)

        row = QHBoxLayout()
        # The launch no longer opens the tour over this panel, so this is the way in. Left-aligned
        # and never the default: it is the optional one of the two things on offer here.
        self.tour = QPushButton("Take the tour")
        self.tour.setAutoDefault(False)
        self.tour.clicked.connect(self._show_tour)
        self.tour.setVisible(on_tour is not None)
        row.addWidget(self.tour)
        row.addStretch(1)
        self.later = QPushButton("Not now")
        self.later.clicked.connect(self.reject)
        row.addWidget(self.later)
        self.go = QPushButton(self._action_label())
        self.go.setDefault(True)
        self.go.clicked.connect(self._start)
        if plan["state"] == bootstrap.NEEDS_RUNTIME:
            self.go.setEnabled(False)     # nothing for it to do
        row.addWidget(self.go)
        root.addLayout(row)

        self.stepped.connect(self._on_step)
        self.finished_setup.connect(self._on_done)

    # -- text ---------------------------------------------------------------- #
    def _headline(self) -> str:
        if (self.plan["state"] == bootstrap.NEEDS_RUNTIME
                and self.plan.get("runtime_state") == "stopped"):
            return "Your container runtime is not running"
        return {
            bootstrap.NEEDS_RUNTIME: "A container runtime is needed",
            bootstrap.BUILD: "Build the container images",
            bootstrap.PULL: "Download the container images",
            bootstrap.UPDATE: "Refresh the container images",
        }.get(self.plan["state"], "Set up GINI")

    def _show_tour(self) -> None:
        if self._on_tour is not None:
            self._on_tour()

    def _action_label(self) -> str:
        return "Build them" if self.plan["state"] == bootstrap.BUILD else "Get them"

    # -- doing it ------------------------------------------------------------ #
    def _start(self) -> None:
        self.go.setEnabled(False)
        self.later.setText("Hide")
        self.bar.show()
        self.detail.setText("Starting…")

        def work():
            result = bootstrap.execute(self.plan, on_step=self.stepped.emit)
            self.finished_setup.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _on_step(self, text: str) -> None:
        self.detail.setText(text)

    def _on_done(self, result: dict) -> None:
        self.bar.hide()
        self.detail.setText(result.get("message", ""))
        self.later.setText("Close")
        if result.get("ok"):
            # Setup is done, so the tour becomes the sensible next step rather than an interruption.
            self.go.hide()
            self.tour.setDefault(True)
            return
        if not result.get("ok"):
            # Offer another go: the commonest cause is a dropped connection, and making them
            # restart the app to retry would be a poor answer to that.
            self.go.setText("Try again")
            self.go.setEnabled(True)


def offer(plan: dict, parent=None, on_tour=None) -> FirstRunDialog | None:
    """Show the panel for a plan that needs something. Returns the dialog, or None if not needed."""
    if not plan or plan.get("state") == bootstrap.READY:
        return None
    dlg = FirstRunDialog(plan, parent, on_tour=on_tour)
    dlg.show()
    dlg.raise_()              # nothing should sit on top of the one thing that has to happen
    dlg.activateWindow()
    return dlg

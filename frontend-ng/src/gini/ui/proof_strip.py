"""The proof-of-activity control at the left of the dashboard strip.

Two states and nothing else:

  * **unarmed** — a code box and one line saying what it is for.
  * **armed** — ``● recording · A3K7 · 47 events`` and a *Generate proof* button.

The recording indicator is the whole reason this lives on the always-visible strip rather than
behind a menu. A student must never do three hours of work and only then discover that nothing was
recorded, so the state is on screen the entire time and the event counter moves as they work.

Thin by design: every decision belongs to `services.proof_recorder`, which is testable without Qt.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


class ProofStrip(QWidget):
    """Arm recording, show that it is recording, and generate the proof."""

    # The recorder is deliberately Qt-free and some of the signals it records arrive on worker
    # threads (a rider's reader thread, the mission worker). Its "chain grew" callback therefore
    # goes through a Signal, which Qt queues onto the GUI thread — touching a widget from the
    # emitting thread would be a crash waiting for a slow afternoon.
    changed = Signal()

    def __init__(self, theme, recorder, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.recorder = recorder
        self.setObjectName("ProofStrip")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 14, 0)
        root.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.code = QLineEdit()
        self.code.setObjectName("ProofCode")
        self.code.setPlaceholderText("assignment code")
        self.code.setMaxLength(20)                 # 12 symbols + the two printed hyphens, with room
        self.code.setFixedWidth(150)
        self.code.returnPressed.connect(self._arm)
        row.addWidget(self.code)

        self.state = QLabel("")
        self.state.setObjectName("ProofState")
        self.state.setTextFormat(Qt.RichText)
        self.state.hide()
        row.addWidget(self.state)

        self.button = QPushButton("Record")
        self.button.setObjectName("ProofButton")
        self.button.clicked.connect(self._clicked)
        row.addWidget(self.button)
        row.addStretch(1)
        root.addLayout(row)

        self.hint = QLabel("")
        self.hint.setObjectName("ProofHint")
        self.hint.setTextFormat(Qt.RichText)
        root.addWidget(self.hint)

        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self._restyle)
        self.changed.connect(self._on_recorder_changed)
        if recorder is not None and hasattr(recorder, "set_on_change"):
            recorder.set_on_change(self.changed.emit)
        self._restyle()
        self.refresh()

    # -- actions ------------------------------------------------------------ #
    def _clicked(self) -> None:
        if self.recorder is not None and self.recorder.armed:
            self._generate()
        else:
            self._arm()

    def _arm(self) -> None:
        if self.recorder is None:
            return
        ok, message = self.recorder.arm(self.code.text())
        if ok:
            self.code.clear()
        # The refusal is shown in the strip, not in a modal: a mistyped code is an everyday
        # slip, and a dialog for it would train students to dismiss dialogs without reading.
        self._say(message, bad=not ok)
        self.refresh(keep_hint=True)

    def _generate(self) -> None:
        if self.recorder is None:
            return
        result = self.recorder.generate_proof()
        if not result.get("ok"):
            self._say(result.get("message", "Could not generate a proof."), bad=True)
            self.refresh(keep_hint=True)
            return
        receipt = result.get("receipt", "")
        self._say(f"Proof generated · receipt <b>{receipt}</b>", bad=False)
        self.refresh(keep_hint=True)
        QMessageBox.information(
            self, "Proof generated",
            f"Your proof was written to:\n{result.get('path', '')}\n\n"
            f"Receipt code: {receipt}\n\n"
            "Hand in the proof file. The receipt is only so you and your instructor can check "
            "at a glance that you are both looking at the same submission.")

    # -- rendering ----------------------------------------------------------- #
    def _on_recorder_changed(self) -> None:
        """The chain grew. Keeps whatever the strip was last saying — an entry recorded a moment
        after 'Proof generated' must not wipe the receipt off the screen."""
        self.refresh(keep_hint=True)

    def refresh(self, keep_hint: bool = False) -> None:
        """Repaint from the recorder's state. Called on every appended entry, so it stays cheap:
        two label writes and a visibility flip."""
        t = self.theme.theme
        s = self.recorder.status() if self.recorder is not None else {"armed": False}
        if s.get("armed"):
            self.code.hide()
            self.state.show()
            self.state.setText(
                f'<span style="color:{t.accent_for("red")}">●</span> '
                f'<span style="color:{t.text};font-weight:700">recording</span> '
                f'<span style="color:{t.faint}">· {s.get("short", "")} · '
                f'{s.get("count", 0)} events</span>')
            self.button.setText("Generate proof")
            if not keep_hint:
                self._say("Your work is being recorded under this code.")
        else:
            self.state.hide()
            self.code.show()
            self.button.setText("Record")
            if not keep_hint:
                self._say("Enter your assignment code to record proof of your work.")

    def _say(self, text: str, bad: bool = False) -> None:
        t = self.theme.theme
        colour = t.danger if bad else t.faint
        self.hint.setText(f'<span style="color:{colour}">{text}</span>')

    def _restyle(self) -> None:
        t = self.theme.theme
        from .theme.manager import sp
        self.setStyleSheet(f"""
            QWidget#ProofStrip {{ border-right: 1px solid {t.line2}; }}
            QLineEdit#ProofCode {{ background: {t.bg3}; color: {t.text};
                                   border: 1px solid {t.line}; border-radius: 5px;
                                   padding: 3px 6px; font-size: {sp(12)}px;
                                   letter-spacing: 1px; }}
            QLabel#ProofState {{ font-size: {sp(12)}px; }}
            QLabel#ProofHint {{ font-size: {sp(9)}px; }}
            QPushButton#ProofButton {{ background: {t.panel2}; color: {t.text};
                                       border: 1px solid {t.line}; border-radius: 5px;
                                       padding: 3px 10px; font-size: {sp(11)}px; }}
            QPushButton#ProofButton:hover {{ border-color: {t.accent}; }}
        """)
        self.refresh(keep_hint=True)

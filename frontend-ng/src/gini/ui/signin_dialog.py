"""Sign in to the Teaching Center.

Two shapes, because a student's first sign-in is a different act from their tenth:

  * **First time (claim).** The teacher enrolled you and handed you an enrolment token. You supply it
    once, choose a password, and the token is spent. This is what stops a classmate from claiming
    your identity: student ids are guessable, so "first password wins" would be an open door.
  * **Every time after.** Student id + password.

The password is used once, to get a session token, and is never stored. If the course server isn't
HTTPS and isn't localhost, the client REFUSES to send it (see `teaching_center.refuse_plaintext_
password`) — and we surface that refusal as an explanation, not a stack trace.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)


class SignInDialog(QDialog):
    def __init__(self, parent, settings, *, first_time: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in to your course")
        self.setMinimumWidth(420)
        self._settings = settings
        root = QVBoxLayout(self)

        head = QLabel(f"<b>{settings.tc_course or 'your course'}</b> · {settings.tc_url}")
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        form = QFormLayout()
        self.student = QLineEdit(settings.tc_student)
        self.student.setPlaceholderText("your username — e.g. ravi")
        self.student.setToolTip("The username your instructor gave you (not your school ID).")
        form.addRow("Username", self.student)

        self.first = QCheckBox("First time — I have an enrolment token")
        self.first.setChecked(bool(first_time))
        form.addRow("", self.first)

        self.enrol = QLineEdit(settings.tc_token)
        self.enrol.setPlaceholderText("the token your instructor gave you")
        form.addRow("Enrolment token", self.enrol)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("at least 8 characters")
        form.addRow("Password", self.password)

        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm password", self.confirm)
        root.addLayout(form)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setObjectName("Faint")
        root.addWidget(self.note)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("Sign in")
        self.bb.accepted.connect(self._accept)
        self.bb.rejected.connect(self.reject)
        root.addWidget(self.bb)

        self.first.toggled.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        """The claim fields only exist for a claim. A returning student shouldn't be asked to confirm
        a password they already have."""
        claiming = self.first.isChecked()
        for w in (self.enrol, self.confirm):
            w.setVisible(claiming)
            lbl = self.layout().itemAt(1).layout().labelForField(w)
            if lbl is not None:
                lbl.setVisible(claiming)
        self.bb.button(QDialogButtonBox.Ok).setText("Set password & sign in" if claiming
                                                    else "Sign in")
        self.note.setText(
            "Your instructor gave you a one-time enrolment token. You'll choose your own password "
            "now; the token is then spent." if claiming else
            "Signing in stores a session on this machine — never your password.")
        self.adjustSize()

    def _error(self, msg: str) -> None:
        self.note.setText(msg)
        self.note.setObjectName("")
        self.note.setStyleSheet("color:#d9534f; font-weight:600;")

    def _accept(self) -> None:
        if not self.student.text().strip():
            return self._error("Enter your student id.")
        pw = self.password.text()
        if len(pw) < 8:
            return self._error("Your password must be at least 8 characters.")
        if self.first.isChecked():
            if not self.enrol.text().strip():
                return self._error("Enter the enrolment token your instructor gave you.")
            if pw != self.confirm.text():
                return self._error("The two passwords don't match.")
        self.accept()

    def values(self) -> dict:
        return {"student": self.student.text().strip(),
                "password": self.password.text(),
                "claim": self.first.isChecked(),
                "enrolment_token": self.enrol.text().strip()}

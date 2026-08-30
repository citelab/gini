"""Teacher: open a student's submission from its receipt.

The marking loop, inside the tool the work was built in. A receipt goes in; what comes back is the
account of what happened — integrity, how long it took, whether it fitted the session window, and
whether the same topology was handed in under another code — and then the student's topology opens
on the canvas, where it can be run.

That last part is the point. A report you cannot run is half a report: the questions a marker
actually has ("does it forward?", "did they wire the second subnet?") are answered by pressing Run,
not by reading a summary. The server writes the download in gBuilder's own project format precisely
so there is no conversion step here.

**No score anywhere.** v1 describes and the teacher judges — the server is deliberate about that,
and this window does not invent one.

Network work goes to a worker thread and comes back through Signals. Every call here can block for
twenty seconds on campus wifi, and freezing the window while a marker works through a stack of
receipts would make the tool useless exactly when it is being used most.
"""
from __future__ import annotations

import pathlib
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..services import tc_staff


def _fmt(rep: dict) -> str:
    """The report as a marker reads it: what it is, whether it holds together, what happened."""
    if not rep:
        return ""
    lines = [
        f"{rep.get('title') or '(untitled)'}   [{rep.get('activity', '')}]",
        f"receipt   {rep.get('receipt', '')}",
        f"integrity {rep.get('verdict') or 'unknown'}",
        f"took      {rep.get('minutes', 0)} min"
        + ("" if rep.get("within_session", True) else "   — LONGER than the code allowed"),
        f"entries   {rep.get('entries', 0)} recorded actions",
        f"runnable  {'yes' if rep.get('runnable') else 'NO — this gBuilder sent only the proof'}",
    ]
    twins = rep.get("twins") or []
    if twins:
        # Flagged, never decided: a shared starter topology is a legitimate reason for two
        # submissions to match, and only the teacher can tell which it is.
        lines.append(f"MATCHES   the same topology was handed in under {len(twins)} other code(s)")
    claims = rep.get("claims") or rep.get("attempts") or []
    if claims:
        lines.append(f"claims    {len(claims)}")
    if rep.get("narration"):
        lines += ["", "What happened:", str(rep["narration"])]
    return "\n".join(lines)


class MarkDialog(QDialog):
    """Sign in as staff if needed, look a receipt up, and open the work."""

    signedIn = Signal(object, str)          # {session, role, who} | None, error
    fetched = Signal(object, str)           # report dict | None, error
    opened = Signal(object, str)            # project dict | None, error
    accepted = Signal(object, str)          # accept answer | None, error

    def __init__(self, ctx, on_open_topology, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._on_open = on_open_topology
        self._report: dict = {}
        self.setWindowTitle("Open a submission")
        self.setMinimumWidth(620)
        # NOT modal, and it does not close when the work opens. A marker reads the account of what
        # happened WHILE looking at the topology it describes — closing the report the moment the
        # canvas fills means holding it in your head, or looking the receipt up twice.
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        url = self._url()
        head = QLabel(f"<b>{url}</b>" if url else
                      "<b>No course server configured</b> — Settings → Teaching Center")
        head.setTextFormat(Qt.RichText)
        root.addWidget(head)

        # -- sign-in (hidden once there is a session) ------------------------- #
        self.auth = QWidget()
        af = QFormLayout(self.auth)
        af.setContentsMargins(0, 0, 0, 0)
        self.who = QLineEdit(getattr(ctx.settings, "tc_student", "") or "")
        self.who.setPlaceholderText("your staff username")
        af.addRow("Username", self.who)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self._sign_in)
        af.addRow("Password", self.password)
        self.claim = QLineEdit()
        self.claim.setPlaceholderText("first time only — from your admin")
        af.addRow("Claim token", self.claim)
        self.signin_btn = QPushButton("Sign in")
        self.signin_btn.clicked.connect(self._sign_in)
        af.addRow("", self.signin_btn)
        root.addWidget(self.auth)

        # -- the receipt ------------------------------------------------------ #
        row = QHBoxLayout()
        self.receipt = QLineEdit()
        self.receipt.setPlaceholderText("receipt code, e.g. 4KTP-9QME")
        self.receipt.returnPressed.connect(self._look_up)
        row.addWidget(self.receipt, 1)
        self.look_btn = QPushButton("Look up")
        self.look_btn.clicked.connect(self._look_up)
        row.addWidget(self.look_btn)
        root.addLayout(row)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(240)
        root.addWidget(self.out, 1)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.open_btn = QPushButton("Open on canvas")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open)
        btns.addWidget(self.open_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        root.addLayout(btns)

        self.signedIn.connect(self._on_signed_in)
        self.accepted.connect(self._on_accepted)
        self.fetched.connect(self._on_fetched)
        self.opened.connect(self._on_opened)
        self._sync_auth()

    # -- state ---------------------------------------------------------------- #
    def _url(self) -> str:
        return (getattr(self.ctx.settings, "tc_url", "") or "").strip()

    def _session(self) -> str:
        return getattr(self.ctx, "staff_session", "") or ""

    def _sync_auth(self) -> None:
        signed = bool(self._session())
        self.auth.setVisible(not signed)
        self.receipt.setEnabled(signed)
        self.look_btn.setEnabled(signed)
        if signed:
            self._say(f"Signed in as {getattr(self.ctx, 'staff_who', '')}.")
        else:
            self._say("Sign in with the same username and password as the course portal.")

    def _say(self, text: str, bad: bool = False) -> None:
        self.hint.setText(text)
        self.hint.setStyleSheet("color: palette(mid);" if not bad else "color: #c0392b;")

    def _busy(self, on: bool) -> None:
        for w in (self.signin_btn, self.look_btn, self.open_btn):
            w.setEnabled(not on and (w is not self.open_btn or bool(self._report.get("runnable"))))

    # -- actions -------------------------------------------------------------- #
    def _sign_in(self) -> None:
        url, who, pw = self._url(), self.who.text().strip(), self.password.text()
        if not url:
            self._say("Set the course server in Settings → Teaching Center first.", bad=True)
            return
        if not (who and pw):
            self._say("Username and password, please.", bad=True)
            return
        claim = self.claim.text().strip()
        self._busy(True)
        self._say("Signing in…")

        def work():
            try:
                self.signedIn.emit(tc_staff.sign_in(url, who, pw, claim), "")
            except Exception as e:                                # noqa: BLE001
                self.signedIn.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_signed_in(self, result, error: str) -> None:
        self._busy(False)
        if not result:
            self._say(error or "Sign-in failed.", bad=True)
            return
        # Held in memory only, for the life of the window. The password is never stored, and a
        # marker on a shared machine leaves nothing behind.
        self.ctx.staff_session = result.get("session", "")
        self.ctx.staff_who = result.get("who", "")
        self.ctx.staff_role = result.get("role", "")
        self.password.clear()
        self.claim.clear()
        self._sync_auth()

    # -- taking a late one by hand ------------------------------------------- #
    def choose_proof_file(self) -> None:
        """Pick the proof file a student still has, and offer it to the course server.

        Not verified here. A local verdict is correct and useless: the submission then exists
        nowhere — not in the gradebook, invisible to every TA. The server runs the same chain check
        and KEEPS it, recording which member of staff waived the deadline.
        """
        if not self._session():
            self._say("Sign in first — accepting a submission is a staff action.", bad=True)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Accept a late submission", "", "Proof files (*.json);;All files (*)")
        if not path:
            return
        import json
        try:
            payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except Exception as e:                                   # noqa: BLE001
            self._say(f"That file could not be read: {e}", bad=True)
            return
        # gBuilder writes the proof itself; a saved submission may wrap it. Accept either shape
        # rather than making a teacher know which one they were handed.
        proof = payload.get("proof") if isinstance(payload, dict) else None
        if not isinstance(proof, dict):
            proof = payload if isinstance(payload, dict) and payload.get("ticket") else None
        if not isinstance(proof, dict):
            self._say("That file does not look like a GINI proof.", bad=True)
            return
        topo = payload.get("topology") if isinstance(payload, dict) else None
        self._busy(True)
        self._say("Offering it to the course server…")

        def work(url=self._url(), s=self._session()):
            try:
                self.accepted.emit(tc_staff.accept(url, s, proof, topo), "")
            except Exception as e:                               # noqa: BLE001
                self.accepted.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_accepted(self, answer, error: str) -> None:
        self._busy(False)
        if not answer:
            self._say(error or "The course server would not take that submission.", bad=True)
            return
        receipt = answer.get("receipt", "")
        self.receipt.setText(receipt)
        self._say(f"Accepted · receipt {receipt} · recorded as taken by "
                  f"{answer.get('accepted_by', 'you')}.")
        self._look_up()          # show the report for what was just filed

    def _look_up(self) -> None:
        code = self.receipt.text().strip()
        if not code:
            return
        self._report = {}
        self.open_btn.setEnabled(False)
        self._busy(True)
        self._say("Asking the course server…")

        def work(url=self._url(), s=self._session()):
            try:
                self.fetched.emit(tc_staff.report(url, s, code), "")
            except Exception as e:                                # noqa: BLE001
                self.fetched.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_fetched(self, rep, error: str) -> None:
        self._busy(False)
        if rep is None:
            self.out.setPlainText("")
            self._say(error or "Could not read that receipt.", bad=True)
            # An expired session is an ordinary end to a working day, not a failure to explain.
            if "session" in (error or "").lower() or "signed in" in (error or "").lower():
                self.ctx.staff_session = ""
                self._sync_auth()
            return
        self._report = rep or {}
        self.out.setPlainText(_fmt(self._report))
        runnable = bool(self._report.get("runnable"))
        self.open_btn.setEnabled(runnable)
        self._say("Open it on the canvas to run it." if runnable else
                  "This submission carried no runnable copy — the report is all there is.")

    def _open(self) -> None:
        self._busy(True)
        self._say("Downloading the topology…")

        def work(url=self._url(), s=self._session(), code=self.receipt.text().strip()):
            try:
                self.opened.emit(tc_staff.topology(url, s, code), "")
            except Exception as e:                                # noqa: BLE001
                self.opened.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_opened(self, project, error: str) -> None:
        self._busy(False)
        if not project:
            self._say(error or "Could not download that topology.", bad=True)
            return
        try:
            self._on_open(project, self._report)
        except Exception as e:                                    # noqa: BLE001
            QMessageBox.warning(self, "Could not open it",
                                f"The submission downloaded, but gBuilder could not open it:\n{e}")
            return
        # Deliberately still open, and pushed behind the window it just filled. The report is the
        # thing you read while you work through the topology, and the next receipt is typed here.
        self._say(f"Open on the canvas. Look up another receipt when you are done with this one.")
        self.lower()

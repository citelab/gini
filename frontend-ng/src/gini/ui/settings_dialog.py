"""Settings dialog — organized into tabs (Appearance, Networking, GINI AI, Naming,
Pricing, Help). Edits the live Settings; the caller applies + persists the result."""
from __future__ import annotations

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QTabWidget, QVBoxLayout, QWidget,
)

_THEMES = ("Dark", "Light", "GINI Brand", "High Contrast", "Sand", "Blue", "Green")
_TEXT_SIZES = ("Normal", "Large", "Extra Large")


def _page(tabs: QTabWidget, title: str) -> QFormLayout:
    """Add a tab whose body is a QFormLayout, and return that form."""
    w = QWidget()
    lay = QVBoxLayout(w)
    form = QFormLayout()
    lay.addLayout(form)
    lay.addStretch(1)
    tabs.addTab(w, title)
    return form


def _note(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Faint")
    lbl.setWordWrap(True)
    return lbl


class SettingsDialog(QDialog):
    """Edits the live Settings; the caller applies + persists the returned values."""

    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("GINI Settings")
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # --- Appearance --------------------------------------------------- #
        appf = _page(tabs, "Appearance")
        self.theme = QComboBox(); self.theme.addItems(_THEMES)
        # Select the CURRENT theme by matching the list itself. This used to use a hand-written
        # {name: index} map that only covered the first four themes — so Sand/Blue/Green fell through
        # to index 0 and the dialog silently reported "Dark". Saving Settings for ANY reason (e.g.
        # editing your Teaching Center credentials) then wrote Dark back and stole your theme.
        # Deriving the index from _THEMES means the two can never drift apart again.
        cur = (settings.theme or "Dark").strip().lower()
        cur = {"brand": "gini brand"}.get(cur, cur)          # tolerate the old short alias
        names = [t.lower() for t in _THEMES]
        self.theme.setCurrentIndex(names.index(cur) if cur in names else 0)
        appf.addRow("Theme", self.theme)
        self.text_size = QComboBox(); self.text_size.addItems(_TEXT_SIZES)
        cur_sz = (getattr(settings, "text_size", "Normal") or "Normal").strip().lower()
        sizes = [s.lower() for s in _TEXT_SIZES]
        self.text_size.setCurrentIndex(sizes.index(cur_sz) if cur_sz in sizes else 0)
        appf.addRow("Text size", self.text_size)
        appf.addRow("", _note("Scales the whole interface — handy on large or high-resolution "
                              "displays. Applies instantly."))
        self.reduced = QCheckBox("Reduce motion / animations")
        self.reduced.setChecked(settings.reduced_motion)
        appf.addRow("", self.reduced)

        # --- Networking --------------------------------------------------- #
        netf = _page(tabs, "Networking")
        self.auto_internet = QCheckBox("Containers get internet automatically (default eth)")
        self.auto_internet.setChecked(settings.auto_internet)
        netf.addRow("", self.auto_internet)
        netf.addRow("", _note("Off = faithful mode: a machine reaches the internet only via "
                              "a drawn Internet element, routed through your network."))

        # --- GINI AI ------------------------------------------------------ #
        aif = _page(tabs, "GINI AI")
        self.llm_enabled = QCheckBox("Use a local LLM for open-ended questions")
        self.llm_enabled.setChecked(settings.llm_enabled)
        aif.addRow("", self.llm_enabled)
        self.llm_url = QLineEdit(settings.llm_url)
        self.llm_url.setPlaceholderText("http://localhost:11434")
        aif.addRow("Server (Ollama)", self.llm_url)
        self.llm_model = QLineEdit(settings.llm_model)
        self.llm_model.setPlaceholderText("llama3.1 / gemma / qwen …")
        aif.addRow("Model", self.llm_model)
        # editing the server/model is a clear intent to use the LLM — auto-tick the box
        self.llm_url.textEdited.connect(lambda *_: self.llm_enabled.setChecked(True))
        self.llm_model.textEdited.connect(lambda *_: self.llm_enabled.setChecked(True))
        self.llm_think = QCheckBox("Reasoning ('thinking') model")
        self.llm_think.setChecked(settings.llm_think)
        aif.addRow("", self.llm_think)
        aif.addRow("", _note("Without an LLM, GINI still builds, explains, traces paths, "
                            "and runs Wizard recipes."))

        # --- Naming ------------------------------------------------------- #
        nf = _page(tabs, "Naming")
        from ..domain.devices import default_prefix
        self._prefix_edits = {}
        for key, label in (("host", "Machine"), ("router", "Router"), ("switch", "Switch"),
                           ("hub", "Hub"), ("instance", "Instance"), ("container", "Container")):
            cur = settings.name_prefixes.get(key, default_prefix(key))
            edit = QLineEdit(cur); edit.setPlaceholderText(default_prefix(key))
            self._prefix_edits[key] = edit
            nf.addRow(label, edit)
        nf.addRow("", _note("e.g. set Machine to “Mach_” for Mach_1, Mach_2, …"))

        # --- Pricing ------------------------------------------------------ #
        from ..domain.pricing import rate_of
        prf = _page(tabs, "Pricing")
        self._price_edits = {}
        for key, label in (("instance", "Instance (VM)"), ("container", "Container"),
                           ("host", "Machine"), ("router", "Router"), ("switch", "Switch"),
                           ("database", "Database"), ("object_store", "Object store"),
                           ("dashboard", "Dashboards (Grafana)")):
            edit = QLineEdit(f"{rate_of(key, settings.prices):g}")
            edit.setValidator(QDoubleValidator(0.0, 100000.0, 2, edit))
            edit.setPlaceholderText(f"{rate_of(key):g}")
            self._price_edits[key] = edit
            prf.addRow(label, edit)
        prf.addRow("", _note("Toy “cloud bill” shown live in the dashboard while a lab runs. "
                            "Blank = use the default rate."))

        # --- Teaching Center ---------------------------------------------- #
        tcf = _page(tabs, "Teaching Center")
        self.tc_url = QLineEdit(settings.tc_url)
        self.tc_url.setPlaceholderText("http://localhost:8080")
        tcf.addRow("Course server", self.tc_url)
        self.tc_course = QLineEdit(settings.tc_course)
        self.tc_course.setPlaceholderText("cs4480-fall26")
        tcf.addRow("Course", self.tc_course)
        self.tc_student = QLineEdit(settings.tc_student)
        self.tc_student.setPlaceholderText("your username — e.g. ravi")
        tcf.addRow("Username", self.tc_student)
        self.tc_token = QLineEdit(settings.tc_token)
        self.tc_token.setPlaceholderText("one-time token from your instructor")
        self.tc_token.setEchoMode(QLineEdit.Password)
        tcf.addRow("Enrolment token", self.tc_token)
        self.tc_insecure = QCheckBox("Allow insecure (plain HTTP) connection")
        self.tc_insecure.setChecked(bool(getattr(settings, "tc_allow_insecure", False)))
        tcf.addRow("", self.tc_insecure)
        tcf.addRow("", _note("Your password is never stored — signing in exchanges it for a session. "
                             "The enrolment token is used ONCE, to claim your account.\n\n"
                             "GINI refuses to send a password over plain HTTP to a remote server: on "
                             "shared wifi anyone could read it. Tick the box above only for a demo on "
                             "a network you trust (localhost is always allowed).\n\n"
                             "Leave the server blank to work offline — Missions then offers the local "
                             "practice catalog."))

        # --- Help & Tour -------------------------------------------------- #
        hf = _page(tabs, "Help")
        self.show_help = QCheckBox("Show the feature tour (Cue Cards) at launch")
        self.show_help.setChecked(settings.show_help_on_launch)
        hf.addRow("", self.show_help)
        hf.addRow("", _note("Reopen the tour any time from Help → Feature Tour."))

        root.addWidget(_note("Saved to ~/.gini/config.json."))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def values(self) -> dict:
        from ..domain.devices import default_prefix
        from ..domain.pricing import DEFAULT_RATES
        prefixes = {}
        for key, edit in self._prefix_edits.items():
            p = edit.text().strip()
            if p and p != default_prefix(key):       # only store real overrides
                prefixes[key] = p
        prices = {}
        for key, edit in self._price_edits.items():
            txt = edit.text().strip()
            if not txt:
                continue
            try:
                val = round(float(txt), 2)
            except ValueError:
                continue
            if val != DEFAULT_RATES.get(key):        # only store real overrides
                prices[key] = val
        return {
            "theme": self.theme.currentText(),
            "text_size": self.text_size.currentText(),
            "reduced_motion": self.reduced.isChecked(),
            "auto_internet": self.auto_internet.isChecked(),
            "llm_enabled": self.llm_enabled.isChecked(),
            "llm_url": self.llm_url.text().strip() or "http://localhost:11434",
            "llm_model": self.llm_model.text().strip() or "llama3.1",
            "llm_think": self.llm_think.isChecked(),
            "name_prefixes": prefixes,
            "prices": prices,                         # was computed but dropped -> KeyError
            "show_help_on_launch": self.show_help.isChecked(),
            "tc_url": self.tc_url.text().strip(),
            "tc_course": self.tc_course.text().strip(),
            "tc_student": self.tc_student.text().strip(),
            "tc_token": self.tc_token.text().strip(),
            "tc_allow_insecure": self.tc_insecure.isChecked(),
        }

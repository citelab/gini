"""Settings dialog — defaults for theme and the GINI AI (local LLM), saved to ~/.gini."""
from __future__ import annotations

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout,
)

_THEMES = ("Dark", "Light", "GINI Brand", "High Contrast")


class SettingsDialog(QDialog):
    """Edits the live Settings; the caller applies + persists the returned values."""

    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("GINI Settings")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)

        lay.addWidget(_head("Appearance"))
        appf = QFormLayout(); lay.addLayout(appf)
        self.theme = QComboBox(); self.theme.addItems(_THEMES)
        cur = (settings.theme or "Dark").lower()
        self.theme.setCurrentIndex(
            {"dark": 0, "light": 1, "gini brand": 2, "brand": 2, "high contrast": 3}
            .get(cur, 0))
        appf.addRow("Theme", self.theme)
        self.reduced = QCheckBox("Reduce motion / animations")
        self.reduced.setChecked(settings.reduced_motion)
        appf.addRow("", self.reduced)

        lay.addWidget(_head("Networking"))
        netf = QFormLayout(); lay.addLayout(netf)
        self.auto_internet = QCheckBox("Containers get internet automatically (default eth)")
        self.auto_internet.setChecked(settings.auto_internet)
        netf.addRow("", self.auto_internet)
        net_note = QLabel("Off = faithful mode: a machine reaches the internet only via a "
                          "drawn Internet element, routed through your network.")
        net_note.setObjectName("Faint"); net_note.setWordWrap(True)
        netf.addRow("", net_note)

        lay.addWidget(_head("GINI AI  ·  local LLM"))
        aif = QFormLayout(); lay.addLayout(aif)
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
        # so filling these in isn't silently ignored
        self.llm_url.textEdited.connect(lambda *_: self.llm_enabled.setChecked(True))
        self.llm_model.textEdited.connect(lambda *_: self.llm_enabled.setChecked(True))
        self.llm_think = QCheckBox("Reasoning ('thinking') model")
        self.llm_think.setChecked(settings.llm_think)
        aif.addRow("", self.llm_think)

        lay.addWidget(_head("Naming  ·  default prefixes"))
        nf = QFormLayout(); lay.addLayout(nf)
        from ..domain.devices import default_prefix
        self._prefix_edits = {}
        for key, label in (("host", "Machine"), ("router", "Router"), ("switch", "Switch"),
                           ("hub", "Hub"), ("instance", "Instance"), ("container", "Container")):
            cur = settings.name_prefixes.get(key, default_prefix(key))
            edit = QLineEdit(cur)
            edit.setPlaceholderText(default_prefix(key))
            self._prefix_edits[key] = edit
            nf.addRow(label, edit)
        pnote = QLabel("e.g. set Machine to “Mach_” for Mach_1, Mach_2, …")
        pnote.setObjectName("Faint"); pnote.setWordWrap(True)
        lay.addWidget(pnote)

        lay.addWidget(_head("Pricing  ·  GINI $ per hour"))
        from ..domain.pricing import rate_of
        prf = QFormLayout(); lay.addLayout(prf)
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
        prnote = QLabel("Toy “cloud bill” shown live in the dashboard while a lab runs. "
                        "Blank = use the default rate.")
        prnote.setObjectName("Faint"); prnote.setWordWrap(True)
        lay.addWidget(prnote)

        note = QLabel("Saved to ~/.gini/config.json. Without an LLM, GINI still builds, "
                      "explains, traces paths, and runs Wizard recipes.")
        note.setObjectName("Faint"); note.setWordWrap(True)
        lay.addWidget(note)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

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
            "reduced_motion": self.reduced.isChecked(),
            "auto_internet": self.auto_internet.isChecked(),
            "llm_enabled": self.llm_enabled.isChecked(),
            "llm_url": self.llm_url.text().strip() or "http://localhost:11434",
            "llm_model": self.llm_model.text().strip() or "llama3.1",
            "llm_think": self.llm_think.isChecked(),
            "name_prefixes": prefixes,
        }


def _head(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("PanelHead")
    return lbl

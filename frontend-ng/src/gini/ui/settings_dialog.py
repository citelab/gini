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
        self.flow_window = QComboBox()
        for _secs in (30, 60, 120, 300):
            self.flow_window.addItem(f"{_secs} seconds", _secs)
        _cur = int(getattr(settings, "flow_hud_window_s", 60) or 60)
        _i = self.flow_window.findData(_cur)
        self.flow_window.setCurrentIndex(_i if _i >= 0 else 1)   # default 60 s
        appf.addRow("Flow HUD window", self.flow_window)
        # Short windows matter here in a way they do not for the Flow HUD: a program launch is
        # over in microseconds, so 1s or 5s is often the RIGHT setting — it keeps a single
        # launch on screen instead of burying it under everything that happened since.
        self.os_window = QComboBox()
        for _secs in (1, 5, 10, 30, 60):
            self.os_window.addItem(f"{_secs} second" + ("" if _secs == 1 else "s"), _secs)
        _cur_os = int(getattr(settings, "os_hud_window_s", 10) or 10)
        _j = self.os_window.findData(_cur_os)
        self.os_window.setCurrentIndex(_j if _j >= 0 else 2)     # default 10 s
        appf.addRow("OS HUD window", self.os_window)
        appf.addRow("", _note("How much history each HUD keeps on screen. The Flow HUD scrolls "
                              "its congestion-window plot within its window; the OS HUD drops "
                              "kernel events older than its window. Short is right for the OS HUD: a "
                              "program launch is over in microseconds, so 1-5s keeps a single "
                              "launch legible instead of burying it."))

        # A DIFFERENT axis from the window above, and worth keeping separate: the window is how
        # much is on screen at once, this is how far back you can scrub to. Long recordings are
        # cheap (a snapshot is only kept when something CHANGES) but the timeline gets denser, so
        # aiming the playhead at a particular moment gets harder the further back it reaches.
        self.os_scrub = QComboBox()
        for _secs in (30, 60, 120, 300, 600):
            self.os_scrub.addItem(f"{_secs} seconds" if _secs < 120 else f"{_secs // 60} minutes",
                                  _secs)
        _cur_scrub = int(getattr(settings, "os_hud_scrub_s", 120) or 120)
        _k = self.os_scrub.findData(_cur_scrub)
        self.os_scrub.setCurrentIndex(_k if _k >= 0 else 2)      # default 2 minutes
        appf.addRow("OS HUD timeline", self.os_scrub)
        appf.addRow("", _note("How far back the OS HUD's scrub timeline reaches. Kernel events "
                              "happen in microseconds, so the HUD is a recorder first and "
                              "scrubbing is the main way to read it. Longer keeps more to go back "
                              "to; shorter makes the playhead easier to aim, because every tick "
                              "on the timeline marks a moment something actually changed."))

        # --- Networking --------------------------------------------------- #
        netf = _page(tabs, "Networking")
        self.auto_internet = QCheckBox("Containers get internet automatically (default eth)")
        self.auto_internet.setChecked(settings.auto_internet)
        netf.addRow("", self.auto_internet)
        netf.addRow("", _note("Off = faithful mode: a machine reaches the internet only via "
                              "a drawn Internet element, routed through your network."))
        self.autobuild = QCheckBox("Build missing lab images automatically")
        self.autobuild.setChecked(bool(getattr(settings, "autobuild_images", False)))
        netf.addRow("", self.autobuild)
        netf.addRow("", _note("The gRouter and SDN controller run from images built on this machine. "
                              "With this on, GINI builds one the first time you Run without it, "
                              "instead of printing a docker build command and stopping. The first "
                              "build takes a couple of minutes; after that it's instant."))

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
        self.twin_enabled = QCheckBox("Reasoning Twin — audit the AI's coverage of what matters")
        self.twin_enabled.setChecked(bool(getattr(settings, "twin_enabled", False)))
        aif.addRow("", self.twin_enabled)
        aif.addRow("", _note("Without an LLM, GINI still builds, explains, traces paths, "
                            "and runs Wizard recipes.\n\n"
                            "The Twin (missions + OS coach) checks each AI turn against GINI's "
                            "ground truth: missed points trigger a revision, and anything still "
                            "missing is flagged as “Also worth a look…”. Needs the LLM."))

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
        self.tc_url.setPlaceholderText("https://localhost:8443")
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
        tcf.addRow("", _note("Your password is never stored — signing in exchanges it for a session. "
                             "The enrolment token is used ONCE, to claim your account.\n\n"
                             "GINI refuses to send a password over plain HTTP to a remote server: on "
                             "shared wifi anyone could read it. Tick the box above only for a demo on "
                             "a network you trust (localhost is always allowed).\n\n"
                             "Leave the server blank to work offline — Missions then offers the local "
                             "practice catalog."))

        # --- Hardware (GINI32 boards) ------------------------------------- #
        # The one thing a board cannot discover for itself. It is the same network for
        # every board in a class, so it is entered here once and then written to each
        # board over USB by Hardware → Set Up a Board.
        hwf = _page(tabs, "Hardware")
        self.board_ssid = QLineEdit(getattr(settings, "board_wifi_ssid", "") or "")
        self.board_ssid.setPlaceholderText("the Wi-Fi this laptop is on — 2.4 GHz")
        hwf.addRow("Lab Wi-Fi name", self.board_ssid)
        self.board_pw = QLineEdit(getattr(settings, "board_wifi_password", "") or "")
        self.board_pw.setEchoMode(QLineEdit.Password)
        hwf.addRow("Lab Wi-Fi password", self.board_pw)
        hwf.addRow("", _note(
            "A GINI32 board joins this network to reach gBuilder, so it must be the SAME "
            "network this laptop is on. ESP32 radios are 2.4 GHz only — a 5 GHz-only "
            "network will never be found.\n\n"
            "Set boards up from Hardware → Set Up a Board with the board plugged in over "
            "USB. That is the only step that needs a cable; everything afterwards is "
            "wireless.\n\n"
            "This password is stored in the config file so boards can be set up without "
            "retyping it. Use the shared lab network here, not a personal account."))

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
            "flow_hud_window_s": int(self.flow_window.currentData() or 60),
            "os_hud_window_s": int(self.os_window.currentData() or 10),
            "os_hud_scrub_s": int(self.os_scrub.currentData() or 120),
            "auto_internet": self.auto_internet.isChecked(),
            "autobuild_images": self.autobuild.isChecked(),
            "llm_enabled": self.llm_enabled.isChecked(),
            "llm_url": self.llm_url.text().strip() or "http://localhost:11434",
            "llm_model": self.llm_model.text().strip() or "llama3.1",
            "llm_think": self.llm_think.isChecked(),
            "twin_enabled": self.twin_enabled.isChecked(),
            "name_prefixes": prefixes,
            "prices": prices,                         # was computed but dropped -> KeyError
            "show_help_on_launch": self.show_help.isChecked(),
            "tc_url": self.tc_url.text().strip(),
            "tc_course": self.tc_course.text().strip(),
            "tc_student": self.tc_student.text().strip(),
            "tc_token": self.tc_token.text().strip(),
            # GINI32: the lab Wi-Fi written to boards over USB. Not stripped of case —
            # SSIDs are case-sensitive — but surrounding whitespace is, because a name
            # pasted from a slide routinely carries a trailing space and the board would
            # then hunt forever for a network that does not exist.
            "board_wifi_ssid": self.board_ssid.text().strip(),
            "board_wifi_password": self.board_pw.text(),
        }

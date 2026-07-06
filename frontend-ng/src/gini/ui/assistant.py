"""In-app "Ask GINI" assistant panel.

Students chat here to build, inspect, and understand topologies. It works offline
using GiniAPI's deterministic command handling and explanations; an LLM backend can
be attached via `set_llm(fn)` to handle free-form questions, and it calls the very
same GiniAPI the MCP server exposes — so an external agent and the in-app assistant
share one brain.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..agent.api import GiniAPI
from ..app import AppContext
from ..domain import all_devices
from .mission_panel import MissionPanel
from .theme import ThemeManager, icons


class Assistant(QWidget):
    # emitted from the LLM worker thread; delivered on the UI thread (queued)
    answer_ready = Signal(str, str)         # (device_name_or_empty, text)
    answer_chunk = Signal(str)              # a streamed token delta (live "typing")
    status_changed = Signal(str, bool)      # (mode label, busy) -> toolbar indicator
    starter_ready = Signal(str, str)        # (type_key, reason) for the Wizard's first element

    def __init__(self, ctx: AppContext, api: GiniAPI, theme: ThemeManager) -> None:
        super().__init__()
        self.setObjectName("Inspector")
        self.ctx = ctx
        self.api = api
        self.theme = theme
        self._llm: Callable[[str], str] | None = None
        self._loop = None     # AgentLoop, set when an LLM (Ollama) is configured
        self._tutor = True    # tutor mode: explanations highlight/animate on the canvas
        self.explain_mode = False   # when on, selecting a device explains it on the canvas
        self.wizard_mode = False    # when on, the input box describes a system to scaffold
        self.coach_mode = False     # when on, GINI reviews the canvas for problems to fix
        self.missions_mode = False  # when on, the student picks + plays an assessed Mission
        self._busy = False    # an LLM answer is in flight
        from ..agent.session import SessionKnowledge
        self._session = SessionKnowledge()   # accumulated GINI knowledge for this session
        self._messages: list[tuple[str, str, bool, bool]] = []   # (role, text, err, md)
        self._brief = ""                  # per-project teacher framing, fed to the model
        self._streaming = False           # a streamed answer is being typed into the log
        self._stream_buf = ""             # accumulated streamed text (for persistence)
        self._last_ref: tuple[str, str] | None = None   # what we last explained (for chips)
        self.answer_ready.connect(self._on_answer)
        self.answer_chunk.connect(self._on_chunk)
        self.starter_ready.connect(self._place_starter)
        self.ctx.bus.canvas_background_clicked.connect(self._on_canvas_background)
        self._ghost_cache: dict = {}              # (goal, type_key) -> [(type_key, reason)]
        ctx.bus.wizard_ghosts_requested.connect(self._resolve_ghosts_async)
        ctx.bus.wizard_ghosts_ready.connect(self._learn_on_goal)   # endorsed types are on-goal
        ctx.bus.topology_changed.connect(self._show_context_chips)  # contextual quick-actions
        ctx.bus.topology_changed.connect(self._refresh_mode_availability)  # gate Wizard on xv6
        ctx.bus.machine_events.connect(self._on_machine_events)     # proactive OS Coach
        self._coach_last_fire = 0.0                                 # cooldown clock
        theme.themeChanged.connect(lambda *_: self._rerender())   # recolor on theme switch

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(False)
        # empty state = a clickable topic cloud (things to explore/build); it swaps to the
        # conversation once anything is posted. An invitation, not a keyword cage.
        # Mission HUD (objective tracker + clock + lives), shown only while a Mission is active,
        # sitting ABOVE the chat so game-master narration flows in the log below it.
        self._mission_panel = MissionPanel(theme)
        self._mission_panel.setVisible(False)
        lay.addWidget(self._mission_panel)
        self._mission_ctrl = None                       # agent.mission_controller.MissionController
        self._mission_profile = None                    # lazily created student profile

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_cloud())     # index 0: the cloud
        self._stack.addWidget(self.log)                # index 1: the conversation
        lay.addWidget(self._stack, 1)

        # a canvas change during a Mission drives the game-master loop (live structural eval)
        ctx.bus.topology_changed.connect(self._on_topology_for_mission)

        # Mode = one exclusive, segmented switch (exactly one active): Chat / Explain / Wizard.
        chips = QHBoxLayout(); chips.setSpacing(4)
        self._chat_btn = QPushButton("Chat")
        self._chat_btn.setObjectName("ModeBtn")
        self._chat_btn.setCheckable(True); self._chat_btn.setChecked(True)
        self._chat_btn.setToolTip("Chat: ask GINI to build, inspect, or explain")
        self._chat_btn.toggled.connect(self._toggle_chat)

        self._explain_btn = QPushButton("Explain")
        self._explain_btn.setObjectName("ModeBtn")
        self._explain_btn.setCheckable(True)
        self._explain_btn.setToolTip("Explain: click any device on the canvas to explain it")
        self._explain_btn.toggled.connect(self._toggle_explain)

        self._wizard_btn = QPushButton("Wizard")
        self._wizard_btn.setObjectName("WizardBtn")
        self._wizard_btn.setCheckable(True)
        self._wizard_btn.setToolTip("Wizard: describe a goal and GINI guides you to build it")
        self._wizard_btn.toggled.connect(self._toggle_wizard)
        self._wizard_btn.setEnabled(False)          # needs a model; set_loop enables it

        self._coach_btn = QPushButton("Coach")
        self._coach_btn.setObjectName("WizardBtn")
        self._coach_btn.setCheckable(True)
        self._coach_btn.setToolTip("Coach: GINI reviews your canvas and tells you what to fix")
        self._coach_btn.toggled.connect(self._toggle_coach)
        # `clicked` fires on EVERY click (even when Coach is already the active mode, which
        # emits no `toggled`), so clicking Coach again re-runs the review after a fix.
        self._coach_btn.clicked.connect(self._on_coach_clicked)
        self._coach_btn.setEnabled(False)           # needs a model; set_loop enables it

        self._missions_btn = QPushButton("Missions")
        self._missions_btn.setObjectName("WizardBtn")
        self._missions_btn.setCheckable(True)
        self._missions_btn.setToolTip("Missions: play an assigned lab as a timed, assessed game")
        self._missions_btn.toggled.connect(self._toggle_missions)
        self._missions_btn.setEnabled(False)        # needs a model; set_loop enables it

        self._mode_group = QButtonGroup(self)       # radio behaviour: exactly one mode active
        self._mode_group.setExclusive(True)
        for b in (self._chat_btn, self._explain_btn, self._wizard_btn, self._coach_btn,
                  self._missions_btn):
            self._mode_group.addButton(b)
            chips.addWidget(b)
        chips.addStretch(1)

        # Tutor is a *modifier*, not a mode — a small "animate on canvas" toggle, set apart.
        self._tutor_box = QPushButton("⚡ Animate")
        self._tutor_box.setObjectName("ModeBtn")
        self._tutor_box.setCheckable(True); self._tutor_box.setChecked(True)
        self._tutor_box.setToolTip("Animate explanations on the canvas (spotlight + callouts)")
        self._tutor_box.toggled.connect(lambda v: setattr(self, "_tutor", v))
        chips.addWidget(self._tutor_box)
        # (the model presence indicator now lives in the toolbar — see ModeIndicator)
        lay.addLayout(chips)

        # "thinking" spinner — shown while an LLM answer is in flight
        self._spinner = QLabel("")
        self._spinner.setObjectName("Muted")
        self._spinner.setVisible(False)
        lay.addWidget(self._spinner)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(300)
        self._spin_dots = 0
        self._spin_timer.timeout.connect(self._spin_tick)

        # follow-up suggestion chips — context-aware, populated after each explanation
        self._follow_box = QWidget()
        self._follow_lay = QHBoxLayout(self._follow_box)
        self._follow_lay.setContentsMargins(0, 0, 0, 0)
        self._follow_lay.setSpacing(6)
        self._follow_box.setVisible(False)
        lay.addWidget(self._follow_box)

        # Missions picker — a VERTICAL, scrollable list. Mission labels are long, so a horizontal
        # chip row would run thousands of px wide and drag the whole window off-screen.
        self._picker = QWidget()
        self._picker_lay = QVBoxLayout(self._picker)
        self._picker_lay.setContentsMargins(0, 0, 0, 0)
        self._picker_lay.setSpacing(4)
        self._picker_scroll = QScrollArea()
        self._picker_scroll.setWidgetResizable(True)
        self._picker_scroll.setWidget(self._picker)
        self._picker_scroll.setFrameShape(QScrollArea.NoFrame)
        self._picker_scroll.setMaximumHeight(240)
        self._picker_scroll.setVisible(False)
        lay.addWidget(self._picker_scroll)

        # Wizard objective banner (shown once a goal is set) — the canvas does the guiding
        self._wz_panel = self._make_wizard_panel()
        lay.addWidget(self._wz_panel)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask GINI to build, inspect, or explain…")
        self.input.returnPressed.connect(self._send)
        send = QPushButton()
        send.setObjectName("Accent")
        send.setIcon(icons.icon("send", "#ffffff", 16))
        send.clicked.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(send)
        lay.addLayout(row)

        self._post("GINI", "Hi! I can build, explain, and teach on the canvas. Try: "
                            "“add a router”, “connect R1 and S1”, “explain this topology”, "
                            "“show path M1 to M2”, or “how does M1 reach M2”. "
                            "Tutor mode highlights and animates as I explain.")
        self._show_cloud()          # start on the topic cloud; first message swaps to the log

    # LLM seam -------------------------------------------------------------- #
    def set_llm(self, fn: Callable[[str], str]) -> None:
        self._llm = fn

    def set_loop(self, loop) -> None:
        """Attach an AgentLoop (Ollama-backed) for open-ended questions. The Wizard needs a
        model, so its button is enabled/disabled with the loop."""
        self._loop = loop
        self._embedder_obj = None         # rebuild the L2 embedder against the new backend
        if loop is not None:
            loop.brief = self._brief      # carry the project's framing onto the new loop
        self._refresh_mode_availability()

    def _embedder(self):
        """The L2 semantic-recall embedder (cached), built against the active model backend.
        Returns a NullEmbeddings (disabled) when there's no backend or no shipped index — so
        retrieval quietly falls back to lexical + LLM-expansion."""
        if getattr(self, "_embedder_obj", None) is not None:
            return self._embedder_obj
        from ..agent import embed
        backend = getattr(self._loop, "backend", None) if self._loop is not None else None
        self._embedder_obj = (embed.OllamaEmbeddings(backend)
                              if backend is not None and hasattr(backend, "embed")
                              else embed.NullEmbeddings())
        return self._embedder_obj

    def _has_xv6(self) -> bool:
        return any(getattr(d, "type_key", "") == "xv6"
                   for d in self.ctx.topology.devices.values())

    def _refresh_mode_availability(self) -> None:
        """Enable/disable the model-gated modes. Wizard is additionally disabled when an xv6
        Machine is present — it's a standalone OS lab, not a topology to build toward a goal
        (signed off: 'Wizard disabled when xv6 runs'). Coach stays on and becomes the OS tutor."""
        has_model = self._loop is not None
        xv6 = self._has_xv6()
        self._coach_btn.setEnabled(has_model)
        self._wizard_btn.setEnabled(has_model and not xv6)
        self._missions_btn.setEnabled(has_model)
        self._missions_btn.setToolTip(
            "Missions: play an assigned lab as a timed, assessed game" if has_model
            else "Missions need a local model — enable one in Settings → LLM")
        if not has_model:
            self._wizard_btn.setToolTip("Wizard needs a local model — enable one in Settings → LLM")
            self._coach_btn.setToolTip("Coach needs a local model — enable one in Settings → LLM")
        else:
            self._coach_btn.setToolTip(
                "Coach: measured, state-grounded help for your xv6 experiment" if xv6
                else "Coach: GINI reviews your canvas and tells you what to fix")
            self._wizard_btn.setToolTip(
                "Wizard doesn't apply to an xv6 Machine — it's a standalone OS lab, not a "
                "topology to build" if xv6
                else "Wizard: describe a goal and GINI guides you to build it")
        # if a now-disabled mode was active, fall back to Chat (a mode must stay active)
        if ((not self._wizard_btn.isEnabled() and self._wizard_btn.isChecked())
                or (not self._coach_btn.isEnabled() and self._coach_btn.isChecked())
                or (not self._missions_btn.isEnabled() and self._missions_btn.isChecked())):
            self._chat_btn.setChecked(True)

    # -- per-project AI state (saved with the project, swapped on project switch) --- #
    def set_brief(self, text: str) -> None:
        """The teacher's framing for the current project — prepended to the model's context."""
        self._brief = text or ""
        if self._loop is not None:
            self._loop.brief = self._brief

    def brief(self) -> str:
        return self._brief

    def ai_state(self) -> dict:
        """Serialisable snapshot: the visible transcript + the model's message history."""
        history = []
        if self._loop is not None:
            for m in self._loop.history[1:]:          # skip the system prompt (regenerated)
                history.append({"role": m.role, "content": m.content, "name": m.name})
        return {"messages": [list(t) for t in self._messages], "history": history}

    def load_ai_state(self, state: dict | None) -> None:
        """Restore a project's conversation into the panel and the model's memory."""
        from ..agent.session import SessionKnowledge
        from ..agent.llm.backend import Message
        state = state or {}
        self._messages = [tuple(m) for m in state.get("messages", [])]
        self._session = SessionKnowledge()            # accumulator re-fills as chat continues
        self._last_ref = None
        self._rerender()
        if self._loop is not None:
            base = self._loop.history[0]
            self._loop.history = [base] + [
                Message(h.get("role", "user"), h.get("content", ""), name=h.get("name"))
                for h in state.get("history", [])]

    def clear_conversation(self) -> None:
        """Blank slate for a brand-new project."""
        from ..agent.session import SessionKnowledge
        self._messages = []
        self._session = SessionKnowledge()
        self._last_ref = None
        self._brief = ""
        self.log.clear()
        if self._loop is not None:
            self._loop.history = self._loop.history[:1]
            self._loop.brief = ""
        self._show_cloud()          # blank slate -> back to the topic cloud

    # chat ------------------------------------------------------------------ #
    def _md_to_html(self, md: str) -> str:
        """Render the model's Markdown (headers, tables, lists, bold, code) to HTML so the
        chat reads like a real assistant, not a raw dump. Qt's Markdown parser emits no
        explicit colours, so the themed palette shows through."""
        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        try:
            doc.setMarkdown(md or "", QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        except Exception:
            import html as _h
            return _h.escape(md or "").replace("\n", "<br>")
        html = doc.toHtml()
        m = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
        return m.group(1) if m else html

    def _msg_html(self, role: str, text: str, error: bool = False,
                  markdown: bool = False) -> str:
        t = self.theme.theme
        label = t.accent if role == "GINI" else t.muted
        body = getattr(t, "danger", "#ff5555") if error else t.text   # errors in red
        lbl = f'<b style="color:{label};">{role}:</b>'
        if markdown and not error:
            return (f'<div style="margin:6px 0;">{lbl}'
                    f'<div style="color:{body};">{self._md_to_html(text)}</div></div>')
        return (f'<p style="margin:6px 0;">{lbl} '
                f'<span style="color:{body};">{text}</span></p>')

    def _post(self, role: str, text: str, error: bool = False,
              markdown: bool = False) -> None:
        self._messages.append((role, text, error, markdown))
        self.log.append(self._msg_html(role, text, error, markdown))
        if hasattr(self, "_stack"):
            self._stack.setCurrentWidget(self.log)   # a message -> show the conversation
        self.ctx.bus.assistant_message.emit(role, text)
        if role == "GINI":
            self._raise_self()          # surface the Ask GINI tab so replies aren't missed

    def _rerender(self) -> None:
        """Re-paint the whole conversation in the current theme's colours (message
        colours are baked into the HTML at post time, so a theme switch must redraw)."""
        self.log.clear()
        for role, text, error, markdown in self._messages:
            self.log.append(self._msg_html(role, text, error, markdown))
        if hasattr(self, "_cloud_flow"):
            self._populate_cloud()                    # recolour the cloud on theme switch

    # -- topic cloud (empty-state) ----------------------------------------- #
    def _build_cloud(self) -> QWidget:
        from .flow_layout import FlowLayout
        w = QWidget()
        outer = QVBoxLayout(w); outer.setContentsMargins(12, 12, 12, 12); outer.setSpacing(8)
        cap = QLabel("Not sure what to ask? Tap a topic to explore or build — or just type "
                     "anything.")
        cap.setObjectName("Muted"); cap.setWordWrap(True)
        outer.addWidget(cap)
        host = QWidget()
        self._cloud_flow = FlowLayout(host, spacing=6)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(host)
        sc.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(sc, 1)
        self._populate_cloud()
        return w

    def _populate_cloud(self) -> None:
        import random
        from ..domain.topic_cloud import topic_cloud
        t = self.theme.theme
        flow = self._cloud_flow
        while flow.count():                            # clear (rebuild on theme change)
            it = flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        items = topic_cloud()
        random.shuffle(items)                          # a little freshness each visit
        items = items[:36]
        for it in items:
            label = ("▶ " + it.label) if it.kind == "recipe" else it.label
            btn = QPushButton(label)
            px = {3: 15, 2: 13, 1: 11}.get(it.weight, 12)
            col = t.accent_for(it.accent) if hasattr(t, "accent_for") else t.accent
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{border:1px solid {col}; color:{col}; border-radius:13px; "
                f"padding:4px 11px; font-size:{px}px; background:transparent;}}"
                f"QPushButton:hover{{background:{col}22;}}")
            btn.clicked.connect(lambda _=False, q=it.query: self._cloud_pick(q))
            flow.addWidget(btn)

    def _cloud_pick(self, query: str) -> None:
        self.input.setText(query)                      # show what was asked, then send it
        self._send()

    def _show_cloud(self) -> None:
        if hasattr(self, "_stack"):
            self._stack.setCurrentIndex(0)           # index 0 is the cloud

    def _raise_self(self) -> None:
        from PySide6.QtWidgets import QDockWidget
        w = self.parent()
        while w is not None and not isinstance(w, QDockWidget):
            w = w.parent()
        if w is not None:
            w.raise_()

    def showEvent(self, e) -> None:            # panel is chat-ready: cursor waits in the input
        super().showEvent(e)
        self.input.setFocus()

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._post("You", text)
        # Wizard's objective is set via its own goal box; the chat box here is for
        # refining / asking (goal-aware via the injected context) — same brain as Q&A.
        try:
            reply = self._handle(text)
        except Exception as e:  # surface errors to the student rather than crashing
            self._post("GINI", f"Sorry, I couldn't do that: {e}", error=True)   # red
            return
        if reply is not None:   # None => an LLM answer is coming asynchronously
            self._post("GINI", reply)
            self._refresh_followups()   # chips appear iff this was an explain (_last_ref set)

    # deterministic intent handling (LLM-free) ------------------------------ #
    def _handle(self, text: str) -> str:
        low = text.lower().strip()
        self._last_ref = None      # cleared by default; explain paths below set it

        # During an active Mission, the student's messages go to the game master (which reasons
        # about their intent), not the general Q&A router.
        if self._mission_ctrl is not None and self._mission_ctrl.active:
            if low in ("end mission", "quit mission", "stop mission"):
                self.end_mission()
                return "Mission ended."
            self._mission_ctrl.ask(text)
            return None

        # Preview launcher (temporary; the real UI surface is a design decision): "/mission <id>"
        # or "start mission <id>" launches a seed archetype from the Game Catalog.
        m = re.match(r"(?:/mission|start mission)\s+([\w-]+)", low)
        if m:
            return self._start_preview_mission(m.group(1))

        if low in ("explain", "explain this", "explain this topology", "what is happening"):
            if self._explain_btn.isChecked():
                self._run_overview()              # already in explain mode: re-explain
            else:
                self._explain_btn.setChecked(True)   # -> _toggle_explain runs the overview
            return None
        if low in ("summary", "summarize", "stats"):
            s = self.api.summary()
            cats = ", ".join(f"{v} {k.lower()}" for k, v in s["by_category"].items()) or "nothing yet"
            return f"{s['devices']} devices, {s['links']} links — {cats}."
        if low in ("status", "context", "what's on the canvas", "whats on the canvas",
                   "what is on the canvas", "what do i have", "show topology"):
            return self.api.context_digest()
        if low in ("list", "what can you add", "device types"):
            labels = ", ".join(sorted({d.label for d in all_devices()}))
            return f"I can add: {labels}."

        if low in ("recipes", "blueprints", "wizard"):
            rs = self.api.list_recipes()
            lines = "\n".join(f"• <b>{r['name']}</b> — {r['summary']} "
                              f"<i>(type “recipe {r['id']}” to lay it out)</i>" for r in rs)
            return "Working blueprints I can lay out for you:<br>" + lines

        m = re.match(r"recipe ([\w-]+)", low)
        if m:
            try:
                res = self.api.apply_recipe(m.group(1))
            except KeyError:
                return (f"I don't have a recipe called “{m.group(1)}”. "
                        "Type “recipes” to see the list.")
            self.ctx.bus.topology_changed.emit()
            return (f"Laid out the <b>{res['name']}</b> blueprint — {len(res['added'])} "
                    f"elements, {res['links']} links. Press Run to start it.")

        m = re.match(r"explain (.+)", low)
        if m:
            target = m.group(1).strip()
            # "explain <thing>" is a DEVICE explanation only when <thing> is actually a placed
            # device; otherwise it's a concept/topic question ("explain SDN") and must reach
            # the retrieval pipeline — not be mistaken for a missing device.
            if self._device_id(target.upper()) is not None:
                return self._show_device(target.upper())
            if self._loop is not None:
                self._ask_gini(text)          # concept/topic -> retrieval pipeline (async)
                return None
            reply = self._offline_concept(target)   # offline: deterministic concept note
            if reply is not None:
                return reply
            # else: fall through to the offline capability hint below

        m = re.match(r"(?:add|create|place)(?: an?| a)? (.+)", low)
        if m:
            return self._add(m.group(1).strip())

        m = re.match(r"connect (.+?) (?:and|to|with) (.+)", low)
        if m:
            r = self.api.connect(m.group(1).strip().upper(), m.group(2).strip().upper())
            return f"Connected {r['source']} ↔ {r['target']}."

        m = (re.match(r"(?:show |trace |animate )?(?:the )?path (?:from )?(\w+) (?:to|->|→) (\w+)", low)
             or re.match(r"how (?:does|do|can|would) (\w+) (?:reach|get to|ping|talk to|connect to) (\w+)", low))
        if m:
            return self._trace_and_show(m.group(1).upper(), m.group(2).upper())

        if self._loop is not None:
            self._ask_gini(text)               # free-form -> understand + retrieve + reason
            return None
        if self._llm is not None:
            return self._llm(text)
        # offline with no model: still show we can see the canvas, then guide.
        hint = ("I can: add a <device>, connect A and B, explain [device], summarize, "
                "status, or list. Connect a local Ollama model (set GINI_LLM_URL) for "
                "open-ended questions.")
        if self.ctx.topology.devices:
            return f"{self.api.context_digest()}\n\n{hint}"
        return hint

    # tutor-mode helpers ---------------------------------------------------- #
    def _chip(self, cmd: str) -> None:
        if cmd == "__path__":
            hosts = [d.name for d in self.ctx.topology.devices.values()
                     if d.type_key in ("host", "instance", "container")]
            if len(hosts) < 2:
                self._post("GINI", "Add two machines and a link, then I'll trace the path.")
                return
            self._post("You", f"show path {hosts[0]} to {hosts[-1]}")
            self._post("GINI", self._trace_and_show(hosts[0], hosts[-1]))
            return
        self.input.setText(cmd)
        self._send()

    # --- follow-up suggestion chips ---------------------------------------- #
    def _clear_followups(self) -> None:
        while self._follow_lay.count():
            item = self._follow_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._follow_box.setVisible(False)

    def _refresh_followups(self) -> None:
        """Show 2-3 context-aware next questions based on what we just explained."""
        self._render_followups(self._followups_for(self._last_ref))

    def _render_followups(self, chips: list[str]) -> None:
        self._clear_followups()
        if not chips:
            return
        for label in chips:
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=label: self._run_followup(c))
            self._follow_lay.addWidget(b)
        self._follow_lay.addStretch(1)
        self._follow_box.setVisible(True)

    def _show_context_chips(self) -> None:
        """In Chat mode, keep contextual quick-actions visible (Show a path when there are
        ≥2 hosts, What needs fixing? when there are warnings) — this is where the demoted
        'Show a path' / 'Status' buttons now live, surfaced only when relevant."""
        if (self.wizard_mode or self.coach_mode or self.missions_mode or self._busy
                or self._last_ref is not None):
            return
        self._render_followups(self._followups_for(("overview", "")))

    def _run_followup(self, text: str) -> None:
        self._clear_followups()
        if text == "Show a path":
            self._chip("__path__")
            return
        if text == "Re-check":                        # Coach: re-scan after fixing
            self._run_coach()
            return
        if text.startswith("Fix "):                   # Coach: explain + how to fix a flagged element
            self.explain_warning(text[4:].strip())
            return
        self.input.setText(text)
        self._send()

    def _followups_for(self, ref: tuple[str, str] | None) -> list[str]:
        if not ref:
            return []
        kind, val = ref
        t = self.ctx.topology
        if kind == "device":
            d = next((x for x in t.devices.values() if x.name == val), None)
            role = d.type_key if d else ""
            if role in ("router", "firewall"):
                return [f"How does {val} forward a packet?",
                        f"What's connected to {val}?", "Compare a router and a switch"]
            if role in ("switch", "hub", "ovs"):
                return [f"How does {val} move frames?",
                        "What's the difference between a switch and a hub?",
                        f"What's connected to {val}?"]
            if role in ("host", "instance", "container"):
                return [f"What is {val}'s gateway?",
                        f"How does {val} reach another machine?", "Show a path"]
            return [f"Why is {val} here?", f"What's connected to {val}?"]
        if kind == "warning":
            return [f"How do I fix {val}?", f"Explain {val}", "Explain my topology"]
        if kind == "type":
            return ["When should I NOT use it?",
                    "Compare it to the alternatives", "Explain my topology"]
        if kind == "overview":
            chips: list[str] = []
            names = [d.name for d in t.devices.values()]
            if names:
                chips.append(f"Explain {names[0]}")
            hosts = sum(1 for d in t.devices.values()
                        if d.type_key in ("host", "instance", "container"))
            if hosts >= 2:
                chips.append("Show a path")
            if self.ctx.warnings:
                chips.append("What needs fixing?")
            return chips[:3]
        return []

    def explain_warning(self, name: str) -> None:
        """Student clicked a device's amber lint badge — explain why it's flagged and
        how to fix it, tying the validation back into the tutor."""
        issues = self.ctx.warnings.get(name) or []
        problem = "; ".join(issues) if issues else "a possible configuration issue"
        self._post("You", f"Why is {name} flagged?")
        self._last_ref = ("warning", name)
        did = self._device_id(name)
        if self._tutor and did:
            self.ctx.bus.present_spotlight.emit([did])
            self.ctx.bus.present_callout.emit(did, self._callout_line(problem))
        if self._loop is not None:
            facts = self.api.explain_device(name)
            self._ask_async(
                f"The student clicked a warning on {name}. The advisory lint says: "
                f"\"{problem}\". Explain in 2-3 sentences why this is flagged and how to "
                f"fix it, for a student. Facts: {facts}", name)
        else:
            self._post("GINI", f"{name}: {problem}. "
                               "Fix it by completing the missing link or gateway above.")
            self._refresh_followups()

    def _toggle_missions(self, on: bool) -> None:
        """Missions mode: pick an assigned lab and play it as a timed, assessed game. Entering
        shows a picker; leaving ends any active mission. Model-gated (the button is disabled
        without a model, so `on` implies a model is attached)."""
        self.missions_mode = on
        self._emit_status()
        if on:
            self.input.setPlaceholderText("Playing a Mission — type to talk to the game master…")
            self._show_mission_picker()
        else:
            self.end_mission()
            self._hide_mission_picker()

    def _show_mission_picker(self) -> None:
        """List the missions the student can play as a VERTICAL list of full-width buttons. Today
        these come from the Game Catalog; once the Teaching Center is wired, from the released
        course manifest."""
        from ..domain import catalog
        while self._picker_lay.count():                 # rebuild the list
            it = self._picker_lay.takeAt(0)
            if it.widget() is not None:
                it.widget().setParent(None)
        self._post("GINI", "Pick a mission to play:")
        for a in catalog.all_archetypes():
            b = QPushButton(a.summary)
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("text-align:left; padding:5px 10px;")
            b.clicked.connect(lambda _=False, aid=a.id: self._start_preview_mission(aid))
            self._picker_lay.addWidget(b)
        self._picker_scroll.setVisible(True)

    def _hide_mission_picker(self) -> None:
        self._picker_scroll.setVisible(False)

    def _toggle_chat(self, on: bool) -> None:
        """Chat = the default mode (no special click behaviour). The Explain/Wizard 'off'
        branches already clear the canvas stage + mission when switching away from them."""
        if not on:
            return
        self.input.setPlaceholderText("Ask GINI to build, inspect, or explain…")
        self._emit_status()
        self._show_context_chips()              # surface contextual chips (Show a path, …)

    def _toggle_coach(self, on: bool) -> None:
        """Coach reviews the CURRENT canvas for problems and tells the student what to fix —
        the corrective complement to the Wizard. Detection is deterministic (the advisory
        lint); the model authors the coaching. Model-gated like Wizard."""
        self.coach_mode = on
        self.input.setPlaceholderText(
            "Coach mode — I'm reviewing your canvas. Ask a follow-up…" if on
            else "Ask GINI to build, inspect, or explain…")
        self._emit_status()
        if on:
            self._coach_toggled = True          # entering (click or setChecked) -> review runs
            self._run_coach()
        else:
            self.ctx.bus.present_clear.emit()

    def _on_coach_clicked(self) -> None:
        # `clicked` fires on EVERY click. When it merely re-selects the already-active Coach
        # button (no `toggled` is emitted), re-run the review — that's the "re-check after a
        # fix" gesture. On the entering click, `toggled` already ran it (guarded below).
        if not self.coach_mode:
            return
        if getattr(self, "_coach_toggled", False):
            self._coach_toggled = False
            return
        self._run_coach()

    def _run_coach(self) -> None:
        ms = self._active_xv6_state()
        if ms is not None:                       # OS Coach: measured, state-grounded help
            self._run_os_coach(ms)
            return
        from ..agent.wizard import coach_prompt
        from ..services.compiler import validate
        self._clear_followups()
        try:
            issues = [i for i in validate(self.ctx.topology) if i.get("level") == "warn"]
        except Exception:
            issues = []
        if not issues:
            self.ctx.bus.present_clear.emit()
            self._post("GINI", "✓ I reviewed your canvas — no problems found. It looks "
                               "complete and valid. Press <b>Run</b> when you're ready.")
            return
        flagged = [d for d in (self._device_id(i["device"]) for i in issues if i.get("device")) if d]
        if self._tutor and flagged:
            self.ctx.bus.present_highlight.emit(flagged)     # draw the eye to the trouble spots
        self._last_ref = None
        if self._loop is not None:                            # model authors the coaching
            self._ask_async(coach_prompt(issues, self.api.context_digest()), "")
        else:                                                 # safety net (Coach is model-gated)
            self._post("GINI", "Things to look at — " + "; ".join(
                f"{i['device']}: {i['message']}" for i in issues[:4]))
        # one tappable fix per flagged element + a re-scan, surfaced as chips
        seen, chips = set(), ["Re-check"]
        for i in issues:
            d = i.get("device")
            if d and d not in seen:
                seen.add(d)
                chips.append(f"Fix {d}")
        self._render_followups(chips[:6])

    def _on_machine_events(self, device_id: str) -> None:
        """A running xv6 produced new teachable moments. If Coach mode is on and this is the
        machine in focus, coach proactively — bounded by the help budget and a short cooldown so
        it nudges rather than nags."""
        import time
        if not self.coach_mode or self._busy:
            return
        ms = self._active_xv6_state()
        if ms is None or getattr(ms, "device_id", None) != device_id:
            return
        if not ms.pending_events() or not ms.ledger.can_help():
            return
        now = time.monotonic()
        if now - getattr(self, "_coach_last_fire", 0.0) < 8.0:     # cooldown
            return
        self._coach_last_fire = now
        self._run_os_coach(ms)

    def _run_os_coach(self, ms) -> None:
        """Coach an xv6 experiment: drain the detected teachable moments, then give ONE Socratic,
        budgeted, logged nudge grounded in the live kernel state (the 'measured help' moat)."""
        from ..agent.wizard import os_coach_prompt
        self._clear_followups()
        events = ms.drain_events()
        ledger = ms.ledger
        if not ledger.can_help():
            self._post("GINI", "You've used all your Coach hints for this run — try reasoning "
                               "it through from the panels (who's running, who's starving, how "
                               "the switches fall), or check with your instructor. Your Coach "
                               "use is logged.")
            self._render_followups(["Re-check", "Step switch"])
            return
        card = ms.card(level=1)
        if self._loop is not None:               # the model authors the Socratic nudge
            ledger.record(events)
            self._ask_async(os_coach_prompt(events, card, ledger.remaining()), "")
        else:                                    # deterministic fallback (Coach is model-gated)
            if events:
                ledger.record(events)
                self._post("GINI", "Notice — " + "; ".join(e.detail for e in events[:2]))
            else:
                self._post("GINI", "The run looks steady. Try slowing the time-slice or "
                                   "spawning another CPU-bound process, and watch the switches.")
        self._render_followups(["Re-check", "Step switch"])

    def _toggle_explain(self, on: bool) -> None:
        """Explain mode: click a DEVICE to explain it; clicking empty canvas reverts to Chat
        (so it doesn't stay stuck on). Chat is the default mode."""
        self.explain_mode = on
        self.input.setPlaceholderText(
            "Explain mode — click any device on the canvas…" if on
            else "Ask GINI to build, inspect, or explain…")
        self._emit_status()
        if on:
            self._run_overview()

    def _on_canvas_background(self) -> None:
        # clicking empty canvas exits Explain back to Chat (device clicks still explain).
        # Wizard/Coach are deliberate multi-step flows, so they stay put.
        if self.explain_mode:
            self._chat_btn.setChecked(True)      # -> _toggle_chat / _toggle_explain(False)
        else:
            self.ctx.bus.present_clear.emit()      # exit: clear the stage

    def _toggle_wizard(self, on: bool) -> None:
        """Wizard mode = X-ray with a goal. Describe an objective ("a multi-LAN IP network")
        and GINI guides you toward it: it highlights on-goal elements, long-press shows only
        the connections that serve the goal, and off-goal drops get flagged. You build it."""
        self.wizard_mode = on
        self._wz_panel.setVisible(on)
        self._wz_banner_box.setVisible(on and self.ctx.mission is not None)
        self.input.setPlaceholderText(
            "Refine the goal or ask GINI…" if on
            else "Ask GINI to build, inspect, or explain…")
        self._emit_status()
        if on:
            self._clear_followups()
            self._post("GINI", "Wizard mode on. Type your objective above and press <b>Set</b> "
                               "— e.g. “a multi-LAN IP network”, “a cloud service on "
                               "Kubernetes”. I'll reason about what it needs and guide you to "
                               "build it: on-goal elements are highlighted, and long-pressing "
                               "shows only the connections that fit the goal.")
        elif self.ctx.mission is not None:
            self.ctx.set_mission(None)                # leaving guided mode clears the objective

    def _run_overview(self) -> None:
        self._last_ref = ("overview", "")
        self._spotlight_hub()
        hint = "Click any device on the canvas to explain it. Toggle Explain off to exit."
        if self._loop is not None:
            self._ask_async("Give a 2-3 sentence overview of the current topology for a "
                            "student, and note the most important device.", "")
            self._post("GINI", hint)
        else:
            narration = self.api.explain_topology()
            self._explain_stage(narration)
            self._post("GINI", narration + "  ·  " + hint)
            self._refresh_followups()

    def _spotlight_hub(self) -> None:
        t = self.ctx.topology
        if self._tutor and t.devices:
            hub = max(t.devices.values(), key=lambda d: t.degree(d.id))
            self.ctx.bus.present_spotlight.emit([hub.id])

    def _device_id(self, name: str) -> str | None:
        return next((d.id for d in self.ctx.topology.devices.values()
                     if d.name == name), None)

    def _show_device(self, name: str) -> str | None:
        """Explain one device and put it on the canvas. With an LLM connected, the
        explanation is authored by the model (async, in the shared conversation, so it
        remembers what was discussed before); offline it uses the deterministic facts.
        Spotlight moves immediately so the click feels instant. Returns None when the
        answer is coming asynchronously (it posts itself)."""
        did = self._device_id(name)
        if did is None:
            return f"I don't see a device called {name}."
        self._last_ref = ("device", name)
        if self._tutor:
            self.ctx.bus.present_spotlight.emit([did])     # instant
        if self._loop is not None:
            facts = self.api.explain_device(name)          # ground the model in real facts
            self._ask_async(f"The student is now looking at {name}. Explain it for them in "
                            f"2-3 sentences, connecting it to what we discussed. Facts: {facts}",
                            name)
            return None
        text = self.api.explain_device(name)               # deterministic fallback
        if self._tutor:
            self.ctx.bus.present_callout.emit(did, self._callout_line(text))
        return text

    # --- Missions: run an authored Lesson as a game -------------------------- #
    def start_mission(self, lesson) -> bool:
        """Launch a Mission for `lesson` (a domain.lesson.Lesson). Requires a model (Missions
        are LLM-gated). Shows the objective tracker and hands control to the game master."""
        from ..agent.mission_controller import MissionController
        from ..domain import profile as _profile
        if self._loop is None:
            self._post("GINI", "Missions need a local model — enable one in Settings → LLM.")
            return False
        if self._mission_profile is None:
            self._mission_profile = _profile.Profile(getattr(self.ctx.settings, "student_id", "local"))
        self._mission_ctrl = MissionController(
            get_topology=lambda: self.ctx.topology,
            llm=self._quick_llm,
            post=lambda role, tx: self._post(role, tx, markdown=True),
            make_runner=self._mission_runner,
            panel=self._mission_panel,
            profile=self._mission_profile)
        self._hide_mission_picker()         # drop the picker once we're playing
        self._mission_panel.setVisible(True)
        ok = self._mission_ctrl.start(lesson)
        if not ok:
            self.end_mission()
        return ok

    def end_mission(self) -> None:
        self._mission_ctrl = None
        self._mission_panel.setVisible(False)
        self._hide_mission_picker()

    def _start_preview_mission(self, archetype_id: str) -> str | None:
        """Launch a seed Game-Catalog archetype for a quick preview (temporary command)."""
        from ..domain import catalog, lesson as _lesson
        arch = catalog.get(archetype_id)
        if arch is None:
            ids = ", ".join(a.id for a in catalog.all_archetypes())
            return f"No such mission. Try one of: {ids}."
        les = _lesson.from_archetype(archetype_id, catalog.demo_params(archetype_id),
                                     id=f"preview-{archetype_id}", title=arch.summary,
                                     brief=arch.summary, time_limit="20m")
        self.start_mission(les)
        return None

    def _mission_runner(self):
        """A behavioral probe runner from the live runtime, if one is reachable — else None so
        behavioral objectives stay pending (structural still evaluates live). Wired defensively;
        real probing needs the orchestrator + a running stack (Mac-side)."""
        orch = getattr(self.ctx, "orchestrator", None)
        if orch is None:
            return None
        try:
            from ..services.probe_runner import DockerProbeRunner
            return DockerProbeRunner(orch)
        except Exception:
            return None

    def _on_topology_for_mission(self) -> None:
        if self._mission_ctrl is not None and self._mission_ctrl.active:
            self._mission_ctrl.on_canvas_changed()

    def _offline_concept(self, target: str) -> str | None:
        """With no model attached, answer 'explain <topic>' from the matching concept note
        (deterministic). Returns the note text, or None if nothing matches (caller falls
        through to the generic capability hint)."""
        try:
            from ..agent import recall
        except Exception:
            return None
        c, strength = recall.best_concept("explain " + target)
        if c is None or strength == "empty":
            return None
        self._last_ref = ("concept", c.key)
        return f"**{c.title}**\n\n{c.body}"

    @staticmethod
    def _callout_line(text: str) -> str:
        """One glanceable line for an on-canvas callout (first sentence). The full
        explanation lives in the right pane — the callout just labels the element."""
        import re
        first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
        return first if len(first) <= 130 else first[:127] + "…"

    def explain_selected(self, device_name: str) -> None:
        """Called when the user selects a device while in explain mode."""
        if self.explain_mode:
            reply = self._show_device(device_name)
            if reply is not None:                          # deterministic; async posts itself
                self._post("GINI", reply)
                self._refresh_followups()

    def explain_element_type(self, type_key: str) -> None:
        """Explain a palette element TYPE (Router/Switch/Hub/…): what it is + when to use
        it, grounded in the element guide and authored by the LLM when connected."""
        from ..domain.devices import REGISTRY
        dt = REGISTRY.get(type_key)
        label = dt.label if dt else type_key
        self._post("You", f"What is a {label}, and when do I use it?")
        self._last_ref = ("type", type_key)
        facts = self.api.explain_element_type(type_key)
        if self._loop is not None:
            self._ask_async(
                f"The student is asking about the '{label}' element from the palette. "
                f"Explain what it is and WHEN to use it (vs. similar elements), for a "
                f"student, in 2-4 sentences. Reference: {facts}", "")
        else:
            self._post("GINI", facts)        # palette element — answer in the right pane
            self._refresh_followups()

    # --- spinner + status -------------------------------------------------- #
    def _emit_status(self) -> None:
        mode = ("Missions mode" if self.missions_mode
                else "Wizard mode" if self.wizard_mode
                else "Coach mode" if self.coach_mode
                else "Explain mode" if self.explain_mode else "Chat mode")
        self.status_changed.emit(mode, self._busy)

    # --- Wizard mode: an objective that guides X-ray (no auto-build) ---------- #
    def _make_wizard_panel(self) -> QWidget:
        panel = QWidget()
        pl = QVBoxLayout(panel); pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(6)
        cap = QLabel("Describe what you want to build"); cap.setObjectName("Muted")
        pl.addWidget(cap)
        inrow = QHBoxLayout(); inrow.setSpacing(6)
        self._wz_goal = QLineEdit()
        self._wz_goal.setPlaceholderText("e.g. a multi-LAN IP network")
        self._wz_goal.returnPressed.connect(self._set_goal_from_input)
        setbtn = QPushButton("Set"); setbtn.setObjectName("Accent")
        setbtn.setCursor(Qt.PointingHandCursor)
        setbtn.clicked.connect(self._set_goal_from_input)
        inrow.addWidget(self._wz_goal, 1); inrow.addWidget(setbtn)
        pl.addLayout(inrow)
        # the "🎯 Building: …" banner appears once a goal is set
        self._wz_banner_box = QWidget(); self._wz_banner_box.setObjectName("GoalBanner")
        brow = QHBoxLayout(self._wz_banner_box); brow.setContentsMargins(12, 8, 12, 8)
        self._wz_banner = QLabel(""); self._wz_banner.setWordWrap(True)
        brow.addWidget(self._wz_banner, 1)
        self._wz_clear = QPushButton("Clear"); self._wz_clear.setObjectName("Chip")
        self._wz_clear.setCursor(Qt.PointingHandCursor)
        self._wz_clear.clicked.connect(self._clear_mission)
        brow.addWidget(self._wz_clear)
        self._wz_banner_box.setVisible(False)
        pl.addWidget(self._wz_banner_box)
        panel.setVisible(False)
        return panel

    def _set_goal_from_input(self) -> None:
        goal = self._wz_goal.text().strip()
        if not goal:
            return
        self._wz_goal.clear()
        self._post("You", goal)
        self._set_mission(goal)

    def _set_mission(self, goal: str) -> None:
        """Set the objective and let the model drive: it picks a starter element (placed
        for the student), and from then on it filters each element's neighbours to the
        goal. Requires a connected model (the Wizard button is disabled otherwise)."""
        from ..domain import missions
        goal = (goal or "").strip()
        if not goal:
            return
        if self._loop is None:
            self._post("GINI", "The Wizard needs a local model. Enable one in Settings → LLM.")
            return
        self._ghost_cache = {}
        # One source of truth: the on-goal set is what the MODEL endorses (the starter +
        # each approved neighbour), grown as we build — never a separate keyword guess.
        mission = missions.Mission(goal, frozenset(), None)
        self.ctx.set_mission(mission)
        self._show_mission(mission)
        self._post("GINI", f"Thinking about how to start “{goal}”…")
        self._pick_starter_async(goal)

    def _add_on_goal(self, types) -> None:
        """Grow the mission's on-goal set with element types the model has endorsed, so the
        off-goal flag never contradicts what the Wizard itself placed/suggested."""
        from ..domain.missions import Mission
        m = self.ctx.mission
        if m is None:
            return
        new = frozenset(set(m.types) | set(types))
        if new != m.types:
            self.ctx.set_mission(Mission(m.goal, new, m.first))

    def _learn_on_goal(self, _device_id: str, items) -> None:
        self._add_on_goal({t for t, _r in items})

    # -- LLM helpers (quiet, stateless — don't pollute the chat history) ------- #
    def _llm_complete(self, prompt: str,
                      system: str = "You are GINI, a precise gBuilder assistant. Be brief.") -> str:
        from ..agent.llm.backend import Message
        out = []
        for c in self._loop.backend.chat([Message("system", system), Message("user", prompt)]):
            if c.text:
                out.append(c.text)
        return "".join(out)

    def _canvas_summary(self) -> str:
        devs = list(self.ctx.topology.devices.values())
        if not devs:
            return "nothing yet"
        return ", ".join(f"{d.name} ({d.type.label})" for d in devs[:12])

    def _pick_starter_async(self, goal: str) -> None:
        import threading
        from ..agent import wizard as wz
        catalog, names = wz.element_catalog(), wz.element_names()

        def work():
            # Ask, validate, and RE-ASK (up to 3) — never guess. The retry prompt is terse and
            # demands one exact element name. If still no valid pick, we ask the user.
            prompts = [wz.starter_prompt(goal, catalog),
                       wz.starter_retry_prompt(goal, names),
                       wz.starter_retry_prompt(goal, names)]
            key, reason, last = "", "", ""
            for p in prompts:
                try:
                    text = self._llm_complete(p)
                except Exception:                      # noqa: BLE001
                    text = ""
                last = text
                k, r = wz.parse_starter(text)
                if k:
                    key, reason = k, r
                    break
            snippet = " ".join((last or "(empty)").split())[:300]
            self.ctx.log(f"Wizard starter — model said: “{snippet}” → parsed: {key or '(none)'}",
                         "info")
            self.starter_ready.emit(key, reason)
        threading.Thread(target=work, daemon=True).start()

    def _place_starter(self, type_key: str, reason: str) -> None:
        from ..domain.devices import REGISTRY
        if self.ctx.mission is None:
            return
        if not type_key or type_key not in REGISTRY:   # no valid pick after retries — ask, don't guess
            self._post("GINI", f"I couldn't settle on a clear first element for "
                               f"“{self.ctx.mission.goal}”. Could you make the goal more specific "
                               "(e.g. name the kind of network or service), or tell me which "
                               "element to start with? I'd rather ask than guess.")
            return
        self._add_on_goal({type_key})              # the starter is on-goal by definition
        devs = list(self.ctx.topology.devices.values())
        x = (max(d.x for d in devs) + 320.0) if devs else 220.0
        y = (sum(d.y for d in devs) / len(devs)) if devs else 200.0
        d = self.api.add_device(type_key, x=x, y=y)
        self.ctx.select(d["id"])
        label = REGISTRY[type_key].label
        self._post("GINI", f"Start with a <b>{label}</b> — {reason or 'the foundation for this goal'}. "
                           "Tap a glowing suggestion to add the next piece.")
        self.ctx.bus.wizard_ghosts_requested.emit(d["id"])     # auto-show its goal ghosts

    def _resolve_ghosts_async(self, device_id: str) -> None:
        """Filter an element's grammar-valid neighbours to the goal (one batched LLM call,
        cached per goal+type). Emits wizard_ghosts_ready for the canvas to draw."""
        from ..domain import connection_rules as cr
        from ..domain.devices import REGISTRY
        m = self.ctx.mission
        d = self.ctx.topology.devices.get(device_id)
        if m is None or d is None:
            return
        partners = cr.partners_for(d.type_key)
        candidates = [(p.type_key, REGISTRY[p.type_key].label) for p in partners]
        if not candidates:
            self.ctx.bus.wizard_ghosts_ready.emit(device_id, [])
            return
        grammar_items = [(p.type_key, p.why) for p in partners]
        key = (m.goal, d.type_key)
        if key in self._ghost_cache:
            self.ctx.bus.wizard_ghosts_ready.emit(device_id, self._ghost_cache[key])
            return
        if self._loop is None:                          # safety: no model -> grammar ring
            self.ctx.bus.wizard_ghosts_ready.emit(device_id, grammar_items)
            return
        import threading
        from ..agent import wizard as wz
        goal, cur = m.goal, REGISTRY[d.type_key].label
        summary = self._canvas_summary()

        def work():
            try:
                text = self._llm_complete(wz.filter_prompt(goal, cur, candidates, summary))
                items = wz.parse_filter(text, candidates) or grammar_items
            except Exception:                          # noqa: BLE001
                items = grammar_items
            self._ghost_cache[key] = items
            self.ctx.bus.wizard_ghosts_ready.emit(device_id, items)
        threading.Thread(target=work, daemon=True).start()

    def _show_mission(self, mission, refined: bool = False, speak: bool = True) -> None:
        t = self.theme.theme
        self._wz_banner.setText(
            f'<span style="color:{t.accent};font-weight:700">🎯 Building:</span> '
            f'<span style="color:{t.text}">{mission.goal}</span>')
        self._wz_banner_box.setVisible(True)
        self._wz_panel.setVisible(self.wizard_mode)

    def _clear_mission(self) -> None:
        self.ctx.set_mission(None)
        self._ghost_cache = {}
        self._wz_banner_box.setVisible(False)
        self._post("GINI", "Goal cleared — X-ray is back to showing every valid connection.")

    def _start_spinner(self, what: str = "GINI is thinking") -> None:
        self._busy = True
        self._spin_base = what
        self._spin_dots = 0
        self._spinner.setText(what + "…")
        self._spinner.setVisible(True)
        self._spin_timer.start()
        self._emit_status()

    def _stop_spinner(self) -> None:
        self._busy = False
        self._spin_timer.stop()
        self._spinner.setVisible(False)
        self._spinner.setText("")
        self._emit_status()

    def _spin_tick(self) -> None:
        self._spin_dots = (self._spin_dots + 1) % 4
        self._spinner.setText(self._spin_base + "." * self._spin_dots)

    # --- Ask GINI pipeline: understand -> retrieve -> route -> reason ------- #
    def _mode_name(self) -> str:
        return ("coach" if self.coach_mode else "wizard" if self.wizard_mode
                else "explain" if self.explain_mode else "chat")

    def _quick_llm(self, prompt: str) -> str:
        """A one-shot completion on the same model — for understanding-refine and the
        session summariser. Runs on the worker thread (never blocks the UI)."""
        try:
            from ..agent.llm.backend import Message
            out = []
            for ch in self._loop.backend.chat([Message("user", prompt)], tools=None):
                if ch.text:
                    out.append(ch.text)
            return "".join(out)
        except Exception:
            return ""

    def _ask_gini(self, text: str) -> None:
        """Front door for free-form questions: interpret, retrieve GINI knowledge, and
        route (build a recipe, reason with grounded context, or clarify)."""
        from ..agent import ask, kb
        from ..agent import understand as U
        names = [d.name for d in self.ctx.topology.devices.values()]
        # deterministic parse on the UI thread (fast, non-blocking); the model-refine and
        # summariser run later on the worker thread.
        intent = U.understand(text, canvas_names=names, mode=self._mode_name())
        retrieval = kb.retrieve(intent, topology=self.ctx.topology)
        plan = ask.plan(intent, retrieval)
        if plan.action == "clarify":
            self._post("GINI", plan.clarify)
            return
        if plan.action == "build_recipe":
            self._build_recipe(plan.recipe_id)
            return
        offer = plan.recipe_id if plan.offer_build else ""
        self._ask_async(text, "", grounded=(intent, retrieval, offer))

    def _build_recipe(self, recipe_id: str) -> None:
        """Auto-build a vetted example on the canvas and narrate it (deterministic — the
        elements are authored, so this can't produce a broken topology)."""
        from ..domain import recipes
        rec = recipes.get_recipe(recipe_id)
        if rec is None:
            self._ask_async(recipe_id, "")
            return
        try:
            res = self.api.apply_recipe(recipe_id)
        except Exception as e:
            self._post("GINI", f"I couldn't build that: {e}", error=True)
            return
        self.ctx.bus.topology_changed.emit()
        lines = [f"Built a **{rec.name}** on the canvas — {len(res['added'])} elements, "
                 f"{res['links']} links. Press **Run** to start it.", "", rec.summary]
        whys = [f"- **{el.type_key}** — {el.why}" for el in rec.elements if el.why]
        if whys:
            lines += ["", "**What each piece does:**"] + whys
        self._post("GINI", "\n".join(lines), markdown=True)

    def _active_xv6_state(self):
        """The MachineState the student is focused on, if any: the selected xv6 Machine, else
        the sole xv6 Machine when there's exactly one. Returns None otherwise."""
        states = getattr(self.ctx, "machine_states", {}) or {}
        if not states:
            return None
        devs = self.ctx.topology.devices
        sel = self.ctx.selected_id
        if sel in states and getattr(devs.get(sel), "type_key", "") == "xv6":
            return states[sel]
        xv6 = [ms for did, ms in states.items()
               if getattr(devs.get(did), "type_key", "") == "xv6"]
        return xv6[0] if len(xv6) == 1 else None

    def _active_machine_card(self, prompt: str) -> str:
        """The live xv6 state card for the grounded context (empty when no xv6 focus). Depth
        scales with the question so the small-LLM budget stays lean."""
        try:
            from ..agent import ask
            ms = self._active_xv6_state()
            if ms is None:
                return ""
            return ms.card(level=ask.machine_card_level(prompt))
        except Exception:
            return ""

    # --- async LLM plumbing: one shared conversation, off the UI thread ----- #
    def _ask_async(self, prompt: str, device: str, grounded=None) -> None:
        import threading
        # one place for "waiting" feedback: the spinner in the pane (no canvas popup).
        about = f" about {device}" if device and not device.startswith("__") else ""
        self._start_spinner("GINI is thinking" + about)
        self._streaming = False
        self._stream_buf = ""
        self._clear_followups()                  # hide stale suggestions while answering

        def work():
            from ..agent import ask, kb
            from ..agent.loop import visible_text
            if grounded is not None:
                intent, retrieval, offer_rid = grounded
                # Re-run retrieval WITH the model + embedder now that we're on the worker
                # thread — this is where the L1 (LLM query-expansion) and L2 (semantic) fallbacks
                # fire, since they do I/O. The UI-thread pass (for routing) was lexical-only.
                retrieval = kb.retrieve(intent, topology=self.ctx.topology,
                                        llm=self._quick_llm, embedder=self._embedder())
                # accumulate this turn's knowledge (small-LLM summary if over budget), then
                # assemble the full grounded context the reasoning model sees, with a grounding
                # stance derived from how strongly the KB matched (closed vs. open-but-fenced).
                self._session.add(retrieval.cards, llm=self._quick_llm)
                mcard = self._active_machine_card(prompt)
                stance = ask.grounding_stance(retrieval, intent)
                ctx = ask.grounded_context(kb.always_on_context(), self._session.as_context(),
                                           retrieval, self.api.context_digest(), intent,
                                           machine_card=mcard, stance=stance)
                if offer_rid:
                    ctx += (f"\n\nIf it would help, end by offering to build the "
                            f"'{offer_rid}' example (the student can say 'show me').")
                self._loop.extra_context = ctx
            raw_parts: list[str] = []
            try:
                text = self._loop.send(prompt, on_text=raw_parts.append)
            except TypeError:
                text = self._loop.send(prompt)   # older loop signature (no streaming)
            except Exception as e:
                raw_parts, text = [], f"(LLM error: {e})"
            finally:
                self._loop.extra_context = ""    # never leak grounding into the next turn
            raw = "".join(raw_parts) or text or ""
            self.answer_ready.emit(device or "", visible_text(raw) or "Done.")
        threading.Thread(target=work, daemon=True).start()

    def _on_chunk(self, delta: str) -> None:
        """A streamed token arrived — begin (or continue) typing it into the pane."""
        if not delta:
            return
        if not self._streaming:
            self._stop_spinner()                 # the answer is appearing; drop the spinner
            self._begin_stream()
            self._streaming = True
        self._stream_buf += delta
        self._stream_insert(delta)

    def _begin_stream(self) -> None:
        t = self.theme.theme
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        if not self.log.document().isEmpty():
            cur.insertBlock()
        lbl = QTextCharFormat(); lbl.setForeground(QColor(t.accent)); lbl.setFontWeight(QFont.Bold)
        cur.insertText("GINI: ", lbl)
        self._body_fmt = QTextCharFormat(); self._body_fmt.setForeground(QColor(t.text))
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()

    def _stream_insert(self, delta: str) -> None:
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(delta, self._body_fmt)
        self.log.setTextCursor(cur)
        self.log.ensureCursorVisible()

    def _on_answer(self, device: str, text: str) -> None:
        self._stop_spinner()
        if self._streaming:                      # text was already typed live — just persist
            final = self._stream_buf or text
            self._messages.append(("GINI", final, False, False))
            self.ctx.bus.assistant_message.emit("GINI", final)
            self._streaming = False
            self._raise_self()
            text = final
        elif text and text.strip():              # buffered reply -> render as Markdown
            self._post("GINI", text, markdown=True)
        if self._tutor and device:
            did = self._device_id(device)
            if did:                              # element-specific: short anchored callout
                self.ctx.bus.present_callout.emit(did, self._callout_line(text))
        if not self.wizard_mode and not self.coach_mode:   # Wizard/Coach manage their own chips
            self._refresh_followups()            # offer context-aware next questions

    def exit_explain_mode(self) -> None:
        if self.explain_mode:
            self.explain_mode = False
            self.ctx.bus.present_clear.emit()

    def _trace_and_show(self, a: str, b: str) -> str:
        try:
            path = self.api.trace_path(a, b)
        except Exception:
            return f"I couldn't find {a} or {b} on the canvas."
        if not path:
            return f"There's no path between {a} and {b} on the canvas."
        by_name = {d.name: d.id for d in self.ctx.topology.devices.values()}
        ids = [by_name[n] for n in path if n in by_name]
        if self._tutor:
            bus = self.ctx.bus
            bus.present_clear.emit()
            bus.present_highlight.emit(ids)
            bus.present_packet.emit(ids)          # animate a packet along the path (no text bubble)
        hops = len([n for n in path if n != a and n != b])
        return f"Path {a} → {b} ({hops} hop{'s' if hops != 1 else ''}):  " + " → ".join(path)

    def _explain_stage(self, narration: str) -> None:
        """Spotlight the hub node, anchor a callout, and narrate — the AI on stage."""
        if not self._tutor:
            return
        t = self.ctx.topology
        if not t.devices:
            return
        hub = max(t.devices.values(), key=lambda d: t.degree(d.id))
        bus = self.ctx.bus
        bus.present_spotlight.emit([hub.id])
        bus.present_callout.emit(hub.id, f"{hub.name} — most connected node")
        # the full overview goes to the right pane (no big canvas bubble)

    def _add(self, phrase: str) -> str:
        phrase = phrase.rstrip(".")
        match = None
        for d in all_devices():
            if d.label.lower() in phrase or d.key in phrase:
                match = d
                break
        if match is None:
            return f"I don't recognize a device called “{phrase}”. Try “list”."
        created = self.api.add_device(match.key)
        return f"Added {created['name']} ({match.label})."

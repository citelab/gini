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
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ..agent.api import GiniAPI
from ..app import AppContext
from ..domain import all_devices
from .theme import ThemeManager, icons


class Assistant(QWidget):
    # emitted from the LLM worker thread; delivered on the UI thread (queued)
    answer_ready = Signal(str, str)         # (device_name_or_empty, text)
    answer_chunk = Signal(str)              # a streamed token delta (live "typing")
    status_changed = Signal(str, bool)      # (mode label, busy) -> toolbar indicator

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
        self._busy = False    # an LLM answer is in flight
        self._messages: list[tuple[str, str]] = []   # (role, text) — for theme re-render
        self._streaming = False           # a streamed answer is being typed into the log
        self._stream_buf = ""             # accumulated streamed text (for persistence)
        self._last_ref: tuple[str, str] | None = None   # what we last explained (for chips)
        self.answer_ready.connect(self._on_answer)
        self.answer_chunk.connect(self._on_chunk)
        theme.themeChanged.connect(lambda *_: self._rerender())   # recolor on theme switch

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(False)
        lay.addWidget(self.log, 1)

        # mode buttons — checkable toggles with a clear pressed/glow state
        chips = QHBoxLayout(); chips.setSpacing(6)
        self._explain_btn = QPushButton("Explain")
        self._explain_btn.setObjectName("ModeBtn")
        self._explain_btn.setCheckable(True)        # a visible, sticky mode (not one-shot)
        self._explain_btn.setToolTip("Explain mode: click any device on the canvas to explain it")
        self._explain_btn.toggled.connect(self._toggle_explain)
        chips.addWidget(self._explain_btn)

        self._wizard_btn = QPushButton("Wizard")
        self._wizard_btn.setObjectName("WizardBtn")
        self._wizard_btn.setCheckable(True)
        self._wizard_btn.setToolTip("Wizard mode: describe the system you want and GINI "
                                    "suggests working blueprints to lay out")
        self._wizard_btn.toggled.connect(self._toggle_wizard)
        chips.addWidget(self._wizard_btn)

        for label, cmd in (("Show a path", "__path__"), ("Status", "status")):
            b = QPushButton(label)
            b.setObjectName("ModeBtn")
            b.clicked.connect(lambda _=False, c=cmd: self._chip(c))
            chips.addWidget(b)
        chips.addStretch(1)
        self._tutor_box = QPushButton("Tutor")
        self._tutor_box.setObjectName("ModeBtn")
        self._tutor_box.setCheckable(True)
        self._tutor_box.setChecked(True)
        self._tutor_box.setToolTip("Tutor mode: explanations highlight and animate on the canvas")
        self._tutor_box.toggled.connect(lambda v: setattr(self, "_tutor", v))
        chips.addWidget(self._tutor_box)
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

    # LLM seam -------------------------------------------------------------- #
    def set_llm(self, fn: Callable[[str], str]) -> None:
        self._llm = fn

    def set_loop(self, loop) -> None:
        """Attach an AgentLoop (Ollama-backed) for open-ended questions."""
        self._loop = loop

    # chat ------------------------------------------------------------------ #
    def _msg_html(self, role: str, text: str) -> str:
        t = self.theme.theme
        color = t.accent if role == "GINI" else t.muted
        return (f'<p style="margin:6px 0;"><b style="color:{color};">{role}:</b> '
                f'<span style="color:{t.text};">{text}</span></p>')

    def _post(self, role: str, text: str) -> None:
        self._messages.append((role, text))
        self.log.append(self._msg_html(role, text))
        self.ctx.bus.assistant_message.emit(role, text)
        if role == "GINI":
            self._raise_self()          # surface the Ask GINI tab so replies aren't missed

    def _rerender(self) -> None:
        """Re-paint the whole conversation in the current theme's colours (message
        colours are baked into the HTML at post time, so a theme switch must redraw)."""
        self.log.clear()
        for role, text in self._messages:
            self.log.append(self._msg_html(role, text))

    def _raise_self(self) -> None:
        from PySide6.QtWidgets import QDockWidget
        w = self.parent()
        while w is not None and not isinstance(w, QDockWidget):
            w = w.parent()
        if w is not None:
            w.raise_()

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._post("You", text)
        if self.wizard_mode:                    # the box is a wish list for the Wizard
            self._run_wizard(text)
            return
        try:
            reply = self._handle(text)
        except Exception as e:  # surface errors to the student rather than crashing
            reply = f"Sorry, I couldn't do that: {e}"
        if reply is not None:   # None => an LLM answer is coming asynchronously
            self._post("GINI", reply)
            self._refresh_followups()   # chips appear iff this was an explain (_last_ref set)

    # deterministic intent handling (LLM-free) ------------------------------ #
    def _handle(self, text: str) -> str:
        low = text.lower().strip()
        self._last_ref = None      # cleared by default; explain paths below set it

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
            return self._show_device(m.group(1).strip().upper())

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
            self._ask_async(text, "")          # free-form question -> the shared LLM loop
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
        self._clear_followups()
        chips = self._followups_for(self._last_ref)
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

    def _run_followup(self, text: str) -> None:
        self._clear_followups()
        if text == "Show a path":
            self._chip("__path__")
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

    def _toggle_explain(self, on: bool) -> None:
        """Explain mode is a visible, sticky toggle — it won't drop when you click the
        canvas, so the spotlight reliably follows whatever device you select."""
        self.explain_mode = on
        if on and self._wizard_btn.isChecked():    # modes are mutually exclusive
            self._wizard_btn.setChecked(False)
        self.input.setPlaceholderText(
            "Explain mode — click any device on the canvas…" if on
            else "Ask GINI to build, inspect, or explain…")
        self._emit_status()
        if on:
            self._run_overview()
        else:
            self.ctx.bus.present_clear.emit()      # exit: clear the stage

    def _toggle_wizard(self, on: bool) -> None:
        """Wizard mode: the input box becomes a wish list. Describe the system you want
        ('something I can visualize under load') and GINI matches curated, working
        blueprints and offers to lay them out. The LLM only selects+explains; the build
        is deterministic, so it can't produce a broken topology."""
        self.wizard_mode = on
        if on and self._explain_btn.isChecked():
            self._explain_btn.setChecked(False)
        self.input.setPlaceholderText(
            "Wizard — describe the system you want to build…" if on
            else "Ask GINI to build, inspect, or explain…")
        self._emit_status()
        if on:
            self._clear_followups()
            rs = self.api.list_recipes()
            names = ", ".join(r["name"] for r in rs)
            self._post("GINI", "Wizard mode on. Tell me what you want to build or explore "
                               "— e.g. “something I can watch under load”, “a streaming "
                               f"pipeline”, “a web app with a database”. I know: {names}.")

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
        mode = ("Wizard mode" if self.wizard_mode
                else "Explain mode" if self.explain_mode else "Q&A mode")
        self.status_changed.emit(mode, self._busy)

    # --- Wizard mode: match the student's wish to curated, working blueprints --- #
    def _run_wizard(self, wish: str) -> None:
        ranked = (self.api.suggest_recipes(wish) or self.api.list_recipes())[:3]
        if self._loop is not None:
            catalog = "; ".join(f"{r['name']} — {r['summary']}" for r in ranked)
            self._ask_async(            # clears followups, starts the spinner
                f"The student wants: \"{wish}\". From ONLY these GINI blueprints, recommend "
                f"the best fit and say in 2-3 sentences what it builds and why it matches "
                f"(do not invent components): {catalog}", "")
        else:
            lines = "<br>".join(f"• <b>{r['name']}</b> — {r['summary']}" for r in ranked)
            self._post("GINI", "Blueprints that fit — pick one below to lay it out:<br>"
                       + lines)
        # deterministic, always-correct chips to lay each blueprint out (set AFTER
        # _ask_async, which clears followups; kept through the answer by the wizard guard)
        self._wizard_chips(ranked)

    def _wizard_chips(self, recipes: list[dict]) -> None:
        self._clear_followups()
        if not recipes:
            return
        for r in recipes:
            b = QPushButton(f"Lay out: {r['name']}")
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, rid=r["id"]: self._apply_recipe(rid))
            self._follow_lay.addWidget(b)
        self._follow_lay.addStretch(1)
        self._follow_box.setVisible(True)

    def _apply_recipe(self, recipe_id: str) -> None:
        self._clear_followups()
        try:
            res = self.api.apply_recipe(recipe_id)
        except KeyError:
            self._post("GINI", "I couldn't find that blueprint.")
            return
        self.ctx.bus.topology_changed.emit()
        self._post("GINI", f"Laid out the <b>{res['name']}</b> blueprint — "
                   f"{len(res['added'])} elements, {res['links']} links. Press Run to "
                   f"start it, then open the consoles from the run log.")

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

    # --- async LLM plumbing: one shared conversation, off the UI thread ----- #
    def _ask_async(self, prompt: str, device: str) -> None:
        import threading
        # one place for "waiting" feedback: the spinner in the pane (no canvas popup).
        self._start_spinner("GINI is thinking" + (f" about {device}" if device else ""))
        self._streaming = False
        self._stream_buf = ""
        self._clear_followups()                  # hide stale suggestions while answering

        def work():
            try:
                # stream tokens straight into the pane for a live "typing" feel; loops
                # that don't support on_text just return the whole answer at the end.
                text = self._loop.send(prompt, on_text=lambda d: self.answer_chunk.emit(d))
            except TypeError:
                text = self._loop.send(prompt)   # older loop signature (no streaming)
            except Exception as e:
                text = f"(LLM error: {e})"
            self.answer_ready.emit(device or "", text or "(no answer)")
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
            self._messages.append(("GINI", final))
            self.ctx.bus.assistant_message.emit("GINI", final)
            self._streaming = False
            self._raise_self()
            text = final
        elif text and text.strip():              # no tokens streamed (error / non-stream loop)
            self._post("GINI", text)
        if self._tutor and device:
            did = self._device_id(device)
            if did:                              # element-specific: short anchored callout
                self.ctx.bus.present_callout.emit(did, self._callout_line(text))
        if not self.wizard_mode:                 # keep the Wizard's "Lay out" chips
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

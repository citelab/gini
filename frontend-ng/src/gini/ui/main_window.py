"""The gBuilder main window — shell that assembles every panel.

Layout: device palette (left dock) · canvas (center) · inspector + assistant
(right docks, tabbed) · console (bottom dock) · toolbar + status bar. All visuals
flow from the ThemeManager so Dark / Light / GINI Brand swap instantly.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QDockWidget, QFrame, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QToolBar,
    QToolButton, QWidget,
)

from ..agent.api import GiniAPI
from ..app import AppContext
from .assistant import Assistant
from .canvas import NODE_H, NODE_W, CanvasView
from .inspector import Inspector
from .palette import Palette
from .theme import ThemeManager, icons


def _theme_swatch(theme, size: int = 18):
    """A little palette chip for a theme menu row: the theme's surface with its
    accent + success dots, so each theme is recognisable at a glance."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
    s = size * 2
    pm = QPixmap(s, s); pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor(theme.line), 1.5)); p.setBrush(QColor(theme.bg))
    p.drawRoundedRect(QRectF(1, 1, s - 2, s - 2), s * 0.30, s * 0.30)
    p.setPen(Qt.NoPen)
    d = s * 0.42
    p.setBrush(QColor(theme.accent)); p.drawEllipse(QRectF(s*0.30 - d/2, s*0.42 - d/2, d, d))
    ds = s * 0.34
    p.setBrush(QColor(theme.success)); p.drawEllipse(QRectF(s*0.66 - ds/2, s*0.60 - ds/2, ds, ds))
    p.end()
    return QIcon(pm)


def _theme_dots_icon(theme, size: int = 19):
    """Three coloured dots for the toolbar button, signalling 'this changes colours'."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    s = size * 2
    pm = QPixmap(s, s); pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing, True); p.setPen(Qt.NoPen)
    d = s * 0.44
    for col, x, y in ((theme.accent, 0.28, 0.40), (theme.warning, 0.72, 0.40),
                      (theme.success, 0.50, 0.64)):
        p.setBrush(QColor(col)); p.drawEllipse(QRectF(s*x - d/2, s*y - d/2, d, d))
    p.end()
    return QIcon(pm)

# Runs INSIDE the faas container (python -c). Reads GINI_FN/METHOD/BODY from the env,
# checks whether this is the function's first call (cold), invokes it on localhost:8000,
# and prints one JSON line {code, ms, cold, body} for the inspector to display.
_FAAS_INVOKE = (
    "import os,time,json,urllib.request,urllib.error\n"
    "fn=os.environ['GINI_FN'];method=os.environ.get('GINI_METHOD','GET')\n"
    "body=os.environ.get('GINI_BODY','');base='http://localhost:8000'\n"
    "try:\n"
    "    m=json.load(urllib.request.urlopen(base+'/_gini/metrics',timeout=5))\n"
    "    prev=m.get('functions',{}).get(fn,{}).get('invocations',0)\n"
    "except Exception:\n"
    "    prev=0\n"
    "data=body.encode() if body else None\n"   # send the body whenever one is typed
    "req=urllib.request.Request(base+'/'+fn,data=data,method=method)\n"
    "t=time.time()\n"
    "try:\n"
    "    r=urllib.request.urlopen(req,timeout=15);code=r.getcode();out=r.read().decode('utf-8','replace')\n"
    "except urllib.error.HTTPError as e:\n"
    "    code=e.code;out=e.read().decode('utf-8','replace')\n"
    "except Exception as e:\n"
    "    code=0;out=str(e)\n"
    "print(json.dumps({'code':code,'ms':round((time.time()-t)*1000),'cold':prev==0,'body':out}))\n"
)


def _photo_data_url(path: str, size: int = 128) -> str:
    """Load an image, centre-crop to a square, downscale to a face-sized thumbnail, and encode it as
    a PNG data-URL. QImage (not QPixmap) so it's CPU-only — safe off the GUI thread and headless.
    Downscaling on the client keeps the DB tiny and means the original never leaves the machine.
    Returns '' if the file isn't a readable image."""
    import base64

    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return ""
    side = min(img.width(), img.height())
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    img = img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ba = QByteArray()                    # MUST outlive the buffer — a temporary here crashes PySide
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return "data:image/png;base64," + base64.b64encode(bytes(ba)).decode()


class MainWindow(QMainWindow):
    def __init__(self, app) -> None:
        super().__init__()
        self.ctx = AppContext()
        self.api = GiniAPI(self.ctx)
        self._load_config()          # ~/.gini/config.json defaults
        self.ctx.topology.prefix_overrides = dict(self.ctx.settings.name_prefixes)
        self._apply_env_settings()   # env vars override saved config
        from ..agent.tools import build_registry
        self.registry = build_registry(self.api)   # shared: in-app loop + present tools
        # An LLM turn runs on a worker thread, and its tools mutate topology.devices /
        # topology.links -- dicts the GUI thread iterates on every canvas paint. Route every
        # handler through the GUI thread so a build cannot land mid-iteration.
        from .gui_dispatch import GuiDispatcher
        self._gui_dispatch = GuiDispatcher(self)
        self.registry.dispatch = self._gui_dispatch
        # gLoader: compiles the drawn topology into a runtime plan and launches it
        from pathlib import Path
        from .. import runtime as _rt
        from ..services import GLoader
        self._gloader = GLoader(Path(_rt.__file__).parent)
        self.ctx.orchestrator = self._gloader.orchestrator   # behavioral probes exec through this
        # NOTE: we deliberately do NOT sign in to the course at startup, even with saved credentials.
        # Signing in is an act, not a side effect of launching an app: you might be demoing, or on a
        # shared machine, or simply not want your instructor to see you're online. The User pill
        # starts signed-out and you sign in from it.
        self._remote = None              # RemoteClient when connected to a GINI server, else None
        self._running = False
        self._launching = False        # `up` is in flight: containers may already be appearing
        self._orphaned = False         # a launch failed and may have left containers behind
        self._stopping = False
        self._workdir: str | None = None
        self._project_path: str | None = None
        self._project_dir: str | None = None       # active project folder (Projects)
        self._experiment: str | None = None        # the experiment (topology) open within it
        self._router_programs: dict = {}        # device id -> RouterProgram (Router Lab)
        # Proof of activity: records the student's work into a hash chain once they enter their
        # assignment code. Inert until armed, and every handler is guarded, so it can never take a
        # session down. Built before the docks, because the dashboard strip displays its state.
        from ..services.proof_recorder import ProofRecorder
        self.proof_recorder = ProofRecorder(self.ctx)
        self.proof_recorder.attach()
        self.ctx.proof_recorder = self.proof_recorder   # so the assistant can reach it (hunk 5)
        self.ctx.bus.run_state.connect(self._on_run_state)
        self.ctx.bus.runtime_status.connect(self._on_runtime_status)
        self.ctx.bus.board_status_ready.connect(self._on_board_status)
        self.ctx.bus.device_activated.connect(self._on_device_activated)
        from PySide6.QtCore import QTimer
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._poll_status)
        self._fabric_poll = QTimer(self)        # cloud-fabric app metrics
        self._fabric_poll.setInterval(2000)
        self._fabric_poll.timeout.connect(self._poll_fabric)
        self.ctx.bus.fabric_metrics.connect(self._on_fabric_metrics)
        from ..domain.memwatch import MemWatch
        self._memwatch = MemWatch()             # fleet memory gauge + runaway detection
        self._mem_warned: set = set()           # services already flagged this run
        self._mem_pressure_warned = False
        self._mem_poll = QTimer(self)
        self._mem_poll.setInterval(5000)        # docker stats is ~1-2s of work; keep it light
        self._mem_poll.timeout.connect(self._poll_mem)
        self.ctx.bus.mem_metrics.connect(self._on_mem_metrics)
        self._k8s_poll = QTimer(self)           # kubernetes metrics (kubectl)
        self._k8s_poll.setInterval(3000)
        self._k8s_poll.timeout.connect(self._poll_k8s)
        self.ctx.bus.k8s_metrics.connect(self._on_k8s_metrics)
        self.ctx.bus.llm_reachable.connect(self._on_llm_reachable)
        self.ctx.bus.enrolment_changed.connect(self._on_enrolment)
        self._chat_dock = None
        self._force_new_signin = False             # one-shot: force the sign-in dialog (switch user)
        self._beat = QTimer(self)                  # presence + group progress, while signed in
        self._beat.setInterval(30_000)
        self._beat.timeout.connect(self._heartbeat)
        self._beat.start()
        from .theme.manager import scale_for
        self.theme = ThemeManager(app, self.ctx.settings.theme,
                                  scale_for(getattr(self.ctx.settings, "text_size", "Normal")))
        self.theme.apply()

        self.setWindowTitle("gBuilder 6.0 — networks + cloud")
        from .branding import app_icon
        self.setWindowIcon(app_icon())              # window + taskbar/dock icon (the GINI mascot)
        # open to a sensible size, but never larger than the screen (a wide dock must never push
        # the window off-screen)
        screen = app.primaryScreen().availableGeometry() if app.primaryScreen() else None
        w = min(1280, screen.width() - 40) if screen else 1280
        h = min(820, screen.height() - 80) if screen else 820
        self.resize(w, h)

        self.canvas = CanvasView(self.ctx, self.theme.theme)
        self.setCentralWidget(self.canvas)
        self.canvas.scene_.selectionChanged.connect(self._on_scene_selection)

        self._make_toolbar()
        self._make_delete_shortcut()
        self._make_menubar()
        self._make_docks()
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app is not None:
            # Backstop so ⌘Q / app-menu Quit is blocked while running. A CALLBACK on the
            # application, never an application-wide event filter: the filter form tapped every
            # event for every QObject and segfaulted the moment the mouse crossed the OS Zoo /
            # Desktop screen's embedded QWebEngineView. See ui/app.py for the crash report.
            _app.quit_guard = self._quit_blocked
        self._make_statusbar()
        self._wire_llm()

        self.theme.themeChanged.connect(self._on_theme_changed)
        self.ctx.bus.topology_changed.connect(self._update_status)
        # Debounce the HEAVY per-change recompute: addressing, lint, and billing each run the
        # compiler, and topology_changed fires once PER device — so loading a project, the
        # agent building a recipe, or a multi-delete would trigger a burst of synchronous
        # compiles on the GUI thread (a momentary freeze). Coalesce them into ONE recompute a
        # short idle after the last change so the UI stays responsive.
        self._recompute_timer = QTimer(self)
        self._recompute_timer.setSingleShot(True)
        self._recompute_timer.setInterval(120)
        self._recompute_timer.timeout.connect(self._do_recompute)
        self.ctx.bus.topology_changed.connect(self._recompute_timer.start)
        self.ctx.bus.device_resized.connect(self._on_device_resized)
        self.ctx.bus.device_changed.connect(self._on_device_changed_live)
        self.ctx.bus.log.connect(self._on_log)
        self.ctx.bus.device_delete_requested.connect(self._delete_device)
        self.ctx.bus.warning_explain_requested.connect(self._on_warning_explain)
        self.ctx.bus.device_logs_requested.connect(self._open_logs)
        self.ctx.bus.device_console_requested.connect(self._open_console)
        self.ctx.bus.function_invoke_requested.connect(self._on_function_invoke)
        self.ctx.bus.function_deploy_requested.connect(self._on_function_deploy)
        self.ctx.bus.rider_ran.connect(self._on_rider_state)
        # xv6 riders run over the console (not docker); register the serial-path hooks
        self._xv6_rider_sessions: dict = {}
        self.ctx.xv6_rider_toggle = self._toggle_xv6_rider
        self.ctx.xv6_rider_running = lambda rid: rid in self._xv6_rider_sessions
        self.ctx.bus.selection_changed.connect(self._on_selection_explain)
        self.ctx.bus.selection_changed.connect(self._on_selection_source)
        self.ctx.bus.canvas_background_clicked.connect(self._on_canvas_background)
        self.palette.element_selected.connect(self._on_palette_explain)
        self.assistant.status_changed.connect(self.mode_indicator.set_status)
        self.mode_indicator.set_status("Chat mode", False)   # initial
        self.mode_indicator.model_clicked.connect(self._open_settings)   # Model pill -> Settings
        self.mode_indicator.user_clicked.connect(self._user_menu)   # User pill -> the user menu
        # Ask GINI messages live in the right-hand pane only; the Console is for
        # build/run logs, so we deliberately do NOT mirror chat into it.
        self._update_status()
        # NOTE: reopening last session's project is done by the app entry point
        # (restore_last_project), NOT here — so constructing a window in tests is inert.

    def _load_config(self) -> None:
        """Load persisted defaults from ~/.gini/config.json into Settings (applied as the
        ThemeManager and LLM read Settings during construction)."""
        from ..app.paths import PERSISTED_KEYS, load_config
        cfg = load_config()
        s = self.ctx.settings
        for k in PERSISTED_KEYS:
            if k in cfg:
                setattr(s, k, cfg[k])

    def _open_settings(self) -> None:
        from ..app.paths import PERSISTED_KEYS, save_config
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self, self.ctx.settings)
        if not dlg.exec():
            return
        v = dlg.values()
        s = self.ctx.settings
        # Apply EVERY value the dialog returned that is a real Settings field. This used to be a
        # hand-maintained whitelist, which meant adding a control to the dialog silently did
        # nothing until you remembered to add its key here too — the edit was read, then dropped,
        # and Settings saved the old value back. `hasattr` still guards against a typo'd key.
        for k, val in v.items():
            if hasattr(s, k):
                setattr(s, k, val)
        self.ctx.topology.prefix_overrides = dict(s.name_prefixes)   # apply to current topo
        from .theme.manager import scale_for
        self.theme.set_font_scale(scale_for(v.get("text_size", "Normal")))   # live text-size switch
        self.theme.set_theme(v["theme"])               # live theme switch
        self._wire_llm()                               # re-create / clear the LLM loop
        if getattr(self.ctx, "teaching_center", None) is not None:
            self._connect_teaching_center()            # already signed in → pick up the new details
        # …but saving Settings does NOT sign you in. Signing in stays an explicit act (the User pill).
        self._rebill()                                 # prices may have changed
        save_config({k: getattr(s, k) for k in PERSISTED_KEYS})
        self.ctx.log("Settings saved to ~/.gini/config.json.", "ok")

    def _connect_teaching_center(self) -> None:
        """Enrol in the course from Settings, and refresh the toolbar's User pill.

        Everything here is NETWORK — `online()`, the manifest, the profile — so it runs OFF the GUI
        thread. It used to block startup and every Settings save for as long as the course server
        took to answer (or to time out, which is worse). The pill is updated via a bus signal, the
        same way the LLM health probe reports back."""
        tc = self.ctx.connect_teaching_center()
        if tc is None or not tc.signed_in():           # not signed in — a perfectly normal state
            self.ctx.bus.enrolment_changed.emit("", False, 0)
            return
        import threading
        student = self.ctx.settings.tc_student

        def probe():
            try:
                online = tc.online()
                if not online and tc.session_expired():
                    # the server ANSWERED and rejected us. Say "sign in again", not "you're offline"
                    self.ctx.log("Teaching Center: your session expired — sign in again from the "
                                 "user menu.", "info")
                    self.ctx.bus.enrolment_changed.emit("", False, 0)
                    return
            except Exception:                          # noqa: BLE001
                online = False
            # OTA: pull any teacher-authored fragments into the local content layer, so assigned
            # experiments that reference them actually load. Version-gated on this end; a pack a newer
            # engine authored is skipped-with-reason, never bricks the client.
            if online:
                try:
                    res = tc.pull_content()
                    if res["installed"] or res["skipped"] or res.get("removed"):
                        note = f"Content synced: {len(res['installed'])} fragment(s)"
                        if res.get("removed"):
                            note += f", {len(res['removed'])} removed"
                        if res["skipped"]:
                            note += f", {len(res['skipped'])} skipped (needs a newer gBuilder)"
                        self.ctx.log(note, "info")
                except Exception:                      # noqa: BLE001 — a content pull never blocks sign-in
                    pass
            # a TEACHER has no "assignments due" — that's a student notion. Emit -1 as the sentinel;
            # the pill renders it as the teacher role, not a count.
            if tc.is_teacher():
                self.ctx.bus.enrolment_changed.emit(student, online, -1)
                return
            due = 0
            try:
                lessons = tc.available_lessons()       # cached when offline — homework doesn't vanish
                prof = tc.checkout_profile()
                done = {lid for lid, rec in (prof.lessons or {}).items() if rec.completed}
                due = sum(1 for m in lessons if m.get("id") not in done)
                if online:
                    tc.flush()                         # push anything queued while offline
            except Exception:                          # noqa: BLE001
                pass
            self.ctx.bus.enrolment_changed.emit(student, online, due)
        threading.Thread(target=probe, daemon=True).start()

    def _on_enrolment(self, student: str, online: bool, due: int) -> None:
        self.mode_indicator.set_enrolment(student, online, due)
        if not student:
            return
        if due < 0:                                    # teacher
            self.ctx.log(f"Teaching Center: signed in as {student} (teacher) to "
                         f"{self.ctx.settings.tc_course}.", "ok")
        elif online:
            self.ctx.log(f"Teaching Center: signed in as {student} to "
                         f"{self.ctx.settings.tc_course} — {due} mission"
                         f"{'s' if due != 1 else ''} due.", "ok")
        else:
            self.ctx.log("Teaching Center: offline — using the cached course; "
                         "results will sync when it's reachable.", "info")

    # -- the User menu ------------------------------------------------------- #
    def _sign_in(self) -> None:
        """Sign in to the course — the explicit act.

        A cached session means we can go straight through. Otherwise ask for a password (and, the
        first time, the enrolment token that proves the account is yours to claim). The password is
        exchanged for a session and never stored."""
        from PySide6.QtWidgets import QMessageBox
        from .signin_dialog import SignInDialog
        from ..agent.teaching_center import InsecureTransport

        force = self._force_new_signin           # one-shot: force the dialog (sign in as someone else)
        self._force_new_signin = False
        s = self.ctx.settings
        if not (s.tc_url and s.tc_course):
            self.ctx.log("Teaching Center: set the course server and course in Settings first.",
                         "info")
            self._open_settings()
            return
        if not s.tc_student and not force:
            self._open_settings()
            return

        tc = self.ctx.connect_teaching_center()
        if tc is None:
            return
        if not self._force_new_signin and tc.signed_in():   # a live session — nothing to ask
            self.ctx.log(f"Teaching Center: resuming your session as {s.tc_student}…", "info")
            self._connect_teaching_center()
            return

        dlg = SignInDialog(self, s, first_time=bool(s.tc_token))
        if not dlg.exec():
            self.ctx.teaching_center = None
            return
        v = dlg.values()
        s.tc_student = v["student"]
        tc = self.ctx.connect_teaching_center()   # rebuild with the (possibly edited) student id
        if tc is None:
            return
        try:
            res = (tc.claim(v["password"], v["enrolment_token"]) if v["claim"]
                   else tc.login(v["password"]))
        except InsecureTransport as e:
            self.ctx.teaching_center = None
            QMessageBox.warning(self, "Unencrypted connection", str(e))
            return
        if not res.get("ok"):
            self.ctx.teaching_center = None
            QMessageBox.warning(self, "Sign-in failed",
                                res.get("error") or "The course server rejected the sign-in.")
            self.ctx.log(f"Teaching Center: sign-in failed — {res.get('error', '')}", "error")
            return
        if v["claim"]:
            s.tc_token = ""                       # the enrolment token is spent; don't keep it around
            self._persist_settings()
        self.ctx.log(f"Teaching Center: signed in as {s.tc_student}.", "ok")
        self._connect_teaching_center()           # pull the manifest / profile, update the pill

    def _sign_in_as(self) -> None:
        """Sign in as a DIFFERENT user without editing Settings first — type a username here.

        Used to switch between accounts on one machine (e.g. a student account and the teacher
        account). Forces the sign-in dialog (bypassing any cached session) so you always get to enter
        the username + password of whoever you want to become."""
        from PySide6.QtWidgets import QInputDialog
        who, ok = QInputDialog.getText(self, "Sign in as another user",
                                       "Username:", text="")
        if not ok or not who.strip():
            return
        # switching identity: drop the current session and start fresh as the typed username
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is not None:
            import threading
            threading.Thread(target=tc.logout, daemon=True).start()
        self.ctx.teaching_center = None
        self.ctx.settings.tc_student = who.strip()
        self.ctx.settings.tc_token = ""          # a different account's enrolment token isn't this one's
        self._force_new_signin = True
        self._sign_in()

    def _sign_out(self) -> None:
        """Go local: drop the session (server-side too, so a shared machine doesn't stay signed in).
        Your student id stays in Settings; anything unsent stays queued for the next sign-in."""
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is not None:
            import threading
            threading.Thread(target=tc.logout, daemon=True).start()   # network — never on the GUI thread
        self.ctx.teaching_center = None
        self.ctx.bus.enrolment_changed.emit("", False, 0)
        self.ctx.log("Teaching Center: signed out — Missions now offers the practice catalog.",
                     "info")

    def _user_menu(self) -> None:
        """The User pill's menu: who you are, what you owe, what you've done."""
        from PySide6.QtWidgets import QMenu
        s = self.ctx.settings
        tc = getattr(self.ctx, "teaching_center", None)
        m = QMenu(self)

        head = m.addAction(f"Signed in as {s.tc_student} · {s.tc_course}" if tc
                           else "Not signed in")
        head.setEnabled(False)
        m.addSeparator()

        if tc is None:
            self._add_signin_items(m)
        else:
            # Everything gated here is PARKED, not gone — see app/features.py, which records what
            # each one needs from the Teaching Center before it can come back. Offering a menu item
            # that silently does nothing is worse than not offering it.
            from ..app import features
            teacher = tc.is_teacher()
            if not teacher and features.on("missions.server"):   # 'Due / Completed' is a student view
                self._add_mission_items(m, tc)
                m.addSeparator()
            self._add_teacher_items(m, tc)
            if features.on("messaging"):
                m.addAction("Messages…").triggered.connect(self._open_messages)
            if features.on("user.photo"):
                m.addAction("Set my photo…").triggered.connect(self._set_photo)
            if not teacher:                            # groups + AI-proxy are student notions
                if features.on("groups"):
                    self._add_group_items(m, tc)
                if features.on("ai.proxy"):
                    m.addAction("AI may answer for me…").triggered.connect(self._ai_proxy_consent)
            m.addSeparator()
            m.addAction("Sync now").triggered.connect(self._connect_teaching_center)
            m.addAction("Sign in as another user…").triggered.connect(self._sign_in_as)
            m.addAction("Sign out").triggered.connect(self._sign_out)

        m.addSeparator()
        m.addAction("Settings…").triggered.connect(self._open_settings)
        m.exec(self.mode_indicator.mapToGlobal(
            self.mode_indicator.rect().bottomRight()))

    def _add_mission_items(self, menu, tc) -> None:
        """Due missions (click to play) and what you've already finished, with the band you earned.
        Read from the CACHED manifest + local profile, so the menu opens instantly and works offline
        — it never blocks on the course server."""
        try:
            lessons = tc.available_lessons()
            prof = tc.checkout_profile()
            recs = prof.lessons or {}
        except Exception:                                    # noqa: BLE001
            menu.addAction("(couldn't read your course — try Sync now)").setEnabled(False)
            return

        done = {lid for lid, r in recs.items() if r.completed}
        due = [x for x in lessons if x.get("id") not in done]

        cap = menu.addAction(f"Due — {len(due)}" if due else "Nothing due — you're clear")
        cap.setEnabled(False)
        for x in due:
            a = menu.addAction("   ▸  " + (x.get("title") or x["id"]))
            a.triggered.connect(lambda _=False, lid=x["id"]: self._play_assigned(lid))

        if done:
            menu.addSeparator()
            cap = menu.addAction(f"Completed — {len(done)}")
            cap.setEnabled(False)
            titles = {x["id"]: (x.get("title") or x["id"]) for x in lessons}
            for lid in sorted(done):
                band = (recs[lid].best_band or "").upper()
                a = menu.addAction(f"   ✓  {titles.get(lid, lid)}   ·   {band}")
                a.setEnabled(False)                          # history, not a launcher

    def _play_assigned(self, lesson_id: str) -> None:
        if self.assistant.enter_missions():
            self.assistant._start_assigned_mission(lesson_id)

    # -- groups, messages, AI consent (Phases B–E) ---------------------------- #
    def _add_group_items(self, menu, tc) -> None:
        """Your team, and where they are on the mission. A class with no groups simply has no group
        section — absence, not an error."""
        try:
            g = tc.my_group()
        except Exception:                                    # noqa: BLE001
            return
        if not g.get("group"):
            return
        cap = menu.addAction(f"Group {g['group']}")
        cap.setEnabled(False)
        gid = f"group:{g['group']}"
        menu.addAction("   Open group chat").triggered.connect(
            lambda _=False, c=gid: self.assistant.open_conversation(c))
        for m in g.get("members", []):
            pr = m.get("progress") or {}
            where = (f"Level {pr['level']}" if pr.get("level") else "—")
            dot = "●" if m.get("online") else "○"
            label = f"   {dot}  {m['name']}" + ("  (you)" if m.get("me") else f"   ·   {where}")
            act = menu.addAction(label)
            if m.get("me"):
                act.setEnabled(False)
            else:                              # click a teammate → open the DM
                peer = m["id"]
                act.triggered.connect(
                    lambda _=False, p=peer: self.assistant.open_conversation(
                        "dm:" + "|".join(sorted((self.ctx.settings.tc_student, p)))))

    def _open_messages(self) -> None:
        """Messages live IN the Ask GINI panel now — one conversation surface, GINI and people
        together. Jump to the instructor thread."""
        student = self.ctx.settings.tc_student
        self.assistant.open_conversation(f"teacher:{student}")

    def _set_photo(self) -> None:
        """Pick an image → downscale to a small square → upload as a data-URL. Downscaling on the
        client keeps the DB tiny (a phone photo is megabytes; the roster needs a thumbnail), and it
        means we never ship the original off the machine."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a profile photo", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if not path:
            return
        data_url = _photo_data_url(path)
        if not data_url:
            QMessageBox.warning(self, "Couldn't read that", "That file isn't an image I can read.")
            return

        import threading
        def work():
            res = tc.set_photo(data_url)
            self.ctx.log("Photo updated — your instructor will see it." if res.get("ok")
                         else f"Couldn't set photo: {res.get('error', 'unknown error')}",
                         "ok" if res.get("ok") else "error")
        threading.Thread(target=work, daemon=True).start()

    def _add_signin_items(self, menu) -> None:
        """The signed-out user menu: sign in as the saved user, or type a different username."""
        s = self.ctx.settings
        if s.tc_student:
            menu.addAction(f"Sign in as {s.tc_student}…").triggered.connect(self._sign_in)
        menu.addAction("Sign in as another user…").triggered.connect(self._sign_in_as)

    def _add_teacher_items(self, menu, tc) -> None:
        """TEACHER MODE — author fragments. Unlocked only when signed in as a teacher; students never
        see it. The TC session's role is the single source of truth."""
        if not tc.is_teacher():
            return
        from ..app import features
        cap = menu.addAction("Teacher tools")
        cap.setEnabled(False)
        # The Fragment Manager itself is local and works; only its Library tab talked to a server.
        menu.addAction("Fragment Manager…").triggered.connect(self._fragment_manager)
        if features.on("missions.server"):        # playtesting a DRAFT the course handed out
            menu.addAction("Playtest an experiment…").triggered.connect(self._playtest_experiment)
        menu.addSeparator()

    def _playtest_experiment(self) -> None:
        """Teacher: play a DRAFT experiment before approving it — the approval gate's playtest half.
        Lists all experiments (draft + released); playing one drops into it as a mission so the
        teacher proves the whole composition is winnable against the real engine."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is None or not tc.is_teacher():
            return
        lessons = tc.list_lessons()
        if not lessons:
            QMessageBox.information(self, "Playtest", "No experiments yet — compose one in the "
                                                     "Teaching Center console first.")
            return
        labels = [f"{le.get('title', le['id'])}  ·  {le.get('status', 'released')}" for le in lessons]
        pick, ok = QInputDialog.getItem(self, "Playtest an experiment",
                                        "Draft experiments are only visible to you until approved:",
                                        labels, 0, False)
        if not ok:
            return
        lid = lessons[labels.index(pick)]["id"]
        if self.assistant.enter_missions():
            self.assistant._start_assigned_mission(lid)

    def _fragment_manager(self) -> None:
        """Open the Fragment Manager (teacher mode): list / create / edit / delete fragments, with a
        recording editor that reads objectives off the live canvas.

        Shown NON-MODALLY (show(), not exec()) — recording needs the canvas to stay interactive, and a
        modal dialog would swallow every drag-and-drop into the board. Kept on `self` so it isn't
        garbage-collected while it floats."""
        from .fragment_manager import FragmentManager
        existing = getattr(self, "_frag_mgr", None)
        if existing is not None:
            existing.raise_(); existing.activateWindow()
            return
        self._frag_mgr = FragmentManager(self, self.ctx, author=self.ctx.settings.tc_student)
        self._frag_mgr.destroyed.connect(lambda *_: setattr(self, "_frag_mgr", None))
        self._frag_mgr.show()
        self._frag_mgr.raise_()

    def _ai_proxy_consent(self) -> None:
        """'May an AI answer on my behalf when I'm away?' — the student's own call, and only if the
        instructor granted hosting. Either one alone is not consent."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is None:
            return
        choice, ok = QInputDialog.getItem(
            self, "AI may answer for me",
            "When you're away, may an AI answer your groupmates on your behalf?\n"
            "(It is always labelled as an AI. It never speaks for you about anything personal,\n"
            "and it refuses deadlines, exams and grades.)",
            ["No — my messages just wait for me", "Yes — let it answer about coursework"], 0, False)
        if not ok:
            return
        on = choice.startswith("Yes")
        blurb = ""
        if on:
            blurb, _ = QInputDialog.getText(
                self, "Anything it should know?",
                "One line about what you're working on (optional):")
        res = tc.set_ai_proxy(on, blurb or "")
        if not res.get("ok"):
            QMessageBox.information(self, "Not available",
                                    res.get("error", "Your instructor hasn't enabled this."))
            return
        self.ctx.log(f"AI proxy {'enabled' if on else 'disabled'}.", "ok")

    def _heartbeat(self) -> None:
        """Tell the course server we're here, and where we are on the current mission — that's what
        makes a group view worth opening. Off the GUI thread; a failed beat is a non-event.

        Defensive throughout, because this fires on a QTimer: an exception raised here lands in the
        Qt event loop (noisy in the app, and fatal under pytest-qt, where it fails whichever test
        happens to be running). A client that can't beat — no client, a stub, a half-built one — is
        simply skipped."""
        tc = getattr(self.ctx, "teaching_center", None)
        signed_in = getattr(tc, "signed_in", None)
        beat = getattr(tc, "heartbeat", None)
        if not callable(signed_in) or not callable(beat):
            return
        try:
            if not signed_in():
                return
        except Exception:                                    # noqa: BLE001 — an unreachable centre
            return
        progress = {}
        try:
            ctrl = getattr(self.assistant, "_mission_ctrl", None)
            m = getattr(ctrl, "mission", None) if ctrl else None
            if m is not None:
                sc = m.score()
                res = m.last_results or []
                level = next((r.level for r in res if r.status != "met"), 4)
                progress = {"lesson_id": m.lesson.id, "title": m.lesson.title, "level": level,
                            "met": sc.met, "total": sc.total, "band": sc.band}
        except Exception:                                    # noqa: BLE001
            progress = {}

        def _beat() -> None:                                 # swallow in the worker too — a dropped
            try:                                             # beat must never surface as a warning
                beat(progress)
            except Exception:                                # noqa: BLE001
                pass
        import threading
        threading.Thread(target=_beat, daemon=True).start()

    def _persist_settings(self) -> None:
        """Save the current Settings to ~/.gini/config.json (used by the Cue Cards tour
        when the user toggles 'show at launch' / voice-over)."""
        from ..app.paths import PERSISTED_KEYS, save_config
        save_config({k: getattr(self.ctx.settings, k) for k in PERSISTED_KEYS})

    def show_feature_tour(self) -> None:
        from .cue_cards import CueCards
        CueCards(self, self.theme, self.ctx.settings, persist=self._persist_settings).exec()

    def maybe_start_tour(self) -> None:
        """Open the Cue Cards tour at launch unless the user turned it off (called from
        __main__ after the window is shown, so widget tests never trigger the modal)."""
        if self.ctx.settings.show_help_on_launch:
            self.show_feature_tour()

    def _apply_env_settings(self) -> None:
        import os
        s = self.ctx.settings
        if os.environ.get("GINI_LLM_URL"):
            s.llm_url = os.environ["GINI_LLM_URL"]
            s.llm_enabled = True
        if os.environ.get("GINI_LLM_MODEL"):
            s.llm_model = os.environ["GINI_LLM_MODEL"]
        if os.environ.get("GINI_LLM_THINK"):
            s.llm_think = os.environ["GINI_LLM_THINK"] not in ("0", "false", "")
        if os.environ.get("GINI_REDUCED_MOTION"):
            s.reduced_motion = True

    def _ai_context(self) -> str:
        """Live canvas snapshot fed to the assistant each turn (topology + run-state)."""
        digest = self.api.context_digest()
        state = "running on Docker" if self._running else "not running (idle, editable)"
        ctx = f"{digest}\nRuntime: the topology is {state}."
        m = getattr(self.ctx, "mission", None)            # Wizard: keep follow-ups goal-aware
        if m is not None:
            ctx += f"\nThe student's current build objective is: \"{m.goal}\"."
        return ctx

    def _wire_llm(self) -> None:
        s = self.ctx.settings
        if not s.llm_enabled:
            self.assistant.set_loop(None)              # clear any existing loop
            self.mode_indicator.set_model("", False)   # toolbar Model pill -> "no model"
            self.ctx.log("GINI AI: offline mode (deterministic). Enable a local LLM in "
                         "Settings (or set GINI_LLM_URL).", "info")
            return
        try:
            from ..agent.llm import OllamaBackend
            from ..agent.loop import AgentLoop
            backend = OllamaBackend(s.llm_url, s.llm_model, think=s.llm_think,
                                    num_ctx=getattr(s, "llm_num_ctx", 8192),
                                    embed_model=getattr(s, "llm_embed_model", "all-minilm"))
            self.assistant.set_loop(AgentLoop(backend, self.registry,
                                              context_provider=self._ai_context))
            self.mode_indicator.set_model(s.llm_model, True)       # optimistic; probe corrects
            # Check reachability OFF the GUI thread — backend.available() does a blocking
            # urlopen (up to 3s), which would freeze the UI on startup / settings-save.
            import threading
            url, model = s.llm_url, s.llm_model

            def probe():
                try:
                    ok = backend.available()
                except Exception:
                    ok = False
                self.ctx.bus.llm_reachable.emit(model, ok)
            threading.Thread(target=probe, daemon=True).start()
        except Exception as e:  # never let LLM wiring break startup
            self.mode_indicator.set_model("", False)
            self.ctx.log(f"GINI AI: LLM unavailable ({e}); offline mode.", "info")

    def _on_llm_reachable(self, model: str, ok: bool) -> None:
        s = self.ctx.settings
        self.mode_indicator.set_model(model, ok)                   # green if up, amber if not
        if ok:
            self.ctx.log(f"GINI AI: connected to {model} at {s.llm_url}.", "ok")
        else:
            self.ctx.log(f"GINI AI: set to {model} at {s.llm_url}, but the server isn't "
                         f"responding. Is Ollama running? (run: ollama serve)", "error")

    # -- toolbar ------------------------------------------------------------ #
    def _make_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)
        self._tb = tb
        self._actions: dict[str, QAction] = {}
        self._tb_buttons: dict[str, QToolButton] = {}

        def act(key: str, icon: str, text: str, slot, checkable=False) -> QAction:
            a = QAction(text, self)
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            self._actions[key] = (a, icon)
            return a

        # build the actions (same wiring as before — checkable, enable/disable, slots)
        # The LEFT tray works INSIDE the current project — the express menu for its experiments.
        # (Project-level operations — switch/create/delete a project — live on the centred
        # navigator, so the two surfaces mean different things instead of duplicating each other.)
        act("new", "new", "New experiment in this project", self._new_experiment)
        act("open", "open", "Open an experiment from this project", self._open_experiment)
        act("save", "save", "Save this experiment", self._save_project)
        act("compile", "compile", "Compile", self._compile)
        act("layout", "layout", "Arrange", self._auto_layout)
        self._connect_act = act("connect", "link", "Connect", self._toggle_connect, checkable=True)
        self._edges_act = act("edges", "elbow",
                              "Connector style: bent ↔ straight",
                              self._toggle_edge_style, checkable=True)
        self._edges_act.setChecked(self.ctx.settings.connector_style == "orthogonal")
        self._manual_addr_act = act("manualaddr", "pencil",
                                    "Manual addressing — assign IP addresses by hand",
                                    self._toggle_manual_addr, checkable=True)
        self._manual_addr_act.setChecked(self.ctx.topology.manual_addressing)
        self._dynroute_act = act("dynroute", "dynroute",
                                 "Dynamic routing — routers boot with connected routes only, "
                                 "so a routing protocol (e.g. RIP in the Lua control plane) "
                                 "builds the table. Off = static routes are pre-installed.",
                                 self._toggle_routing_mode, checkable=True)
        self._dynroute_act.setChecked(self.ctx.topology.routing_mode == "dynamic")
        self._delete_act = act("delete", "trash", "Delete selected — device, box, or link",
                               self._delete_selected)
        self._delete_act.setEnabled(False)
        self._rhud_act = act("rhud", "router",
                             "Network HUD — model view of the network's real forwarding; click a "
                             "router for its forwarding tree, or a switch for its L2 fabric", self._toggle_routing_hud,
                             checkable=True)
        self._oshud_act = act("oshud", "host",
                              "OS HUD — X-ray a running xv6 kernel: the causal story of a launch "
                              "across every subsystem, on one time axis. Scrub to replay.",
                              self._toggle_os_hud, checkable=True)
        self._fhud_act = act("fhud", "metrics",
                             "Flow HUD — live TCP congestion windows; click a flow to plot its "
                             "cwnd sawtooth with drops", self._toggle_flow_hud, checkable=True)
        self._mhud_act = act("mhud", "hub",
                             "Multicast HUD — live groups, joins, and the distribution tree's "
                             "per-interface copy rates (mcast_tree.lua)",
                             self._toggle_mcast_hud, checkable=True)
        self._run_act = act("run", "play", "Run", self._run)
        self._stop_act = act("stop", "stop", "Stop", self._stop)
        self._server_act = act("server", "cloud",
                               "Backend: run on a remote Kata GINI server (or go local)",
                               self._toggle_backend, checkable=True)
        act("zoom_in", "plus", "Zoom in", lambda: self.canvas.zoom_by(1.15))
        act("zoom_out", "minus", "Zoom out", lambda: self.canvas.zoom_by(1 / 1.15))

        def button(key: str, *, labelled=False, oname="") -> QToolButton:
            a, _ = self._actions[key]
            b = QToolButton(tb)
            b.setDefaultAction(a)                # mirrors icon/checkable/enabled/triggered
            b.setAutoRaise(True)
            if labelled:
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            if oname:
                b.setObjectName(oname)
            self._tb_buttons[key] = b
            return b

        def tray(keys, *, name="TbGroup") -> QWidget:
            """A rounded segmented cluster of icon buttons."""
            f = QFrame(tb); f.setObjectName(name)
            lay = QHBoxLayout(f); lay.setContentsMargins(3, 3, 3, 3); lay.setSpacing(1)
            for k in keys:
                lay.addWidget(button(k))
            return f

        from PySide6.QtWidgets import QHBoxLayout, QMenu, QSizePolicy

        def _cluster(build) -> QWidget:
            """A toolbar cluster in an EXPANDING container. Left and right clusters both
            expand, so they take equal widths — which lands the middle chip at the exact
            centre regardless of how much each side actually holds."""
            w = QWidget()
            lay = QHBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
            build(lay)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            return w

        # LEFT cluster: File · Tools · Run · Zoom, packed to the left.
        from .run_button import RunButton
        self.run_button = RunButton(self.theme)
        self.run_button.clicked.connect(self._toggle_run)

        def _build_left(lay) -> None:
            lay.addWidget(tray(("new", "open", "save")))
            lay.addWidget(self._tb_spacer(6))
            lay.addWidget(tray(("compile", "layout", "connect", "edges", "manualaddr",
                                "dynroute", "delete")))
            lay.addWidget(self._tb_spacer(8))
            lay.addWidget(self.run_button)                    # morphing ▶/■ power button
            lay.addWidget(self._tb_spacer(8))
            lay.addWidget(tray(("zoom_in", "zoom_out", "rhud", "fhud", "mhud", "oshud")))
            lay.addStretch(1)                                 # push the cluster left

        # RIGHT cluster: mode/model/activity pills · theme picker, packed to the right.
        from .mode_indicator import ModeIndicator
        self.mode_indicator = ModeIndicator(self.theme)
        self._theme_btn = QToolButton(tb)
        self._theme_btn.setToolTip("Theme")
        self._theme_btn.setAutoRaise(True)
        self._theme_btn.setPopupMode(QToolButton.InstantPopup)
        from .theme.tokens import get_theme
        menu = QMenu(self)
        grp = QActionGroup(self)
        self._theme_actions: dict[str, QAction] = {}

        def add_theme(name: str) -> None:
            a = QAction(_theme_swatch(get_theme(name)), name, self)
            a.setCheckable(True)
            a.setChecked(name.lower() == self.theme.theme.name.lower())
            a.triggered.connect(lambda _=False, n=name: self._pick_theme(n))
            grp.addAction(a); menu.addAction(a)
            self._theme_actions[name] = a

        for name in ("Light", "Sand", "Blue", "Green"):       # light family, lightest first
            add_theme(name)
        menu.addSeparator()                                   # divider between the families
        for name in ("Dark", "GINI Brand", "High Contrast"):  # dark family
            add_theme(name)
        self._theme_btn.setMenu(menu)

        def _build_right(lay) -> None:
            lay.addStretch(1)                                 # push the cluster right
            lay.addWidget(self.mode_indicator)
            lay.addWidget(self._tb_spacer(8))
            lay.addWidget(self._theme_btn)

        # assemble: [ left (expand) ] [ centred project chip ] [ right (expand) ]
        self._make_nav_button()
        tb.addWidget(_cluster(_build_left))
        tb.addWidget(self._nav_btn)
        tb.addWidget(_cluster(_build_right))
        self._refresh_icons()

    @staticmethod
    def _tb_spacer(width: int) -> QWidget:
        w = QWidget(); w.setFixedWidth(width)
        return w

    # -- project navigator -------------------------------------------------- #
    def _make_nav_button(self) -> None:
        from PySide6.QtWidgets import QMenu, QToolButton
        self._nav_btn = QToolButton(self)
        self._nav_btn.setObjectName("NavBtn")
        self._nav_btn.setText("Untitled")
        self._nav_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._nav_btn.setPopupMode(QToolButton.InstantPopup)
        self._nav_btn.setToolTip("Project — switch, create, save, or set the AI brief")
        menu = QMenu(self)
        menu.aboutToShow.connect(lambda: self._build_nav_menu(menu))
        self._nav_btn.setMenu(menu)

    def _build_nav_menu(self, menu) -> None:
        """The centred navigator is PROJECT-level: which project am I in, and switch/create/
        delete/list them. Experiments inside the current project are the left tray's job."""
        from ..app.paths import projects_dir
        from ..services import list_projects
        menu.clear()
        header = menu.addAction(f"● {self._project_name()}")
        header.setEnabled(False)
        sub = menu.addAction(f"    {len(self._experiments())} experiment(s) · "
                             f"now: {self._experiment or '—'}")
        sub.setEnabled(False)
        menu.addSeparator()
        menu.addAction("New project…", self._new_project)
        menu.addAction("Open project…", self._open_project_dialog)
        menu.addSeparator()
        projects = list_projects(projects_dir())[:8]
        if projects:
            r = menu.addAction("Switch project"); r.setEnabled(False)
            for info in projects:
                n = info.get("experiments", 0)
                act = menu.addAction(f"   {info['name']}" + (f"  ({n})" if n else ""))
                act.setCheckable(True)
                act.setChecked(info["path"] == self._project_dir)
                act.triggered.connect(lambda _=False, p=info["path"]: self._switch_project(p))
            menu.addSeparator()
        menu.addAction("Edit project brief…", self._edit_brief)
        rev = menu.addAction("Reveal project folder", self._reveal_project)
        rev.setEnabled(self._project_dir is not None)
        menu.addSeparator()
        ren = menu.addAction("Rename project…", self._rename_project)
        ren.setEnabled(self._project_dir is not None)
        dele = menu.addAction("Delete project…", self._delete_project)
        dele.setEnabled(self._project_dir is not None)

    def _set_project_label(self, name: str) -> None:
        if hasattr(self, "_nav_btn"):
            self._nav_btn.setText(name or "Untitled")

    def _project_name(self) -> str:
        from pathlib import Path
        return Path(self._project_dir).name if self._project_dir else "Untitled"

    def _experiments(self) -> list[str]:
        from ..services import list_experiments
        if not self._project_dir:
            return []
        return [e["name"] for e in list_experiments(self._project_dir)]

    def _show_where(self) -> None:
        """The nav chip names the project; the title adds › experiment once there's a choice to
        make. With a single experiment the suffix is noise, so it's left off."""
        proj = self._project_name()
        self._set_project_label(proj)
        exp = self._experiment
        many = len(self._experiments()) > 1
        self.setWindowTitle(f"gBuilder 6.0 — {proj}" + (f" › {exp}" if exp and many else ""))

    # -- experiments (inside the current project) ---------------------------- #
    def _new_experiment(self) -> None:
        """Add another experiment to this project. The brief and the Ask GINI conversation are
        project-level, so they carry over — that's the whole point of grouping experiments."""
        from PySide6.QtWidgets import QInputDialog
        from ..services import experiment_path
        if self._switch_blocked():
            return
        if not self._project_dir:
            self._save_project_as()                  # no project yet — make one first
            if not self._project_dir:
                return
        name, ok = QInputDialog.getText(self, "New experiment", "Experiment name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if experiment_path(self._project_dir, name).exists():
            self.ctx.log(f"“{name}” already exists in this project.", "info")
            return
        self._persist_current_project()              # keep the outgoing experiment
        self._router_programs.clear()
        from ..domain.topology import Topology
        self._experiment = name
        self._set_topology(Topology(name))
        self.assistant.note_experiment(name)         # the canvas changed — tell the tutor
        self._persist_current_project()              # materialise the new file
        self._show_where()
        self.ctx.log(f"New experiment “{name}” in “{self._project_name()}”.", "ok")

    def _open_experiment(self) -> None:
        """Pick another experiment in this project (the left tray's express switcher)."""
        from PySide6.QtWidgets import QInputDialog
        if self._switch_blocked():
            return
        names = self._experiments()
        if not names:
            self.ctx.log("This project has no other experiments yet — press ＋ to add one.", "info")
            return
        cur = self._experiment if self._experiment in names else names[0]
        name, ok = QInputDialog.getItem(self, "Open experiment",
                                        f"Experiments in “{self._project_name()}”:",
                                        names, names.index(cur), False)
        if ok and name:
            self._switch_experiment(name)

    def _switch_experiment(self, name: str) -> None:
        from ..services import load_experiment
        if not self._project_dir or name == self._experiment or self._switch_blocked():
            return
        self._persist_current_project()              # save the one we're leaving
        try:
            topo = load_experiment(self._project_dir, name)
        except Exception as e:                       # noqa: BLE001
            self.ctx.log(f"Couldn't open “{name}”: {e}", "error")
            return
        self._router_programs.clear()
        self._experiment = name
        self._set_topology(topo)
        self.assistant.note_experiment(name)         # conversation continues, canvas changed
        self._persist_current_project()              # remember which one is current
        self._show_where()
        self.ctx.log(f"Opened experiment “{name}”.", "ok")

    def _rename_experiment(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        from ..services import rename_experiment
        if not (self._project_dir and self._experiment) or self._switch_blocked():
            return
        new, ok = QInputDialog.getText(self, "Rename experiment", "New name:",
                                       text=self._experiment)
        new = (new or "").strip()
        if not ok or not new or new == self._experiment:
            return
        self._persist_current_project()
        if not rename_experiment(self._project_dir, self._experiment, new):
            self.ctx.log(f"Couldn't rename to “{new}” (does it already exist?).", "info")
            return
        self._experiment = new
        self.ctx.topology.name = new
        self._persist_current_project()
        self._show_where()
        self.ctx.log(f"Renamed experiment to “{new}”.", "ok")

    def _delete_experiment(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from ..services import delete_experiment
        if not (self._project_dir and self._experiment) or self._switch_blocked():
            return
        names = self._experiments()
        if len(names) <= 1:
            self.ctx.log("This is the project's only experiment — delete the project instead.",
                         "info")
            return
        if QMessageBox.question(self, "Delete experiment",
                                f"Delete “{self._experiment}” from this project?\n"
                                f"The project's brief and AI conversation are kept."
                                ) != QMessageBox.Yes:
            return
        gone = self._experiment
        delete_experiment(self._project_dir, gone)
        nxt = next((n for n in self._experiments() if n != gone), None)
        if nxt:
            self._experiment = None                  # force the switch to actually load
            self._switch_experiment(nxt)
        self.ctx.log(f"Deleted experiment “{gone}”.", "ok")

    # -- project operations ------------------------------------------------- #
    def _switch_blocked(self) -> bool:
        """Switching projects would pull the topology out from under a running lab."""
        if self._running or getattr(self, "_stopping", False):
            self.ctx.log("Stop the topology before switching projects.", "info")
            return True
        return False

    def _new_project(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QInputDialog
        from ..app.paths import ensure_dirs, projects_dir
        from ..domain import Topology
        if self._switch_blocked():
            return
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        ensure_dirs()
        folder = projects_dir() / name
        if folder.exists():
            self.ctx.log(f"A project named “{name}” already exists.", "info")
            return
        from ..services.project import FIRST_EXPERIMENT
        self._persist_current_project()          # save whatever we were on
        self._project_dir = str(folder)
        self._project_path = None
        self._experiment = FIRST_EXPERIMENT      # neutral — never the project's own name
        self._router_programs.clear()
        self._set_topology(Topology(name))
        self.assistant.clear_conversation()      # a NEW project = a fresh context (unlike a
        self._show_where()                       # new experiment, which keeps the conversation)
        self._persist_current_project()          # materialise the folder on disk
        self.ctx.log(f"New project “{name}”.", "ok")

    def _rename_project(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QInputDialog
        from ..app.paths import projects_dir, remember_project
        if self._project_dir is None or self._switch_blocked():
            return
        cur = Path(self._project_dir)
        name, ok = QInputDialog.getText(self, "Rename project", "New name:", text=cur.name)
        if not ok or not name.strip() or name.strip() == cur.name:
            return
        name = name.strip()
        dest = projects_dir() / name
        if dest.exists():
            self.ctx.log(f"A project named “{name}” already exists.", "info")
            return
        self._persist_current_project()          # flush current work to the old folder first
        try:
            cur.rename(dest)                     # rename the folder on disk
        except OSError as e:
            self.ctx.log(f"Couldn't rename project: {e}", "error")
            return
        self._project_dir = str(dest)
        self.ctx.topology.name = name
        self._set_project_label(name)
        self.setWindowTitle(f"gBuilder 6.0 — {name}")
        remember_project(str(dest))
        self._persist_current_project()          # rewrite metadata under the new name
        self.ctx.log(f"Renamed project to “{name}”.", "ok")

    def _delete_project(self) -> None:
        import shutil
        from pathlib import Path
        from PySide6.QtWidgets import QMessageBox
        from ..app.paths import projects_dir
        from ..domain import Topology
        from ..services import list_projects
        if self._project_dir is None or self._switch_blocked():
            return
        cur = Path(self._project_dir)
        name = cur.name
        if QMessageBox.question(
                self, "Delete project",
                f"Delete project “{name}” and all its files?\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._project_dir = None                 # stop any later save from recreating it
        try:
            shutil.rmtree(cur)
        except OSError as e:
            self._project_dir = str(cur)         # deletion failed -> keep the project active
            self.ctx.log(f"Couldn't delete project: {e}", "error")
            return
        self.ctx.log(f"Deleted project “{name}”.", "ok")
        others = [p for p in list_projects(projects_dir()) if p["path"] != str(cur)]
        if others:
            self._load_project_folder(others[0]["path"])   # open the next project
        else:                                    # nothing left -> a fresh, unsaved project
            self._project_path = None
            self._router_programs.clear()
            self._set_topology(Topology("Untitled"))
            self.assistant.clear_conversation()
            self._set_project_label("Untitled")
            self.setWindowTitle("gBuilder 6.0")

    def _open_project_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from ..app.paths import projects_dir
        from ..services import is_project_dir
        if self._switch_blocked():
            return
        path = QFileDialog.getExistingDirectory(self, "Open project", str(projects_dir()))
        if not path:
            return
        if not is_project_dir(path):
            self.ctx.log("That folder isn't a GINI project (no topology.gini inside).", "info")
            return
        self._switch_project(path)

    def _switch_project(self, path: str) -> None:
        if path == self._project_dir or self._switch_blocked():
            return
        self._persist_current_project()          # keep the current project's work + chat
        self._load_project_folder(path)

    def _load_project_folder(self, path: str) -> None:
        from ..app.paths import remember_project
        from ..services import load_project_dir
        try:
            data = load_project_dir(path)
        except Exception as e:
            self.ctx.log(f"Open failed: {e}", "error")
            return
        self._router_programs.clear()
        self._project_dir = data["path"]
        self._project_path = None
        self._experiment = data.get("experiment")
        self._set_topology(data["topology"])
        self.assistant.set_brief(data["brief"])
        self.assistant.load_ai_state(data["ai_state"])   # swap the Ask GINI conversation
        self._show_where()
        remember_project(data["path"])
        n = len(data.get("experiments") or [])
        self.ctx.log(f"Opened project “{data['name']}” ({n} experiment(s), "
                     f"now “{self._experiment}”).", "ok")

    def _persist_current_project(self) -> None:
        """Write the active project: the CURRENT experiment's topology, plus the project-level
        brief and Ask GINI conversation (shared by every experiment in the project)."""
        if not self._project_dir:
            return
        from pathlib import Path
        from ..app.paths import remember_project
        from ..services import save_project_dir
        from ..services.project import FIRST_EXPERIMENT
        name = Path(self._project_dir).name
        # Fall back to the neutral first-experiment name, NEVER the project's own. This used to be
        # `self._experiment or name`, which meant any caller that forgot to set `_experiment` (the
        # Default project, Save-As) silently produced an experiment named after its project — so
        # the experiment list looked like it contained the project itself.
        exp = self._experiment or FIRST_EXPERIMENT
        self.ctx.topology.name = exp
        save_project_dir(self._project_dir, self.ctx.topology, name=name,
                         brief=self.assistant.brief(), ai_state=self.assistant.ai_state(),
                         experiment=exp)
        remember_project(self._project_dir)

    def _save_project(self) -> None:
        if self._project_dir:
            self._persist_current_project()
            self.ctx.log(f"Saved project “{self._nav_btn.text()}”.", "ok")
        else:
            self._save_project_as()

    def _save_project_as(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QInputDialog
        from ..app.paths import ensure_dirs, projects_dir
        default = self.ctx.topology.name or "untitled"
        name, ok = QInputDialog.getText(self, "Save project as", "Project name:", text=default)
        if not ok or not name.strip():
            return
        from ..services.project import FIRST_EXPERIMENT
        ensure_dirs()
        self._project_dir = str(projects_dir() / name.strip())
        self._project_path = None
        # never name the first experiment after the project (same rule as _new_project) — it made
        # the experiment list look like it contained the project, and it collided with the very
        # next "New experiment…" if the student typed the same name.
        self._experiment = self._experiment or FIRST_EXPERIMENT
        self._show_where()
        self._persist_current_project()
        self.ctx.log(f"Saved project “{name.strip()}”.", "ok")

    def _edit_brief(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(
            self, "Project brief",
            "A short framing for the AI tutor in this project (guides every answer):",
            self.assistant.brief())
        if not ok:
            return
        self.assistant.set_brief(text)
        self._persist_current_project()
        self.ctx.log("Project brief updated.", "ok")

    def _reveal_project(self) -> None:
        if not self._project_dir:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._project_dir))

    def restore_last_project(self) -> None:
        """Reopen the project from the previous session (called by the app entry point).
        If there's no prior project, land in a persistent 'Default' project so the very
        first session's work and conversation survive a restart too."""
        from ..app.paths import load_recents
        from ..services import is_project_dir
        last = load_recents().get("last")
        if last and is_project_dir(last):
            self._load_project_folder(last)
        else:
            self._open_or_create_default()
        self._start_autosave()               # crash-safety on top of save-on-close

    def _open_or_create_default(self) -> None:
        from ..app.paths import ensure_dirs, projects_dir
        from ..services import is_project_dir
        ensure_dirs()
        d = projects_dir() / "Default"
        if is_project_dir(d):
            self._load_project_folder(str(d))
            return
        from ..services.project import FIRST_EXPERIMENT
        self._project_dir = str(d)           # adopt the current (empty) canvas as Default
        self._experiment = FIRST_EXPERIMENT
        self._show_where()
        self._persist_current_project()      # materialise it so it's there next launch
        self.ctx.log("Working in the Default project — your topology and Ask GINI "
                     "conversation here are saved and restored across restarts.", "info")

    def _start_autosave(self) -> None:
        """Persist the active project every so often, so a crash or force-quit loses at
        most the last few seconds (save-on-close handles the normal path)."""
        from PySide6.QtCore import QTimer
        if getattr(self, "_autosave_timer", None) is not None:
            return
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._persist_current_project)
        self._autosave_timer.start()

    def containers_busy(self) -> bool:
        """True while containers of ours may exist — which is wider than `_running`.

        `_running` only goes true once `up` REPORTS success. On a real topology `docker compose up`
        takes tens of seconds, and for all of it containers are appearing while `_running` is still
        False — so a quit in that window was allowed, the window vanished, and the lab stayed up.
        From outside that is indistinguishable from a crash: no traceback, no crash report, just an
        app that is gone and containers that are not.

        A failed launch has the same shape (`compose up` can bring half a topology up and still
        fail), and stopping does too, which is why `_switch_blocked` already refused to change
        project on `_stopping` while quitting — the more destructive act — did not.
        """
        return bool(self._running or self._launching or self._orphaned
                    or getattr(self, "_stopping", False))

    def _busy_quit_message(self) -> str:
        if self._launching:
            return ("The topology is still starting — wait for it to finish, then press Stop "
                    "before quitting.")
        return "The topology is still running — press Stop before quitting."

    def closeEvent(self, e) -> None:
        if self.containers_busy():           # don't quit out from under a live topology
            self.ctx.log(self._busy_quit_message(), "error")
            e.ignore()
            return
        self._persist_current_project()      # never lose the active project's work / chat
        # Reap any hardware worker still talking to a board over USB. Quitting while one
        # runs garbage-collects a live QThread, which Qt turns into an abort — so the app
        # would "crash on exit" for anyone who closed the window mid-scan.
        from .worker_host import drain
        drain()
        super().closeEvent(e)

    def _quit_blocked(self) -> bool:
        """Backstop for macOS ⌘Q / the app-menu Quit, which can bypass closeEvent: refuse the
        application-level Quit while a topology is running. (The window close-button path is
        handled by closeEvent.) True = consume the quit.

        Called by GiniApplication.event(). Deliberately NOT an eventFilter — see ui/app.py.
        """
        if self.containers_busy():
            self.ctx.log(self._busy_quit_message(), "error")
            return True
        return False

    def _toggle_routing_hud(self, checked: bool) -> None:
        """Show/hide the Network HUD — a model view of the whole network's authentic forwarding
        (click a router → its shortest-path/forwarding tree). Overlaid top-right of the canvas."""
        try:
            if checked:
                # only one HUD at a time — turn the other HUDs off if they are on
                if getattr(self, "_fhud", None) is not None:
                    self._fhud.close()
                if hasattr(self, "_fhud_act"):
                    self._fhud_act.setChecked(False)
                if getattr(self, "_mhud", None) is not None:
                    self._mhud.close()
                if hasattr(self, "_mhud_act"):
                    self._mhud_act.setChecked(False)
                if getattr(self, "_oshud", None) is not None:
                    self._oshud.close()
                if hasattr(self, "_oshud_act"):
                    self._oshud_act.setChecked(False)
                if getattr(self, "_rhud", None) is None:
                    from .routing_hud import RoutingHudController
                    # Read the device dict THROUGH self.ctx on every call. Capturing
                    # `devs = self.ctx.topology.devices` once bound the HUD to whatever
                    # topology happened to be open when it was first toggled on: loading a
                    # project REPLACES ctx.topology (main_window ~1891), so the closure kept
                    # querying the previous network's routers and the HUD went on drawing
                    # them, live-looking, over the new canvas.
                    self._rhud = RoutingHudController(
                        self.canvas, self.theme,
                        router_devices=lambda: [
                            (d.id, d.name) for d in self.ctx.topology.devices.values()
                            if d.type_key in ("router", "firewall")],
                        query=self.element_query,
                        delay_prop=lambda rid, k: (
                            self.ctx.topology.devices[rid].properties.get(k, "")
                            if rid in self.ctx.topology.devices else ""),
                        positions_of=lambda: {d.id: (d.x, d.y)
                                              for d in self.ctx.topology.devices.values()},
                        switch_devices=lambda: [
                            (d.id, d.name, self._ovs_controller(d.id))
                            for d in self.ctx.topology.devices.values()
                            if d.type_key == "ovs"],
                        neighbours_of=self._ovs_link_peers,
                        mac_of=self._hud_mac_of,
                        ip_of=self._hud_ip_of,
                        topo_links=lambda: [(l.source_id, l.target_id)
                                            for l in self.ctx.topology.links.values()],
                        # only self-learning L2 devices may be walked THROUGH; a machine is an
                        # endpoint, and bridging one would invent a link that does not exist
                        passthrough_of=lambda: {
                            d.id for d in self.ctx.topology.devices.values()
                            if d.type_key in ("switch", "hub")},
                        controllers_of=lambda: {
                            d.id: d.name for d in self.ctx.topology.devices.values()
                            if d.type_key == "controller"},
                        # Says why the HUD is dark, in the Console. A dark panel has several
                        # causes that look identical on screen; this names the one in force.
                        log=lambda m: self.ctx.bus.log.emit("info", m))
                self._rhud.reset()          # a fresh topology has no shared convergence history
                self._rhud.show_topright()
            elif getattr(self, "_rhud", None) is not None:
                self._rhud.close()
        except Exception as e:
            self.ctx.bus.log.emit("error", f"Network HUD: {e}")

    def _toggle_flow_hud(self, checked: bool) -> None:
        """Show/hide the Flow HUD — live TCP congestion windows read from `ss -tin` on the
        stations. Click a flow chip to plot its cwnd sawtooth with drop marks. Top-right."""
        try:
            if checked:
                # only one HUD at a time — turn the other HUDs off if they are on
                if getattr(self, "_rhud", None) is not None:
                    self._rhud.close()
                if hasattr(self, "_rhud_act"):
                    self._rhud_act.setChecked(False)
                if getattr(self, "_mhud", None) is not None:
                    self._mhud.close()
                if hasattr(self, "_mhud_act"):
                    self._mhud_act.setChecked(False)
                if getattr(self, "_oshud", None) is not None:
                    self._oshud.close()
                if hasattr(self, "_oshud_act"):
                    self._oshud_act.setChecked(False)
                if getattr(self, "_fhud", None) is None:
                    from .flow_hud import FlowHudController
                    from ..services.compiler import _role
                    # Read through self.ctx every call: loading a project REPLACES
                    # ctx.topology, so a captured devices dict pins this HUD to the
                    # topology that was open when it was first toggled on.
                    self._fhud = FlowHudController(
                        self.canvas, self.theme,
                        machines=lambda: [d.name for d in self.ctx.topology.devices.values()
                                          if _role(d.type_key) == "machine"],
                        query=self._machine_shell,   # docker exec `ss -tin` in the station
                        window_getter=lambda: int(
                            getattr(self.ctx.settings, "flow_hud_window_s", 60) or 60))
                self._fhud.show_topright()
            elif getattr(self, "_fhud", None) is not None:
                self._fhud.close()
        except Exception as e:
            self.ctx.bus.log.emit("error", f"Flow HUD: {e}")

    def _toggle_mcast_hud(self, checked: bool) -> None:
        """Show/hide the Multicast HUD — live multicast groups, member interfaces, and
        per-interface copy rates, polled from `gpipe cp status` on every router (the
        mcast_tree.lua forwarder publishes the snapshots). Top-right of the canvas."""
        try:
            if checked:
                # only one HUD at a time — turn the other HUDs off if they are on
                for attr, act_attr in (("_rhud", "_rhud_act"), ("_fhud", "_fhud_act")):
                    if getattr(self, attr, None) is not None:
                        getattr(self, attr).close()
                    if hasattr(self, act_attr):
                        getattr(self, act_attr).setChecked(False)
                if getattr(self, "_mhud", None) is None:
                    from .mcast_hud import McastHudController
                    # Read through self.ctx every call — see the Network HUD note above.
                    self._mhud = McastHudController(
                        self.canvas, self.theme,
                        routers=lambda: [d.name for d in self.ctx.topology.devices.values()
                                         if d.type_key in ("router", "firewall")],
                        query=self.element_query)
                self._mhud.show_topright()
            elif getattr(self, "_mhud", None) is not None:
                self._mhud.close()
        except Exception as e:
            self.ctx.bus.log.emit("error", f"Multicast HUD: {e}")

    def _toggle_os_hud(self, checked: bool) -> None:
        """Show/hide the OS HUD — the kernel board, with the X-ray swimlanes beneath it. Needs a
        RUNNING xv6 Machine: the board comes from that kernel's subsystem counters and the events
        from its rings, merged by the global event clock."""
        try:
            if checked:
                for other, act_name in (("_rhud", "_rhud_act"), ("_fhud", "_fhud_act"),
                                        ("_mhud", "_mhud_act")):
                    if getattr(self, other, None) is not None:
                        getattr(self, other).close()
                    if hasattr(self, act_name):
                        getattr(self, act_name).setChecked(False)
                if getattr(self, "_oshud", None) is None:
                    from .os_hud import OsHudController
                    self._oshud = OsHudController(
                        self.canvas, self.theme,
                        agent_of=self._xv6_agent,
                        on_source=self._open_kernel_source,
                        window_getter=lambda: int(
                            getattr(self.ctx.settings, "os_hud_window_s", 10) or 10),
                        scrub_getter=lambda: int(
                            getattr(self.ctx.settings, "os_hud_scrub_s", 120) or 120))
                self._oshud.show_topright()
            elif getattr(self, "_oshud", None) is not None:
                self._oshud.close()
        except Exception as e:
            self.ctx.bus.log.emit("error", f"OS HUD: {e}")

    def _xv6_state(self):
        """The MachineState of the first xv6 Machine on the canvas, via the same shared
        `ctx.machine_states` the Lab and the Ask GINI agent use — so the HUD reads exactly the
        state the rest of the app sees."""
        for d in self.ctx.topology.devices.values():
            if getattr(d, "type_key", "") == "xv6":
                return self._machine_state_for(d.id)
        return None

    def _open_kernel_source(self, block: str, files) -> None:
        """A block on the kernel board was double-clicked: raise the GINI Source tab on it.

        Kept here rather than in the HUD because the HUD has no business knowing what docks
        exist — it emits "someone wanted this block's source" and the window decides where that
        lands.
        """
        try:
            self.source_browser.show_block(block, files)
            self._source_dock.show()
            self._source_dock.raise_()
        except Exception as e:                        # noqa: BLE001 - never take the app down
            self.ctx.bus.log.emit("error", f"GINI Source: {e}")

    def _xv6_agent(self):
        """The in-container agent client of that machine, or None when nothing is running.
        The HUD reads the kernel's event rings through it."""
        st = self._xv6_state()
        return getattr(getattr(st, "provider", None), "agent", None) if st else None

    # The Mode lane retired with the board: its user/kernel split and boundary-crossing ticks are
    # both done better by the board's user strip and its three doors, and it carried a real defect
    # — it reported SINCE-BOOT tick counters under a "last 10 s" caption. The board differences
    # every counter into per-window rates (domain/kernel_board.Window), so that class of lie has
    # one place it can be fixed rather than one place per view. `modetime` itself is untouched and
    # still feeds the Machine Lab.

    def _refresh_icons(self) -> None:
        t = self.theme.theme
        col = t.muted
        for a, icon_name in self._actions.values():
            a.setIcon(icons.icon(icon_name, col, 19))
        # coloured dots on the button say "this changes colours"; keep the menu ticks
        # in sync with whatever theme is active (e.g. changed from the Settings dialog)
        self._theme_btn.setIcon(_theme_dots_icon(t))
        for name, act in getattr(self, "_theme_actions", {}).items():
            act.setChecked(name.lower() == t.name.lower())
        if hasattr(self, "_nav_btn"):
            self._nav_btn.setIcon(icons.icon("open", t.accent, 18))
        # the circular Run/Stop power button repaints itself in the new theme
        if hasattr(self, "run_button"):
            self.run_button.refresh_theme()

    # -- docks -------------------------------------------------------------- #
    def _make_docks(self) -> None:
        self.palette = Palette(self.theme)
        left = QDockWidget("Devices", self)
        left.setObjectName("dock_palette")
        left.setWidget(self.palette)
        left.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, left)

        self.inspector = Inspector(self.ctx, self.api, self.theme)
        self.inspector.query_fn = self.element_query
        # Boards report through the relay, not a container — see Inspector._board_live_text.
        # The CACHE, not the relay. This is called synchronously during _rebuild --
        # twice for a gini32 element -- and board_status() is a blocking 2s HTTP GET.
        # The poll worker keeps this at most one tick (3s) stale, which is fresher than
        # the panel is redrawn anyway.
        self.inspector.board_status_fn = lambda: getattr(self, "_board_status_raw", None)
        self.inspector.board_action_fn = self.board_action
        self.inspector.stats_fn = self._element_stats
        self.inspector.stats_all_fn = self._element_stats_all
        insp = QDockWidget("Inspector", self)
        insp.setObjectName("dock_inspector")
        insp.setWidget(self.inspector)
        self.addDockWidget(Qt.RightDockWidgetArea, insp)

        self.assistant = Assistant(self.ctx, self.api, self.theme)
        asst = QDockWidget("Ask GINI", self)
        asst.setObjectName("dock_assistant")
        asst.setWidget(self.assistant)
        self.addDockWidget(Qt.RightDockWidgetArea, asst)
        self.tabifyDockWidget(insp, asst)

        # GINI Source — the kernel's own code, read-only, in the same pane as the Inspector.
        # Double-clicking a block on the OS HUD's kernel board raises this tab with that block's
        # file open and a jump list of the entry points the board counts. The source is served
        # from inside the container, so it is the PATCHED tree the running kernel was built from.
        from .source_browser import SourceBrowser
        self.source_browser = SourceBrowser(self.theme, fetch_fn=self._xv6_agent)
        srcd = QDockWidget("GINI Source", self)
        srcd.setObjectName("dock_source")
        srcd.setWidget(self.source_browser)
        self.addDockWidget(Qt.RightDockWidgetArea, srcd)
        self.tabifyDockWidget(asst, srcd)
        self._source_dock = srcd

        # Terminal — a real shell (or the gRouter CLI) on whichever element is selected, served by
        # ttyd from inside that element's own container. Follows the selection quietly; it never
        # raises itself, so it cannot steal the pane from the Inspector mid-read. The view inside
        # is built only when this tab is actually visible — see terminal_panel.py.
        from .terminal_panel import TerminalPanel
        self.terminal_panel = TerminalPanel(
            self.theme,
            workdir_fn=lambda: str(getattr(self, "_workdir", "") or ""),
            running_fn=lambda: bool(getattr(self, "_running", False)),
            record_fn=lambda dev, cmd, out: self.proof_recorder.note_command(dev, cmd, out),
        )
        termd = QDockWidget("Terminal", self)
        termd.setObjectName("dock_terminal")
        termd.setWidget(self.terminal_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, termd)
        self.tabifyDockWidget(srcd, termd)
        self._terminal_dock = termd
        # selection_changed is Signal(object) — the device id ALONE. The panel needs the topology
        # to resolve it, so it goes through an adapter rather than being connected directly.
        # (TerminalPanel subscribes to theme.themeChanged itself, as SourceBrowser does.)
        self.ctx.bus.selection_changed.connect(self._on_selection_terminal)

        insp.raise_()

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("Console")
        self.console.setMaximumBlockCount(2000)
        cons = QDockWidget("Console", self)
        cons.setObjectName("dock_console")
        cons.setWidget(self.console)
        self.addDockWidget(Qt.BottomDockWidgetArea, cons)

        # analytics strip — a short "cloud bill" dashboard stacked under the Console
        from .dashboard import Dashboard
        self.dashboard = Dashboard(self.theme)
        # The proof-of-activity control goes on the RIGHT of the strip, after the cost breakdown
        # and before the run-state text, so the strip reads money → work → status. Inserted from
        # here rather than built into Dashboard, so the cost meter stays one self-contained thing.
        # Positioned relative to the end (not a fixed index) because it must stay past the
        # stretch that pushes the tail of the strip rightward, even if the meters change.
        from PySide6.QtCore import QTimer as _QTimer
        from .proof_strip import ProofStrip
        self.proof_strip = ProofStrip(self.theme, self.proof_recorder)
        # Catch up on anything that never reached the course server. Deferred so it cannot slow
        # the window opening, and it runs on a worker thread from there — the usual recovery for
        # a student whose wifi died mid-submission is simply reopening gBuilder on campus.
        _QTimer.singleShot(2000, self.proof_strip.flush_outbox)
        _dash = self.dashboard.layout()
        _dash.insertWidget(max(0, _dash.count() - 1), self.proof_strip)
        dash = QDockWidget("Dashboard", self)
        dash.setObjectName("dock_dashboard")
        dash.setWidget(self.dashboard)
        self.addDockWidget(Qt.BottomDockWidgetArea, dash)
        self.splitDockWidget(cons, dash, Qt.Vertical)   # dashboard below the console
        self.resizeDocks([left], [240], Qt.Horizontal)
        self.resizeDocks([cons, dash], [180, 96], Qt.Vertical)
        self._rebill()

    def _make_statusbar(self) -> None:
        self.status_conn = QLabel()
        self.status_counts = QLabel()
        self.status_theme = QLabel()
        sb = self.statusBar()
        sb.addWidget(self.status_conn)
        sb.addPermanentWidget(self.status_counts)
        sb.addPermanentWidget(self.status_theme)

    # -- actions ------------------------------------------------------------ #
    # -- project persistence ------------------------------------------------ #
    def _make_menubar(self) -> None:
        from PySide6.QtGui import QKeySequence
        mb = self.menuBar()
        filem = mb.addMenu("&File")

        def add(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        # Experiments (inside the current project) — the everyday verbs get the shortcuts.
        add(filem, "&New Experiment…", self._new_experiment, "Ctrl+N")
        add(filem, "&Open Experiment…", self._open_experiment, "Ctrl+O")
        add(filem, "&Save Experiment", self._save_project, "Ctrl+S")
        add(filem, "&Rename Experiment…", self._rename_experiment)
        add(filem, "&Delete Experiment…", self._delete_experiment)
        filem.addSeparator()
        # Projects (the container: brief + AI conversation + a family of experiments)
        add(filem, "New &Project…", self._new_project, "Ctrl+Shift+N")
        add(filem, "Open Pro&ject…", self._open_project_dialog, "Ctrl+Shift+O")
        add(filem, "Save Project &As…", self._save_project_as, "Ctrl+Shift+S")
        add(filem, "Edit Project &Brief…", self._edit_brief)
        filem.addSeparator()
        add(filem, "&Import topology (.gini)…", self._open)   # legacy single-file import
        add(filem, "&Export PNG…", self._export_png)
        filem.addSeparator()
        # NoRole keeps these in the File menu on macOS (Qt otherwise hoists "Settings"
        # and "Quit" into the application menu, where the book's readers don't expect them)
        settings_act = add(filem, "&Settings…", self._open_settings, "Ctrl+,")
        settings_act.setMenuRole(QAction.MenuRole.NoRole)
        filem.addSeparator()
        quit_act = add(filem, "&Quit", self.close, "Ctrl+Q")
        quit_act.setMenuRole(QAction.MenuRole.NoRole)

        # Hardware: real GINI32 boards. Its own menu because setting a board up is an
        # action a student takes repeatedly, not a preference — and because a first-time
        # user has to be able to FIND it without being told where to look.
        # Ordered as a board's life runs, not alphabetically: flash a new one, set it up,
        # release it when it is someone else's turn, and list what is out there. Flashing
        # comes FIRST because it is the only step a brand-new board can start with — Set
        # Up talks to a console that does not exist until firmware is on the board.
        hwm = mb.addMenu("Hard&ware")
        flash_act = add(hwm, "&Flash a Board…", self._flash_board)
        flash_act.setMenuRole(QAction.MenuRole.NoRole)
        setup_act = add(hwm, "&Set Up a Board…", self._setup_board, "Ctrl+Shift+B")
        setup_act.setMenuRole(QAction.MenuRole.NoRole)
        reset_act = add(hwm, "&Reset a Board…", self._reset_board)
        reset_act.setMenuRole(QAction.MenuRole.NoRole)
        hwm.addSeparator()
        add(hwm, "&List Boards…", self._show_boards)

        # Teacher: verifying a student's proof is an action a marker takes repeatedly, not a
        # preference, so it gets a menu of its own rather than a corner of Settings.
        tm = mb.addMenu("&Teacher")
        # Issue codes is parked: it mints codes the course server has never heard of, so a student
        # who types one is refused. The Teaching Center vends codes. See app/features.py.
        from ..app import features
        if features.on("teacher.issue_codes"):
            issue_act = add(tm, "&Issue codes…", self._issue_codes)
            issue_act.setMenuRole(QAction.MenuRole.NoRole)
        open_act = add(tm, "&Open a submission…", self._open_submission)
        open_act.setMenuRole(QAction.MenuRole.NoRole)
        # Verifying a proof locally is parked (app/features.py): the Teaching Center now verifies
        # AND keeps it, so a checked submission is no longer one that exists nowhere.
        if features.on("teacher.verify_proof"):
            verify_act = add(tm, "&Verify proof…", self._verify_proof)
            verify_act.setMenuRole(QAction.MenuRole.NoRole)
        late_act = add(tm, "Accept a &late submission…", self._accept_late)
        late_act.setMenuRole(QAction.MenuRole.NoRole)

        helpm = mb.addMenu("&Help")
        tour_act = add(helpm, "&Feature Tour…", self.show_feature_tour)
        tour_act.setMenuRole(QAction.MenuRole.NoRole)

    # ---------------------------------------------------------------- GINI32 boards

    def _known_board_ids(self) -> list[str]:
        """Every board id we have reason to believe exists: ones on this canvas, and
        ones a running relay has actually heard from."""
        ids = []
        for node in getattr(self.canvas.scene_, "nodes", {}).values():
            if node.inst.type_key == "gini32":
                bid = str((node.inst.properties or {}).get("BoardID", "")).strip()
                if bid:
                    ids.append(bid)
        for bid in (getattr(self, "_board_state", None) or {}):
            if bid not in ids:
                ids.append(bid)
        return ids

    def _flash_board(self) -> None:
        """Put firmware on a board, then offer to set it up in the same sitting.

        Chained deliberately: a freshly flashed board is useless until it has the lab
        Wi-Fi, and making the student find the next menu item themselves is exactly the
        kind of gap that turns a five-minute task into a support question.
        """
        from .flash_dialog import FlashBoardDialog
        from PySide6.QtWidgets import QMessageBox
        dlg = FlashBoardDialog(self)
        if not dlg.exec():
            return
        self.ctx.log("GINI32: board flashed", "ok")
        if QMessageBox.question(
                self, "Set the board up now?",
                "The board has firmware but no lab Wi-Fi yet, so it cannot reach "
                "gBuilder.\n\nSet it up now?") == QMessageBox.StandardButton.Yes:
            self._setup_board()

    def _reset_board(self) -> None:
        """Release a board's pairing over USB — see ui/reset_dialog.py for why USB."""
        from .reset_dialog import ResetBoardDialog
        dlg = ResetBoardDialog(self)
        if dlg.exec():
            self.ctx.log("GINI32: board released — any gBuilder may claim it now", "ok")

    def _setup_board(self) -> None:
        from .board_dialog import BoardSetupDialog
        dlg = BoardSetupDialog(self, self.ctx.settings, self._known_board_ids())
        if dlg.exec() and getattr(dlg, "applied_id", ""):
            # The dialog put the lab Wi-Fi on Settings; persist it so the next board
            # is a single click.
            from ..app.paths import PERSISTED_KEYS, save_config
            s = self.ctx.settings
            # Remember the id so the canvas can offer it later. Without this the
            # student has to retype it from memory, and a typo yields a board that
            # is online and healthy yet invisible to the topology.
            known = [b for b in (getattr(s, "known_boards", None) or [])
                     if b != dlg.applied_id]
            s.known_boards = ([dlg.applied_id] + known)[:32]
            save_config({k: getattr(s, k) for k in PERSISTED_KEYS})
            self.ctx.log(f"GINI32: board '{dlg.applied_id}' set up for Wi-Fi "
                         f"'{s.board_wifi_ssid}'. Use that as the BoardID on the "
                         f"canvas.", "ok")

    def _show_boards(self) -> None:
        """What the relay currently knows about real boards."""
        from PySide6.QtWidgets import QMessageBox
        # The poller's cache rather than a fresh blocking GET: at most one 3s tick stale,
        # and a menu click should never freeze the window for two seconds.
        st = getattr(self, "_board_status_raw", None)
        if st is None:
            QMessageBox.information(
                self, "Boards",
                "No running lab to ask.\n\nBoard status comes from the gbridge relay, "
                "which runs while a topology containing a GINI32 element is up. Draw a "
                "board, press Run, then look here.\n\nTo set a board up over USB, use "
                "Hardware → Set Up a Board.")
            return
        boards = st.get("boards", [])
        if not boards:
            QMessageBox.information(self, "Boards",
                                    "The lab is running but no GINI32 boards are drawn "
                                    "on the canvas.")
            return
        lines = []
        for b in boards:
            where = b.get("addr") or "not seen yet"
            state = "online" if b.get("online") else "OFFLINE"
            lines.append(f"{b['board_id']}  —  {state}  ({where})\n"
                         f"    fabric {b.get('ip') or '?'}   mode {b.get('mode') or '?'}"
                         f"   clients {len(b.get('clients') or [])}")
        foreign = st.get("foreign") or []
        if foreign:
            lines.append("\nHeard, but claimed by another computer:")
            lines += [f"  {f['board_id']} (owner {f['owner']})" for f in foreign]
        QMessageBox.information(self, "Boards", "\n".join(lines))

    # Legacy single-file `.gini` import. Kept because older labs and shared files use it; the
    # matching _new/_save/_save_as have been removed (nothing called them since projects became
    # folders, and their separate `_project_path` tracker only invited confusion).
    def _open(self) -> None:
        from ..app.paths import projects_dir
        from ..services import PROJECT_EXT
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(projects_dir()),
            f"GINI project (*{PROJECT_EXT});;All files (*)")
        if path:
            self._load_from_path(path)

    def _save_to_path(self, path: str) -> None:
        from pathlib import Path
        from ..services import save_project
        self.ctx.topology.name = Path(path).stem
        save_project(self.ctx.topology, path)
        self._project_path = path
        self.setWindowTitle(f"gBuilder 6.0 — {Path(path).name}")
        self.ctx.log(f"Saved {path}", "ok")

    def _load_from_path(self, path: str) -> None:
        from pathlib import Path
        from ..services import load_project
        try:
            topo = load_project(path)
        except Exception as e:
            self.ctx.log(f"Open failed: {e}", "error")
            return
        self._project_path = path
        self._router_programs.clear()
        self._set_topology(topo)
        self.setWindowTitle(f"gBuilder 6.0 — {Path(path).name}")
        self.ctx.log(f"Opened {path}", "ok")

    def _set_topology(self, topo) -> None:
        scene = self.canvas.scene_
        scene.clear()
        scene.nodes.clear()
        scene.edges.clear()
        scene.groups.clear()
        scene._callouts = []
        scene._spotlit = []
        scene._highlit = []
        # A topology that arrives whole is an IMPORT, not a construction — and the loops below
        # replay device_added/link_added for every element in it. Tell the recorder BEFORE the
        # swap, or an imported .gini writes a perfect fake build.
        rec = getattr(self, "proof_recorder", None)
        if rec is not None:
            rec.note_load(getattr(topo, "name", "") or "an experiment", topo)
        self.ctx.topology = topo
        # A different network is a different history. Any HUD left open across the swap must
        # forget what it recorded, or its convergence timeline mixes the previous topology's
        # events with this one's and the scrub replays a network that is no longer on screen.
        # (The live view corrects itself on the next poll now that the HUDs read the device
        # dict through self.ctx, but recorded history has no such self-correction.)
        for _h in ("_rhud", "_fhud", "_mhud", "_oshud"):
            _hud = getattr(self, _h, None)
            if _hud is not None and hasattr(_hud, "reset"):
                try:
                    _hud.reset()
                except Exception:                       # a HUD reset must never block a load
                    pass
        topo.prefix_overrides = dict(self.ctx.settings.name_prefixes)   # apply naming prefs
        self.ctx.selected_id = None
        if hasattr(self, "_manual_addr_act"):
            self._manual_addr_act.setChecked(topo.manual_addressing)
        if hasattr(self, "_dynroute_act"):
            self._dynroute_act.setChecked(getattr(topo, "routing_mode", "static") == "dynamic")
        for d in topo.devices.values():
            self.ctx.bus.device_added.emit(d.id)
        for link in topo.links.values():
            self.ctx.bus.link_added.emit(link.id)
        self.ctx.bus.topology_changed.emit()     # -> _rebill re-estimates the new topology
        if hasattr(self, "dashboard"):           # fresh experiment -> fresh GINI $ meter
            self.dashboard.reset()
        self.ctx.bus.selection_changed.emit(None)

    def _export_png(self) -> None:
        from PySide6.QtCore import QRectF, QSize, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "topology.png", "PNG (*.png)")
        if not path:
            return
        scene = self.canvas.scene_
        rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        img = QImage(QSize(max(1, int(rect.width())), max(1, int(rect.height()))),
                     QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        scene.render(p, QRectF(img.rect()), rect)
        p.end()
        img.save(path)
        self.ctx.log(f"Exported {path}", "ok")

    def _compile(self):
        cfg = self._gloader.compile(self.ctx.topology)
        self.ctx.log(
            f"Compiled “{self.ctx.topology.name}” → {len(cfg.machines)} machines, "
            f"{len(cfg.switches)} switches, {len(cfg.routers)} routers, "
            f"{len(cfg.subnets)} subnets.", "ok")
        from ..services.compiler import validate
        for it in validate(self.ctx.topology):
            tag = "·" if it["level"] == "info" else "⚠"
            self.ctx.log(f"  {tag} {it['message']}",
                         "info" if it["level"] == "info" else "error")
        return cfg

    def _auto_layout(self) -> None:
        nodes = list(self.canvas.scene_.nodes.values())
        n = len(nodes)
        if n == 0:
            return
        cols = max(1, math.ceil(math.sqrt(n)))
        gx, gy = NODE_W + 60, NODE_H + 60
        for i, node in enumerate(nodes):
            r, c = divmod(i, cols)
            node.setPos(c * gx - (cols - 1) * gx / 2, r * gy - 80)
        self.ctx.log("Arranged topology.", "info")

    def _toggle_connect(self, on: bool) -> None:
        self.canvas.set_connect_mode(on)
        self.ctx.log("Connect mode: click two devices to link them." if on
                     else "Connect mode off.", "info")

    def _on_canvas_background(self) -> None:
        # a click on empty canvas ends connect mode (linking elements keeps it on;
        # clicking empty space is the natural "done" gesture). trigger() (not setChecked)
        # so the action's `triggered` handler actually turns the canvas mode off.
        if self._connect_act.isChecked():
            self._connect_act.trigger()           # -> _toggle_connect(False)

    def _toggle_edge_style(self, bent: bool) -> None:
        self.ctx.settings.connector_style = "orthogonal" if bent else "straight"
        self.ctx.bus.edges_restyled.emit()
        self._persist_settings()          # a toolbar toggle is still a preference — remember it
        self.ctx.log(f"Connectors: {'bent (rounded)' if bent else 'straight'}.", "info")

    def _toggle_manual_addr(self, on: bool) -> None:
        self.ctx.topology.manual_addressing = on
        self._recompute_addressing()      # re-derive with/without the manual overrides
        self._revalidate()
        self.ctx.log(
            "Manual addressing: on — set IPs in Inspector › Interfaces; blanks auto-fill."
            if on else "Manual addressing: off — IPs are auto-assigned.", "info")

    def _toggle_routing_mode(self, on: bool) -> None:
        """Static (default): the compiler pre-installs shortest-path routes at boot.
        Dynamic: routers get CONNECTED routes only — a routing protocol (a control-plane
        program, e.g. RIP in Lua) owns the table, so the two computations never fight."""
        self.ctx.topology.routing_mode = "dynamic" if on else "static"
        self._recompute_addressing()      # Routes tab reflects the mode immediately
        self.ctx.log(
            "Dynamic routing: on — routers boot with connected routes only; run a "
            "control-plane protocol (e.g. RIP) to build the rest. Watch it converge "
            "in the Network HUD."
            if on else
            "Dynamic routing: off — static routes are computed and pre-installed at boot.",
            "info")

    # -- remote (GINI server) backend -------------------------------------- #
    def _toggle_backend(self) -> None:
        """Toolbar toggle: connect to a remote Kata GINI server, or drop back to local."""
        if self._remote is not None:
            self._remote = None
            self.ctx.settings.backend = "local"
            self.ctx.log("Backend: local Docker.", "info")
            self._server_act.setChecked(False)
            return
        if self._running:
            self.ctx.log("Stop the running lab before switching backend.", "info")
            self._server_act.setChecked(False)
            return
        self._connect_server()
        self._server_act.setChecked(self._remote is not None)

    def _connect_server(self, client=None) -> bool:
        """Log in to the configured GINI server (host/port/user from Settings; password is
        prompted, never stored). `client` is injectable for tests."""
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        s = self.ctx.settings
        if client is None and not s.gini_server_host:
            self.ctx.log("Set the GINI server host in Settings → Backend first.", "info")
            return False
        if client is None:
            pw, ok = QInputDialog.getText(self, "Connect to GINI server",
                                          f"Password for {s.gini_server_user}@{s.gini_server_host}:",
                                          QLineEdit.Password)
            if not ok:
                return False
            from ..services.remote import RemoteClient
            client = RemoteClient(f"http://{s.gini_server_host}:{s.gini_server_port}")
            good, err = client.login(s.gini_server_user, pw)
            if not good:
                self.ctx.log(f"Server login failed: {err}", "error")
                return False
        self._remote = client
        s.backend = "gini-server"
        kata = client.kata_available()
        self.ctx.log(f"Connected to GINI server{' (Kata available)' if kata else ''}. "
                     "Build a topology and Run — it executes on the server.", "ok")
        return True

    def _run_remote(self) -> None:
        if self._running:
            self.ctx.log("Already running — stop first.", "info")
            return
        import threading
        topo = self.ctx.topology
        self.ctx.log("Sending topology to the GINI server…", "info")
        self.run_button.set_state("booting")

        def worker():
            ok, msg = self._remote.run(topo)              # start (returns once accepted)
            if not ok:
                self.ctx.bus.run_state.emit(False, msg)
                return
            self.ctx.log("Launching on the server (pulling images / booting)…", "info")
            ok, msg = self._remote.wait_until_running()   # poll until up / error
            self.ctx.bus.run_state.emit(ok, msg)
        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_run_state(self, ok: bool, msg: str) -> None:
        if ok:
            self._running = True
            self._stopping = False
            self._set_runtime_status("running")
            self.run_button.set_state("running")     # remote has no container poller
            self.canvas.scene_.running = True
            self.inspector.set_live_running(True)
            self.ctx.log("Topology running on the GINI server.", "ok")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2500, self._poll_remote_metrics)
        else:
            self._running = False
            self.run_button.set_state("error")
            self.ctx.log(f"Server run failed: {msg}", "error")
            self._set_runtime_status("idle")

    def _poll_remote_metrics(self) -> None:
        if not self._running or self._remote is None:
            return
        import threading

        def work():
            m = self._remote.metrics() or {}
            startup = m.get("startup") or {}
            if startup:
                line = ", ".join(f"{s} {ms:.0f} ms" for s, ms in sorted(startup.items()))
                self.ctx.log("Startup times — " + line, "info")
        threading.Thread(target=work, daemon=True).start()

    def _toggle_run(self) -> None:
        """The circular power button was pressed — what it means depends on state."""
        st = self.run_button.state()
        if st in ("ready", "error"):
            self._run()
        elif st in ("booting", "running"):
            self._stop()
        # "stopping": already winding down — ignore

    def _run(self) -> None:
        import tempfile
        import threading
        if self._remote is not None:           # remote backend: the server runs it
            self._run_remote()
            return
        if self._running:
            self.ctx.log("Already running — stop first.", "info")
            return
        if self._launching:
            # A second launch during the boot window would replace _workdir and orphan the compose
            # project already coming up. The run button's state machine hides this (it offers Stop
            # while "booting"), but _run is reachable from the menu too.
            self.ctx.log("Still starting — wait for the current launch to finish.", "info")
            return
        cfg = self._compile()
        runnable = (cfg.machines or cfg.routers or cfg.ovs_switches
                    or cfg.controllers or cfg.services or cfg.k8s or cfg.faas)
        if not runnable:
            self.ctx.log("Nothing runnable on the canvas yet (add devices + links).", "info")
            return
        # Kata Instances need a 'kata' OCI runtime on the backend — fail with a clear
        # message instead of a raw Docker error if it's not there (e.g. on a Mac).
        if any(getattr(s, "runtime", "") == "kata" for s in cfg.services) \
                and not self._gloader.runtime_available("kata"):
            self.ctx.log("This topology has Kata Instance(s), but the current backend has no "
                         "'kata' runtime. Point GINI at a Kata-enabled Linux backend "
                         "(Settings → Backend) to run VM-isolated workloads.", "info")
            return
        self._last_services = list(cfg.services)
        self._last_machines = list(cfg.machines)     # headful Desktops carry a noVNC port here
        self._last_k8s = list(cfg.k8s)
        self._last_gbridge = list(getattr(cfg, "gbridge", []))   # real GINI32 boards
        self._board_state = {}          # board_id -> live state, refreshed by the poller
        self._board_status_raw = None   # last raw relay snapshot (read by the Inspector)
        self._boards_busy = False       # a board_status fetch is in flight
        # What each controller is ACTUALLY running, so a later property edit can tell an
        # App change from any other edit. Without this seed, the first device_changed on
        # a controller (a rename, say) would look like a new app and bounce it.
        from ..services.compiler import _svc as _svc_name
        self._live_ctrl_app = {_svc_name(c.name): c.app for c in cfg.controllers}
        self._workdir = tempfile.mkdtemp(prefix="gini-lab-")
        self.ctx.log(f"Launching {len(cfg.machines)} machines + {len(cfg.routers)} "
                     f"gRouters + {len(cfg.services)} cloud services via Docker…", "info")
        self.ctx.log(f"Project: {self._workdir}  (double-click a device to log in)", "info")

        auto_internet = self.ctx.settings.auto_internet
        if not auto_internet:
            self.ctx.log("Faithful mode: hosts have NO default route to the internet. "
                         "Draw + wire an Internet element for egress. (Web consoles "
                         "still open — only outbound internet is cut.)", "info")

        self.run_button.set_state("booting")        # ring fills as containers come up

        self._launching = True          # from HERE, `docker compose up` may be making containers

        def worker(workdir=self._workdir, ai=auto_internet, lid=self._laptop_id()):
            # `up` writes files and shells out, so it can raise — and an exception here used to
            # kill this thread with run_state never emitted, leaving the button stuck on "booting"
            # for ever with no error anywhere the user looks. Always report something.
            try:
                ok, msg = self._gloader.up(cfg, workdir, auto_internet=ai, laptop_id=lid)
            except Exception as e:                      # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {e}"
            self.ctx.bus.run_state.emit(ok, msg)
        threading.Thread(target=worker, daemon=True).start()

    def _on_run_state(self, ok: bool, msg: str) -> None:
        self._launching = False            # settled, one way or the other
        if self._remote is not None:           # remote backend has its own (lighter) handling
            self._on_remote_run_state(ok, msg)
            return
        if ok:
            self._running = True
            self._stopping = False
            self._set_runtime_status("running")
            self._poll.start()                  # reconcile with real container state
            self.ctx.log("Topology running on Docker.", "ok")
            # GINI32: say out loud whether real boards can find this lab, and on which
            # address. Discovery failing silently is indistinguishable from a board
            # being broken, and it sends people debugging the wrong end of the link.
            if getattr(self, "_last_gbridge", None):
                where = getattr(self._gloader.orchestrator, "advertising", None)
                boards = ", ".join(b.board_id for b in self._last_gbridge)
                if where:
                    self.ctx.log(f"GINI32: announcing this lab at {where} — boards "
                                 f"expected: {boards}", "ok")
                    self.ctx.log("GINI32: a board must be on the SAME Wi-Fi as this Mac; "
                                 "if it never finds the lab, use `set server "
                                 f"{where.split(':')[0]}` on the board.", "info")
                else:
                    why = getattr(self._gloader.orchestrator, "advertise_error", "")
                    self.ctx.log("GINI32: could NOT announce on the network — boards will "
                                 "not discover this lab. Set the address by hand on the "
                                 "board: `set server <this Mac's IP>`."
                                 + (f"  ({why})" if why else ""), "warn")
            self._wire_xv6_providers()          # attach live GDB bridges to any xv6 kernels
            self._populate_overlay_hosts()      # names resolve over gini0, not the Docker bridge
            for s in getattr(self, "_last_services", []):   # surface web consoles
                for p in s.ports:
                    if p.get("web"):
                        url = f"http://localhost:{p['host']}{p.get('path', '')}"
                        self.ctx.log(f"{s.name} ({p['label']}): {url}", "ok")
            # start the GINI $ meter billing the launched topology
            from ..domain.pricing import bill
            rate = bill(self.ctx.topology, self.ctx.settings.prices)["rate_per_hr"]
            self.dashboard.start(rate)
            self.inspector.set_live_running(True)       # enable the Live metrics plots
            self.canvas.scene_.running = True           # enable console/logs/login actions
            self._fabric_poll.start()                   # poll cloud-fabric app metrics
            self._memwatch.clear()                      # fresh memory history per run
            self._mem_warned.clear()
            self._mem_poll.start()                      # fleet memory gauge + runaway watch
            self._preflight_vm_memory()                 # warn early if the VM looks small
            from PySide6.QtCore import QTimer
            QTimer.singleShot(6000, self._drive_loadgens)   # let Fortio boot, then load
            QTimer.singleShot(2000, self._log_startup_times)  # VM-vs-container startup signal
            if getattr(self, "_last_k8s", None):
                self._apply_k8s()                           # wait for k3s, kubectl apply
                self._k8s_poll.start()                      # poll deployment metrics
        else:
            self.run_button.set_state("error")
            self.ctx.log(f"Run failed: {msg}", "error")
            # `compose up` can bring half a topology up and still fail, so containers may be out
            # there. Mark it, and start the reconciler — that is what clears the mark once they
            # are gone, and what keeps Stop available meanwhile.
            self._orphaned = True
            self._poll.start()
            self.ctx.log("Some containers from that launch may still be running — press Stop to "
                         "clean up.", "warn")
        self._update_status()

    def _stop(self) -> None:
        import threading
        if not (self._running or self._orphaned):
            self.ctx.log("Not running.", "info")
            return
        self._stopping = True
        self._set_runtime_status("stopping")    # yellow while containers wind down
        self.run_button.set_state("stopping")
        self.ctx.log("Stopping…", "info")
        self._update_status()

        def worker():
            self.ctx.stop_all_riders()             # kill rider processes while the stack is still up
            ok, msg = self._remote.stop() if self._remote is not None else self._gloader.down()
            if not ok:
                self.ctx.log(f"Stop issue: {msg}", "error")
            self.ctx.bus.runtime_status.emit({})   # force a final reconcile -> idle
        threading.Thread(target=worker, daemon=True).start()

    # -- real status reconciliation ----------------------------------------- #
    def _poll_status(self) -> None:
        import threading
        if not self._workdir:
            return
        wd = self._workdir

        def worker():
            self.ctx.bus.runtime_status.emit(self._gloader.status(wd))
        threading.Thread(target=worker, daemon=True).start()

    def _on_runtime_status(self, states) -> None:
        from ..services.compiler import _role, _svc
        any_up = any(v == "running" for v in (states or {}).values())

        # finished stopping, or everything died externally
        if not any_up:
            if self._running or self._stopping or self._orphaned:
                was_live = self._running or self._stopping
                self._running = False
                self._stopping = False
                self._orphaned = False          # nothing of ours is left out there
                self._poll.stop()
                self._set_runtime_status("idle")
                self.dashboard.stop()           # freeze the session's GINI $ bill
                self._mem_poll.stop()
                self.dashboard.set_memory(None, None)
                # Devices on a board's radio are facts about a RUNNING lab. With the
                # lab down we no longer know anything, so stop claiming we do.
                self.canvas.clear_live_clients()
                self._board_state = {}
                self._board_status_raw = None
                self._fabric_poll.stop()
                self._k8s_poll.stop()
                self.dashboard.set_fabric({})
                self.inspector.set_live_running(False)   # stop the Live metrics polling
                self.canvas.scene_.running = False        # grey out console/logs/login actions
                self.inspector.set_fabric_snapshot({})
                self.inspector.set_k8s_snapshot({})
                self.run_button.set_state("ready")
                self.ctx.log("All containers stopped." if was_live
                             else "Nothing from that launch is still running.", "info")
                self._update_status()
            return
        if self._stopping:
            return   # keep the yellow 'stopping' chips until containers are actually gone

        # feed the boot ring real progress, and morph ▶→■ once everything is up
        up = sum(1 for v in states.values() if v == "running")
        total = len(states)
        self.run_button.set_progress(up, total)
        self.run_button.set_state("running" if total and up >= total else "booting")

        fabric = states.get("fabric")
        for node in self.canvas.scene_.nodes.values():
            role = _role(node.inst.type_key)
            if role == "machine":
                st = states.get(_svc(node.inst.name))
                node.set_status("running" if st == "running"
                                else "error" if st else "idle")
            elif role in ("router", "ovs", "controller", "service", "compute", "k8scluster",
                          "xv6", "oszoo"):
                st = states.get(_svc(node.inst.name))            # each its own container
                # OS Zoo card tracks its emulator: the container stays up while QEMU/DOSBox/
                # Basilisk runs, so "running" here means the guest is actually up.
                node.set_status("running" if st == "running"
                                else "error" if st else "idle")
            elif role in ("k8sworkload", "hpa", "k8snode"):      # live inside the cluster
                node.set_status("running" if fabric is not None or any(
                    v == "running" for v in states.values()) else "idle")
            elif role == "function":                # functions live in the shared faas runtime
                f = states.get("faas")
                node.set_status("running" if f == "running"
                                else "error" if f else "idle")
            elif role == "switch":                  # switches live in the fabric container
                node.set_status("running" if fabric == "running"
                                else "error" if fabric else "idle")
            elif role == "peripheral":              # terminal / disk: mirror the wired xv6 Machine
                xid = self._xv6_for_peripheral(node.inst.id)
                st = states.get(_svc(self.ctx.topology.devices[xid].name)) if xid else None
                node.set_status("running" if st == "running" else "idle")
            elif role == "rider":                   # Source/Sink: 'ready' when its donor is up
                donor = self.ctx.topology.donor_of(node.inst.id)
                if donor is None:
                    node.set_status("idle")
                else:
                    dst = fabric if _role(donor.type_key) == "switch" \
                        else states.get(_svc(donor.name))
                    node.set_status("ready" if dst == "running"
                                    else "error" if dst else "idle")
            elif role == "gini32":
                # A board has no container: it is real hardware, so the only thing that
                # means "connected" is that it actually checked in. Distinguish "the lab
                # is up and we are waiting for hardware" from "nothing is running" —
                # they look identical otherwise, and only one of them is a problem.
                bid = str((node.inst.properties or {}).get("BoardID", "")).strip()
                st = (getattr(self, "_board_state", None) or {}).get(bid)
                if st and st.get("online"):
                    node.set_status("running")            # -> chip reads "connected"
                elif not bid:
                    node.set_status("error")              # -> "no board": nothing can attach
                else:
                    node.set_status("searching")

        self._poll_boards()

    def _laptop_id(self) -> str:
        """This install's identity to a board, minted once and then stable.

        A board records the id of the laptop that claimed it and ignores every other
        laptop — which is what stops thirty students in one room from taking each
        other's hardware. It must therefore survive restarts.
        """
        lid = getattr(self.ctx.settings, "laptop_id", "") or ""
        if not lid:
            import uuid
            lid = f"gb-{uuid.uuid4().hex[:12]}"
            self.ctx.settings.laptop_id = lid
            self._persist_settings()
        return lid

    def _persist_settings(self) -> None:
        from ..app.paths import PERSISTED_KEYS, save_config
        save_config({k: getattr(self.ctx.settings, k) for k in PERSISTED_KEYS})

    def board_action(self, action: str, board: str) -> bool:
        """Claim / release / blink a board, and remember what we own."""
        ok = self._gloader.orchestrator.board_action(action, board)
        if not ok:
            return False
        claimed = dict(getattr(self.ctx.settings, "claimed_boards", None) or {})
        if action == "claim":
            claimed[board] = {"claimed_at": time.time()}
            self.ctx.log(f"GINI32: claimed {board} — it now ignores other laptops", "ok")
        elif action == "release":
            claimed.pop(board, None)
            self.ctx.log(f"GINI32: released {board} — anyone may claim it now", "info")
        else:
            self.ctx.log(f"GINI32: {board} should be blinking — look at the bench", "info")
        if action in ("claim", "release"):
            self.ctx.settings.claimed_boards = claimed
            self._persist_settings()
        return True

    @staticmethod
    def _ap_gateway(subnet: str) -> str:
        """The board's own address on its hotspot: the .1 of the physical subnet.

        The firmware takes .1 and DHCPs the rest (gb_ap_configure), so this is derived
        rather than reported — one fact, one place. Returns "" for anything unparsable,
        because a wrong address on the canvas is worse than none.
        """
        head = (subnet or "").split("/")[0].strip()
        parts = head.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return ""
        return ".".join(parts[:3] + ["1"])

    def _poll_boards(self) -> None:
        """Ask the relay for board state, OFF the GUI thread.

        `board_status()` is an HTTP GET with a 2-SECOND timeout, and this runs from the
        3-second status poll. Called inline it froze the whole app for two seconds out of
        every three whenever the relay was slow to answer -- which is exactly when a
        student is debugging a board and least wants a frozen window. The failure was
        swallowed (`return None`), so it never looked like an error, only like jank.
        """
        if not getattr(self, "_last_gbridge", None):
            return
        if getattr(self, "_boards_busy", False):
            return                              # last fetch still out; skip rather than pile up
        self._boards_busy = True
        import threading

        def worker():
            st = None
            try:
                st = self._gloader.orchestrator.board_status()
            finally:
                # Emit even on failure so the busy flag is always cleared on the GUI thread.
                self.ctx.bus.board_status_ready.emit(st)
        threading.Thread(target=worker, daemon=True).start()

    def _on_board_status(self, st) -> None:
        """Apply a relay snapshot. Runs on the GUI thread via a queued signal.

        Devices are OBSERVED, not drawn: a phone joining a board's hotspot is a fact
        about the physical world, so it appears as an ephemeral node and disappears
        when it leaves. It is never written into the saved topology.
        """
        self._boards_busy = False
        if st is None:
            return
        # Cache the raw snapshot so the Inspector can render board panels without making
        # its own blocking call -- it used to fire TWO per rebuild (see board_status_fn).
        self._board_status_raw = st
        boards = {b["board_id"]: b for b in st.get("boards", [])}
        was_online = {k: bool(v.get("online"))
                      for k, v in (getattr(self, "_board_state", None) or {}).items()}
        self._board_state = boards

        # element id -> the devices its board currently carries
        live: dict[str, list[dict]] = {}
        for node in self.canvas.scene_.nodes.values():
            if node.inst.type_key != "gini32":
                continue
            bid = str((node.inst.properties or {}).get("BoardID", "")).strip()
            b = boards.get(bid)
            if not b:
                node.set_board_addr("")
                continue
            # Channel is reported by the hardware, never set here (APSTA forces it).
            ch = b.get("channel") or 0
            shown = f"{ch} (from '{b.get('uplink') or '?'}')" if ch else ""
            if (node.inst.properties or {}).get("Channel") != shown:
                node.inst.properties["Channel"] = shown
            if b.get("online"):
                live[node.inst.id] = list(b.get("clients") or [])
                # The hotspot's own address, so the node says WHERE the board is and not
                # merely that it exists — every other element on the canvas shows its
                # address, and a board was the one thing you had to go and look up.
                # Cleared below the moment it goes quiet: a stale address on a board that
                # is not there is worse than no address, because it looks reachable.
                node.set_board_addr(self._ap_gateway(b.get("physical_subnet") or ""))
            else:
                node.set_board_addr("")

        # Tell the Inspector, which otherwise keeps showing a board that has gone away —
        # it reads board state once, when the panel is built.
        now_online = {k: bool(v.get("online")) for k, v in boards.items()}
        if now_online != was_online:
            self.ctx.bus.boards_changed.emit()

        changed = self.canvas.set_live_clients(live)
        if changed:
            for gone in changed.get("left", []):
                self.ctx.log(f"GINI32: device {gone} left the radio", "info")
            for came in changed.get("joined", []):
                self.ctx.log(f"GINI32: device {came} joined the radio", "ok")

    def _set_runtime_status(self, status: str) -> None:
        from ..services.compiler import _role
        for node in self.canvas.scene_.nodes.values():
            if _role(node.inst.type_key) in ("machine", "switch", "router", "ovs",
                                             "controller", "service", "compute", "function",
                                             "k8scluster", "k8sworkload", "hpa", "k8snode",
                                             "xv6", "oszoo", "peripheral"):
                node.set_status(status)

    def _do_recompute(self) -> None:
        # one coalesced pass after a burst of topology changes (debounced) — keeps the three
        # compiler-backed refreshes off the per-change hot path.
        self._recompute_addressing()
        self._revalidate()
        self._rebill()

    def _populate_overlay_hosts(self) -> None:
        """Write peer name→overlay-IP lines into each machine's /etc/hosts, so name resolution
        (getent/DNS Probe) and ping/reach ride the DRAWN gini0 network instead of Docker's bridge.
        Off the GUI thread — it's a docker exec per machine. Containers are fresh each run, so no
        accumulation. This is the 'small Phase-2.x': it makes reachability follow the topology.

        EVERY address is written, and the block is built per machine. A router has an address on
        each subnet it joins; writing one line per device left the others nameless — including the
        default gateway of every segment but the first — and made `R1` resolve, everywhere, to
        whichever interface happened to be numbered eth0. Now each machine's `R1` is the router's
        address on that machine's own segment."""
        import subprocess
        import threading
        from ..services.compiler import _role, _svc, overlay_host_lines
        orch = getattr(self.ctx, "orchestrator", None)
        if orch is None:
            return
        # host_lines, not overlay_hosts: a router has an address on every subnet it joins, and
        # one line per device left the rest of them — the default gateway of each other subnet —
        # with no name at all.
        addressing = getattr(self.ctx, "addressing", {}) or {}
        if len(overlay_host_lines(addressing)) < 2:
            return
        dc = list(getattr(orch, "_dc", ["docker", "compose"]))
        wd = getattr(orch, "workdir", None)
        devs = [d for d in self.ctx.topology.devices.values()
                if _role(d.type_key) in ("machine", "router", "compute")]

        # include SELF, and PREPEND — Docker puts the container's own name→bridge line at the TOP of
        # /etc/hosts, so a getent/ping for the machine's own name would hit the bridge unless our
        # overlay line comes first. `> /etc/hosts` truncates-in-place (works on the bind mount; `mv`
        # wouldn't). Docker's original lines stay below, so nothing that needs the bridge id breaks.
        # Build the block as printf ESCAPES (literal \t and \n two-char sequences), never real
        # tabs/newlines: the payload stays one flat single-line argv element, which survives
        # Windows' CreateProcess command-line round-trip. printf expands the escapes inside the
        # container. Deliberately not base64 — busybox (the lean Alpine tier) may be built without
        # the base64 applet, and a missing applet would break this on every platform.
        # PER MACHINE, not one shared block: which address a multi-homed name resolves to depends
        # on who is asking. A host answers `R1` with the router's address on ITS OWN segment, the
        # way a real network does, instead of whichever interface happened to be numbered first.
        def script_for(viewer: str) -> str:
            esc = "".join(f"{ip}\\t{' '.join(names)}\\n"
                          for ip, names in overlay_host_lines(addressing, viewer=viewer))
            return (f"{{ printf '{esc}'; cat /etc/hosts; }} > /tmp/gini_hosts && "
                    "cat /tmp/gini_hosts > /etc/hosts")

        # Built HERE, on the GUI thread, not inside work(). `addressing` is GUI-thread data that
        # _recompute_addressing replaces from under us, and the worker has no business reading it —
        # only finished strings cross the boundary. Per-machine blocks made it tempting to build
        # them where they are used; that is exactly how a worker ends up walking live state.
        scripts = {dev.name: script_for(dev.name) for dev in devs}

        def work():
            import time
            failed = []
            for dev in devs:
                cmd = [*dc, "exec", "-T", _svc(dev.name), "sh", "-lc", scripts[dev.name]]
                err = ""
                # Retry: this fires right after `up` returns, and a container may not be accepting
                # execs yet — native Linux docker returns from `up` far sooner than Docker Desktop,
                # so the race is platform-sensitive. The write is idempotent, so retrying is safe.
                for attempt in range(4):
                    try:
                        r = subprocess.run(cmd, cwd=str(wd) if wd else None,
                                           capture_output=True, timeout=8)
                        if r.returncode == 0:
                            err = ""
                            break
                        err = (r.stderr or b"").decode(errors="replace").strip()[:120]
                    except Exception as e:       # noqa: BLE001 — best-effort
                        err = str(e)[:120]
                    time.sleep(0.75 * (attempt + 1))
                if err:
                    failed.append(f"{dev.name}: {err}")
            if failed:
                # Don't fail silently: without these lines, names resolve to the Docker bridge and
                # experiments see 172.x addresses instead of the topology's 10/8 ones.
                self.ctx.log("Name resolution over the drawn network could not be set up on: "
                             + ", ".join(failed[:4])
                             + (" …" if len(failed) > 4 else ""), "warn")
        threading.Thread(target=work, daemon=True).start()

    def _recompute_addressing(self) -> None:
        from ..services.compiler import address_map
        try:
            self.ctx.addressing = address_map(self.ctx.topology)
        except Exception:
            self.ctx.addressing = {}
        self.ctx.bus.addressing_changed.emit()

    def _rebill(self) -> None:
        """Refresh the dashboard's projected GINI $/hr from the current canvas. While a
        lab runs the meter holds the launched rate, so we only re-estimate when idle."""
        if not hasattr(self, "dashboard") or self._running:
            return
        from ..domain.pricing import bill
        self.dashboard.set_estimate(bill(self.ctx.topology, self.ctx.settings.prices))

    def _on_device_changed_live(self, device_id: str) -> None:
        """A property changed while running — re-drive a Load Generator if its QPS/target
        changed (the live throttle)."""
        if not self._running:
            return
        d = self.ctx.topology.devices.get(device_id)
        if d is None:
            return
        from ..services.compiler import _role, _svc
        if d.type_key == "load_generator":
            self._drive_loadgen(device_id)
        elif _role(d.type_key) == "k8sworkload":          # Replicas slider -> kubectl scale
            self._k8s_live(d, scale=True)
        elif _role(d.type_key) == "hpa":                  # Target CPU slider -> patch HPA
            self._k8s_live(d, scale=False)
        elif d.type_key == "controller":                  # App changed -> bounce just POX
            self._controller_app_live(d)

    # -- Network HUD glue: the SDN facts the routing model cannot derive ------------------- #
    def _ovs_controller(self, did: str):
        """The controller device linked to this OVS, or None.

        Mirrors the compiler's rule (compiler.py:732): only an OVS↔controller link is a real
        association, and it drives the dashed control-plane overlay — never a forwarding edge.
        """
        # .values(): `links` is a dict, and its insertion order is the same order the
        # compiler sees (compiler.py:709 iterates topo.links.values()) — which is what makes
        # the port numbering below reproducible.
        for l in self.ctx.topology.links.values():
            for a, b in ((l.source_id, l.target_id), (l.target_id, l.source_id)):
                if a == did:
                    d = self.ctx.topology.devices.get(b)
                    if d is not None and d.type_key == "controller":
                        return b
        return None

    def _ovs_link_peers(self, did: str) -> list:
        """This OVS's neighbours in the order its links were COMPILED — which is what fixes
        the OpenFlow port numbers (see routing_model.ovs_port_peers).

        Control links are excluded because the compiler drops them from `kept`
        (compiler.py:726-733) before numbering ports; counting one here would shift every
        port by one and point every L2 hop at the wrong neighbour. The mapping is checked
        against each port's MAC afterwards, so a divergence drops the port instead of
        drawing a false edge.
        """
        out = []
        for l in self.ctx.topology.links.values():
            if getattr(l, "kind", "link") == "attach":
                continue
            peer = (l.target_id if l.source_id == did
                    else l.source_id if l.target_id == did else None)
            if peer is None:
                continue
            d = self.ctx.topology.devices.get(peer)
            if d is not None and d.type_key == "controller":
                continue
            out.append(peer)
        return out

    def _hud_mac_of(self) -> dict:
        """{device_id: [mac, …]} from the compiled address map, so an L3 hop can ask a switch
        whether its destination is programmed. A router is multi-homed, hence a LIST: the
        frame is addressed to whichever of its MACs faces the shared segment."""
        addr = getattr(self.ctx, "addressing", None) or {}
        by_name = {d.name: d.id for d in self.ctx.topology.devices.values()}
        out: dict = {}
        for name, info in addr.items():
            did = by_name.get(name)
            if did is None:
                continue
            macs = [i.get("mac") for i in (info.get("interfaces") or []) if i.get("mac")]
            if macs:
                out[did] = macs
        return out

    def _hud_ip_of(self) -> dict:
        """{device_id: [ip, ...]} for EVERY device, machines included.

        Hosts are not nodes on the HUD, but the model has to know which subnet one sits in:
        the switch that delivers a subnet is identified as the one wired both to the
        delivering router and to something addressed inside that subnet. Without this the
        last hop of a host-to-host path -- the OVS the traffic actually crosses -- cannot
        be worked out at all.
        """
        addr = getattr(self.ctx, "addressing", None) or {}
        by_name = {d.name: d.id for d in self.ctx.topology.devices.values()}
        out: dict = {}
        for name, info in addr.items():
            did = by_name.get(name)
            if did is None:
                continue
            ips = []
            for i in (info.get("interfaces") or []):
                cidr = i.get("ip")
                if cidr:
                    ips.append(str(cidr).split("/")[0])
            if ips:
                out[did] = ips
        return out

    def _controller_app_live(self, d) -> None:
        """The App property changed on a running controller: restart THAT container.

        POX has no hot-reload, so a new app needs a new process — but not a new topology.
        Comparing against the last applied value matters because device_changed fires for
        every property (renaming the controller must not bounce it).
        """
        import threading
        from ..services.compiler import _svc
        app = (d.properties.get("App") or "").strip()
        if not app:
            return
        svc = _svc(d.name)
        if getattr(self, "_live_ctrl_app", None) is None:
            self._live_ctrl_app = {}
        if self._live_ctrl_app.get(svc) == app:
            return                                        # some other property changed
        self._live_ctrl_app[svc] = app
        self.ctx.log(f"{d.name}: switching controller app to '{app}' — restarting the "
                     f"controller (switches will reconnect).", "info")

        def work():
            ok, msg = self._gloader.set_controller_app(svc, app)
            self.ctx.log(f"{d.name}: now running '{app}'. Give discovery a few seconds "
                         f"before testing." if ok
                         else f"{d.name}: could not switch app ({msg}) — stop and Run "
                              f"to apply it.", "ok" if ok else "info")
        threading.Thread(target=work, daemon=True).start()

    def _k8s_live(self, d, *, scale: bool) -> None:
        import threading
        from ..services.compiler import _role, _svc
        cluster = self._k8s_cluster_svc(d.id)
        if not cluster:
            return
        if scale:
            dep, n = _svc(d.name), d.properties.get("Replicas", "2")

            def work_scale():
                ok, msg = self._gloader.k8s_scale(cluster, dep, n)
                self.ctx.log(f"{d.name}: scaled to {n} replicas." if ok
                             else f"{d.name}: scale failed ({msg})", "ok" if ok else "info")
            threading.Thread(target=work_scale, daemon=True).start()
        else:
            # the HPA name == the connected Pod's deployment name
            hpa = None
            for l in self.ctx.topology.links.values():
                other = (l.target_id if l.source_id == d.id else
                         l.source_id if l.target_id == d.id else None)
                od = self.ctx.topology.devices.get(other) if other else None
                if od and _role(od.type_key) == "k8sworkload":
                    hpa = _svc(od.name); break
            if not hpa:
                return
            tgt = d.properties.get("TargetCPU", "60")

            def work_patch():
                ok, msg = self._gloader.k8s_set_hpa(cluster, hpa, target=tgt,
                                                    mn=d.properties.get("Min"),
                                                    mx=d.properties.get("Max"))
                self.ctx.log(f"{d.name}: HPA target {tgt}% applied." if ok
                             else f"{d.name}: HPA patch failed ({msg})", "ok" if ok else "info")
            threading.Thread(target=work_patch, daemon=True).start()

    def _loadgen_target(self, did: str) -> str | None:
        """The full URL a Load Generator should hit, from what it's wired to:
          * a Function          -> http://faas:8000/<fn>           (direct invoke)
          * an API Gateway      -> http://<gw>/<fn>                (through the front door)
          * proxy/LB/web/service -> http://<name>/                 (the existing HTTP path)
        Returns None if it isn't connected to anything drivable."""
        from ..services.compiler import _role, _svc
        topo = self.ctx.topology
        for l in topo.links.values():
            other = (l.target_id if l.source_id == did else
                     l.source_id if l.target_id == did else None)
            if not other or other not in topo.devices:
                continue
            d = topo.devices[other]
            tk = d.type_key
            if tk == "function":
                return f"http://faas:8000/{_svc(d.name)}"
            if tk == "api_gateway":
                fn = self._gateway_function(other)
                return f"http://{_svc(d.name)}/{fn}" if fn else None
            if tk in ("proxy", "load_balancer", "web_app") or \
                    _role(tk) in ("service", "compute"):
                return f"http://{_svc(d.name)}/"
        return None

    def _gateway_function(self, gw_did: str) -> str | None:
        """A function service name routed by this API Gateway (the first one connected)."""
        from ..services.compiler import _svc
        topo = self.ctx.topology
        for l in topo.links.values():
            other = (l.target_id if l.source_id == gw_did else
                     l.source_id if l.target_id == gw_did else None)
            if other and other in topo.devices and topo.devices[other].type_key == "function":
                return _svc(topo.devices[other].name)
        return None

    def _loadgen_hostport(self, name: str) -> int | None:
        from ..services.compiler import _svc
        for s in getattr(self, "_last_services", []):
            if _svc(s.name) == _svc(name):
                for p in s.ports:
                    if p.get("web"):
                        return p["host"]
        return None

    def _drive_loadgen(self, did: str) -> None:
        """Drive a single Load Generator at its QPS against its connected target."""
        if not self._running:
            return
        import threading
        d = self.ctx.topology.devices.get(did)
        if d is None or d.type_key != "load_generator":
            return
        hp = self._loadgen_hostport(d.name)
        url = self._loadgen_target(did)
        if not hp or not url:
            if self._running and d.type_key == "load_generator":
                self.ctx.log(f"{d.name}: connect it to a Function, API Gateway, Web App "
                             f"or Proxy to send load.", "info")
            return
        qps = d.properties.get("QPS", "100")
        conns = d.properties.get("Connections", "8")
        name = d.name
        try:
            off = float(qps) <= 0          # Fortio qps=0 means UNLIMITED — treat 0 as "off"
        except ValueError:
            off = False

        def work():
            if off:
                self._gloader.stop_load(hp)
                self.ctx.log(f"{name}: load paused (rate 0).", "info")
                return
            ok, msg = self._gloader.drive_load(hp, url, qps, conns)
            self.ctx.log(f"{name}: {msg}" if ok else f"{name}: load failed ({msg})",
                         "ok" if ok else "info")
        threading.Thread(target=work, daemon=True).start()

    def _drive_loadgens(self) -> None:
        for d in list(self.ctx.topology.devices.values()):
            if d.type_key == "load_generator":
                self._drive_loadgen(d.id)

    def _poll_k8s(self) -> None:
        if not self._running or not getattr(self, "_last_k8s", None):
            return
        import threading
        clusters = list(self._last_k8s)

        def work():
            merged = {"deployments": {}, "pods": 0}
            for k in clusters:
                m = self._gloader.k8s_metrics(k.svc)
                merged["deployments"].update(m.get("deployments", {}))
                merged["pods"] += m.get("pods", 0)
            self.ctx.bus.k8s_metrics.emit(merged)
        threading.Thread(target=work, daemon=True).start()

    def _on_k8s_metrics(self, snap) -> None:
        self.inspector.set_k8s_snapshot(snap)

    def _k8s_cluster_svc(self, did: str) -> str | None:
        """The k3s cluster service a K8s element belongs to (its connected cluster, or the
        cluster of its connected Pod)."""
        from ..services.compiler import _role, _svc
        topo = self.ctx.topology
        seen, frontier = set(), [did]
        while frontier:                      # 2-hop walk: element -> [pod] -> cluster
            cur = frontier.pop()
            for l in topo.links.values():
                other = (l.target_id if l.source_id == cur else
                         l.source_id if l.target_id == cur else None)
                od = topo.devices.get(other) if other else None
                if not od or other in seen:
                    continue
                seen.add(other)
                if _role(od.type_key) == "k8scluster":
                    return _svc(od.name)
                if _role(od.type_key) == "k8sworkload":
                    frontier.append(other)
        return self._last_k8s[0].svc if getattr(self, "_last_k8s", None) else None

    def _apply_k8s(self) -> None:
        """Once running, wait for each k3s cluster to be Ready and apply its manifests,
        then report the pods that scheduled."""
        import threading
        clusters = list(getattr(self, "_last_k8s", []))

        def work():
            for k in clusters:
                self.ctx.log(f"{k.name}: starting k3s cluster (this takes ~20-30s)…", "info")
                ok, msg = self._gloader.k8s_apply(k.svc)
                if not ok:
                    self.ctx.log(f"{k.name}: kubectl apply failed — {msg}", "error")
                    continue
                pods = self._gloader.k8s_pods(k.svc)
                self.ctx.log(f"{k.name}: applied {len(k.deployments)} deployment(s); "
                             f"{len(pods)} pod(s) scheduling.", "ok")
        threading.Thread(target=work, daemon=True).start()

    def _poll_fabric(self) -> None:
        if not self._running:
            return
        import threading

        def work():
            snap = self._gloader.fabric_metrics()
            if snap:
                self.ctx.bus.fabric_metrics.emit(snap)
        threading.Thread(target=work, daemon=True).start()

    def _on_fabric_metrics(self, snap) -> None:
        self.dashboard.set_fabric(snap.get("totals", {}))
        self.inspector.set_fabric_snapshot(snap)

    # -- memory watchdog (fleet gauge + runaway detection) ------------------- #
    def _poll_mem(self) -> None:
        if not self._running:
            return
        import threading
        import time as _time

        def work():
            stats = self._gloader.stats_all()
            if stats:
                self.ctx.bus.mem_metrics.emit(
                    {"stats": stats, "vm": self._gloader.vm_memory_mib(),
                     "t": _time.monotonic()})
        threading.Thread(target=work, daemon=True).start()

    def _on_mem_metrics(self, snap) -> None:
        self._memwatch.ingest(snap["stats"], snap["t"])
        vm = snap.get("vm")
        used = self._memwatch.total_mib()
        runs = self._memwatch.runaways()
        self.dashboard.set_memory(used, vm, runs)
        for r in runs:                          # warn once per service per run
            if r.svc not in self._mem_warned:
                self._mem_warned.add(r.svc)
                self.ctx.log(
                    f"Memory watch: '{r.svc}' has grown {r.growth_mib:.0f} MiB in "
                    f"{r.span_s / 60:.0f} min (~{r.slope_mib_per_min:.0f} MiB/min) — "
                    "possible runaway. Check its console/processes before the Docker "
                    "VM runs out and the OOM killer takes the lab down.", "warn")
        if vm and used / vm >= 0.9 and not self._mem_pressure_warned:
            self._mem_pressure_warned = True
            self.ctx.log(
                f"Memory watch: the lab is using {used / 1024:.1f} GB of the Docker "
                f"VM's {vm / 1024:.1f} GB (>90%). Containers may be OOM-killed soon — "
                "raise Docker Desktop memory (Settings → Resources).", "warn")

    def _preflight_vm_memory(self) -> None:
        """At Run: warn if the Docker VM looks undersized for this topology — the
        student-facing version of the lesson from the OOM sweep."""
        import threading

        def work():
            vm = self._gloader.vm_memory_mib()
            if not vm:
                return
            from ..domain.memwatch import estimate_need_mib
            n = len(getattr(self, "_last_services", []) or []) or \
                len(self.ctx.topology.devices)
            need = estimate_need_mib(n)
            if need > vm * 0.9:
                self.ctx.log(
                    f"Preflight: this lab (~{n} containers, wants ~{need / 1024:.1f} GB) "
                    f"is tight for the Docker VM's {vm / 1024:.1f} GB. If containers "
                    "die with exit 137, raise Docker Desktop memory "
                    "(Settings → Resources).", "warn")
        threading.Thread(target=work, daemon=True).start()

    def _element_stats(self, device_name: str):
        """Live CPU%/memory sample for the Inspector's metrics plots (None if not running)."""
        if not self._running:
            return None
        from ..services.compiler import _svc
        return self._gloader.stats(_svc(device_name))

    def _element_stats_all(self):
        """CPU/mem/net for every running container — keeps per-element Live history."""
        return self._gloader.stats_all() if self._running else {}

    def _on_device_resized(self, device_id: str) -> None:
        """An element's size tier changed. If the lab is running, apply the new CPU cap
        to its container live (vertical scaling) via `docker update` — no restart."""
        if not self._running:
            return
        import threading
        from ..domain import pricing
        from ..services.compiler import _svc
        d = self.ctx.topology.devices.get(device_id)
        if d is None or not pricing.resizable(d.type_key):
            return
        lvl = pricing.size_level(getattr(d, "size", 1))
        lab, cpus, _mem, _mult = pricing.size_tier(lvl)
        svc, dname = _svc(d.name), d.name

        def worker():
            ok, msg = self._gloader.update_cpus(svc, cpus)
            if ok:
                self.ctx.log(f"Scaled {dname} to {lab} ({cpus:g} vCPU) live — no restart.",
                             "ok")
            else:
                self.ctx.log(f"{dname}: couldn't apply size live ({msg}). "
                             f"It takes effect on the next Run.", "info")
        threading.Thread(target=worker, daemon=True).start()

    def _on_palette_explain(self, type_key: str) -> None:
        """In explain mode, clicking a palette element explains that element TYPE."""
        a = getattr(self, "assistant", None)
        if a is not None and getattr(a, "explain_mode", False):
            a.explain_element_type(type_key)

    def _on_selection_explain(self, device_id) -> None:
        """In explain mode, selecting a device explains it on the canvas; clicking
        empty space exits. Lets the student move the explanation around freely."""
        a = getattr(self, "assistant", None)
        if a is None or not getattr(a, "explain_mode", False):
            return
        if device_id is not None:          # selecting a device explains it; empty space
            dev = self.ctx.topology.devices.get(device_id)   # is ignored (mode stays on,
            if dev:                                           # exit via the Explain toggle)
                a.explain_selected(dev.name)

    def _on_selection_terminal(self, device_id) -> None:
        """Point the Terminal tab at the selected element. Fills quietly — never raises the tab,
        so it cannot steal the pane from the Inspector while a student is reading it.

        Exists because bus.selection_changed is Signal(object) and carries the id alone; the panel
        needs the topology to turn that into a service name."""
        tp = getattr(self, "terminal_panel", None)
        if tp is None:
            return
        try:
            tp.on_selection(device_id, self.ctx.topology)
        except Exception as e:                # noqa: BLE001 - a panel must never break selection
            self.ctx.bus.log.emit("error", f"Terminal: {e}")

    def _issue_codes(self) -> None:
        """Teacher mode: mint the codes handed out with an assignment. One per student, because
        a proof is bound to its code and sharing one defeats the point."""
        from .proof_issue_dialog import ProofIssueDialog
        ProofIssueDialog(self.theme, self).exec()

    def _open_submission(self) -> None:
        """Teacher: look a receipt up on the course server and open the student's work.

        The marking loop, in the tool the work was built in — because the questions a marker
        actually has ("does it forward?", "did they wire the second subnet?") are answered by
        pressing Run, not by reading a summary.
        """
        from .mark_dialog import MarkDialog
        # show(), not exec(): the report stays readable while the marker works on the canvas it
        # describes. Kept on self, or Python would collect it the moment this returns.
        existing = getattr(self, "_mark_dialog", None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass                          # already gone on the C++ side
        self._mark_dialog = MarkDialog(self.ctx, self._open_submitted_topology, self)
        self._mark_dialog.show()
        self._mark_dialog.raise_()

    def _open_submitted_topology(self, project: dict, report: dict) -> None:
        """Put a downloaded submission on the canvas.

        Loaded through the SAME path as any other project — the server writes gBuilder's own format
        precisely so there is no conversion step, and a second reader here would be a second thing
        to keep in step with the format.

        Deliberately leaves `_project_path` unset: this is somebody else's work, and a later Save
        must not quietly write it over the marker's own project.
        """
        from ..domain.topology import Topology
        topo = Topology.from_dict(project.get("topology") or {})
        self._project_path = None
        self._router_programs.clear()
        self._set_topology(topo)
        who = report.get("receipt", "")
        title = report.get("title") or report.get("activity", "")
        self.setWindowTitle(f"gBuilder 6.0 — submission {who} · {title}")
        self.ctx.log(f"Opened submission {who} ({title}) — this is a student's work, not your "
                     f"project. Save As if you want to keep changes.", "ok")

    def _accept_late(self) -> None:
        """Teacher: take a submission the server refused, from the file the student still has.

        The case: they finished, the code lapsed before the upload landed, and gBuilder has been
        retrying something the server will refuse for ever — `expired` is deliberately not a
        settled outcome. The proof is valid and unacceptable at the same time.

        Sent to the Teaching Center rather than checked here, because a local verdict leaves the
        submission off the books: correct, and invisible to the gradebook and to every TA.
        """
        from .mark_dialog import MarkDialog
        existing = getattr(self, "_mark_dialog", None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
        self._mark_dialog = MarkDialog(self.ctx, self._open_submitted_topology, self)
        self._mark_dialog.show()
        self._mark_dialog.raise_()
        self._mark_dialog.choose_proof_file()

    def _verify_proof(self) -> None:
        """Teacher mode: read a student's proof. Read-only — it verifies and renders, never grades."""
        from .proof_verify_dialog import ProofVerifyDialog
        ProofVerifyDialog(self.theme, self).exec()

    def _on_selection_source(self, device_id) -> None:
        """Selecting a router points GINI Source at ~/.gini/scripts, the module directory
        every router shares. The tab is not raised: it fills quietly, so a student who wants
        to read a module before loading it finds it already there."""
        sb = getattr(self, "source_browser", None)
        if sb is None or device_id is None:
            return
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        from ..services.compiler import _role
        tk = getattr(dev, "type_key", "")
        try:
            if _role(tk) == "router":
                sb.show_scripts(dev.name)
            elif tk == "xv6":
                pass          # its source is the kernel board's to open; leave that view alone
            else:
                # anything else has no source yet. Clear, so the pane never keeps showing the
                # last router's module as though it belonged to what was just clicked.
                label = getattr(getattr(dev, "type", None), "label", "") or tk or "this element"
                sb.show_none(f"{dev.name} ({label})")
        except Exception as e:                # noqa: BLE001 - a browser must never break selection
            self.ctx.bus.log.emit("error", f"GINI Source: {e}")

    def _on_warning_explain(self, device_id: str) -> None:
        dev = self.ctx.topology.devices.get(device_id)
        if dev:
            self.ctx.select(device_id)            # spotlight + select the flagged node
            self.assistant.explain_warning(dev.name)

    def _revalidate(self) -> None:
        """Run the advisory lint and surface per-device warnings on the canvas."""
        from ..services.compiler import validate
        try:
            issues = validate(self.ctx.topology)
        except Exception:
            issues = []
        warnings: dict[str, list] = {}
        for it in issues:
            if it["level"] == "warn" and it["device"]:
                warnings.setdefault(it["device"], []).append(it["message"])
        self.ctx.warnings = warnings
        self.ctx.bus.warnings_changed.emit()

    def element_query(self, device_name: str, command: str) -> str:
        """Run a one-shot console command against a network element (needs Docker up)."""
        if not self._workdir:
            return "(not running)"
        import subprocess
        from ..services.compiler import _role, _svc
        svc = _svc(device_name)
        dev = next((d for d in self.ctx.topology.devices.values()
                    if d.name == device_name), None)
        # routers AND the OVS (gRouter in OpenFlow mode) speak via the gRouter control
        # socket — so the OVS console can run `openflow ...` to dump its flow table.
        is_router = dev is not None and _role(dev.type_key) in ("router", "ovs")
        try:
            # `timeout 12` bounds the IN-CONTAINER client too: subprocess.run's timeout
            # only kills the local `docker exec` — the remote grconsole would live on,
            # blocked in recv(), holding the router's control socket (which used to
            # wedge the serial rctl server: dead console + empty HUD queries).
            if is_router:
                # the real C gRouter: run one CLI command over its control socket
                cmd = ["docker", "compose", "exec", "-T", svc, "timeout", "12",
                       "python3", "/build/grouter-build/grconsole.py",
                       f"/run/{svc}.ctl", "--once", command]
            else:
                cmd = ["docker", "compose", "exec", "-T", "fabric", "timeout", "12",
                       "python", "-m", "dataplane.console", svc, command]
            r = subprocess.run(cmd, cwd=self._workdir, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
            return (r.stdout or r.stderr or "").strip() or "(no output)"
        except Exception as e:
            return f"(query failed: {e})"

    def _machine_shell(self, device_name: str, command: str) -> str:
        """Run a shell command directly inside a machine's container (docker exec).

        Unlike element_query, this does NOT go through the element control console (a plain
        Machine has no `gini>` console) — it execs into the container, so telemetry like
        `ss -tin` runs against the station's real TCP stack. Off the GUI thread."""
        if not self._workdir:
            return ""
        import subprocess
        from ..services.compiler import _svc
        try:
            svc = _svc(device_name)
            r = subprocess.run(["docker", "compose", "exec", "-T", svc, "sh", "-c", command],
                               cwd=self._workdir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
            return r.stdout or ""
        except Exception:
            return ""

    def _log_startup_times(self) -> None:
        """Log per-element startup times to the Console — the VM-vs-container headline
        (a Kata Instance boots a guest kernel, so it starts much slower than a container)."""
        if not self._running:
            return
        import threading

        def work():
            times = self._gloader.startup_times()
            if times:
                line = ", ".join(f"{svc} {ms:.0f} ms" for svc, ms in sorted(times.items()))
                self.ctx.log("Startup times — " + line, "info")
        threading.Thread(target=work, daemon=True).start()

    def _on_function_deploy(self) -> None:
        """AWS-style 'Deploy': push the current function code to the running runtime by
        recreating only the faas container (the rest of the lab keeps running)."""
        import threading
        if not self._running or not self._workdir:
            self.ctx.log("Run the topology first, then Deploy.", "info")
            return
        cfg = self._compile()
        if not cfg.faas:
            self.ctx.log("No Functions on the canvas to deploy.", "info")
            return
        self.ctx.log("Deploying functions — recreating the faas runtime…", "info")

        def worker(c=cfg, ai=self.ctx.settings.auto_internet):
            ok, msg = self._gloader.redeploy_faas(c, auto_internet=ai)
            self.ctx.log("Functions deployed — the runtime restarted with your latest code."
                         if ok else f"Deploy failed: {msg}", "ok" if ok else "error")

        threading.Thread(target=worker, daemon=True).start()

    def _on_function_invoke(self, device_id: str, method: str, body: str) -> None:
        """Invoke a Function from the inspector's Test panel: call it inside the faas
        runtime, capture status/duration/cold, and send the result back to the inspector."""
        import json
        import subprocess
        import threading
        from ..services.compiler import _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running or not self._workdir:
            self.ctx.bus.function_invoke_result.emit(
                device_id, "Start the topology first (Run), then Invoke.")
            return
        fn = _svc(dev.name)

        def work():
            cmd = ["docker", "compose", "exec", "-T",
                   "-e", f"GINI_FN={fn}", "-e", f"GINI_METHOD={method}",
                   "-e", f"GINI_BODY={body}", "faas", "python", "-c", _FAAS_INVOKE]
            try:
                r = subprocess.run(cmd, cwd=self._workdir, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=30)
                line = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
                d = json.loads(line) if line else None
                if d:
                    warm = "cold start" if d.get("cold") else "warm"
                    text = f"HTTP {d['code']} · {d['ms']} ms · {warm}\n\n{d['body']}"
                else:
                    text = "(no response)\n" + (r.stderr or "").strip()
            except Exception as e:
                text = f"Invoke failed: {e}"
            self.ctx.bus.function_invoke_result.emit(device_id, text)

        threading.Thread(target=work, daemon=True).start()

    # -- double-click: routers open the Router Lab, others open a terminal -- #
    def _on_device_activated(self, device_id: str) -> None:
        from ..services.compiler import _role, _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if _role(dev.type_key) in ("router", "ovs"):
            self._open_router_lab(device_id)     # OVS opens the lab in SDN dashboard mode
            return
        if dev.type_key == "xv6":                # xv6 Machine -> the OS workbench
            self._open_machine_lab(device_id)
            return
        if _role(dev.type_key) == "oszoo":       # OS Zoo -> the historical OS in an embedded screen
            self._open_zoo_lab(device_id)
            return
        if dev.type_key == "desktop":            # headful Machine -> its graphical desktop over noVNC
            self._open_desktop_console(device_id)
            return
        if dev.type_key in ("terminal", "storage_volume"):
            self._open_peripheral(device_id)     # Terminal / disk view on its xv6
            return
        if getattr(dev.type, "rider", False):    # a Source/Sink -> toggle it on/off on its donor
            self._toggle_rider(device_id)
            return
        # a running service with a web dashboard -> open it (Grafana, MinIO, …)
        if self._running and _role(dev.type_key) == "service":
            svc = _svc(dev.name)
            has_web = any(_svc(s.name) == svc and any(p.get("web") for p in s.ports)
                          for s in getattr(self, "_last_services", []))
            if has_web:
                self._open_console(device_id)
                return
        self._open_terminal(device_id)

    def _toggle_rider(self, device_id: str) -> None:
        """Double-click a Source/Sink → start it (runs continuously, output streams to its Live tab)
        or stop it. Off the GUI thread since the start/kill exec can block briefly."""
        import threading
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        threading.Thread(target=lambda: self.ctx.toggle_rider(device_id), daemon=True).start()

    def _toggle_xv6_rider(self, rider_id: str) -> dict:
        """Start/stop an xv6 rider (Shell Probe / Workload) over the machine's console. Called by
        ctx.toggle_rider for qemu-serial riders, so double-click and the inspector both reach here."""
        import threading
        dev = self.ctx.topology.devices.get(rider_id)
        if dev is None:
            return {"ok": False}
        if rider_id in self._xv6_rider_sessions:              # running -> stop
            self._xv6_rider_sessions[rider_id]["stop"] = True
            return {"ok": True, "running": False}
        donor = self.ctx.topology.donor_of(rider_id)
        provider = getattr(self, "_xv6_providers", {}).get(donor.id) if donor else None
        if provider is None:
            self.ctx.log(f"{dev.name}: run the xv6 Machine first (press Run).", "info")
            self.ctx.bus.rider_ran.emit(rider_id, {"ok": False, "running": False})
            return {"ok": False, "error": "xv6 not running"}
        from ..domain import riders as R
        try:
            cmd = R.xv6_command(dev.type_key, dev.properties)
        except R.RiderError as e:
            self.ctx.log(f"{dev.name}: {e}", "info")
            self.ctx.bus.rider_ran.emit(rider_id, {"ok": False, "running": False})
            return {"ok": False, "error": str(e)}
        sess = self._xv6_rider_sessions[rider_id] = {"stop": False}
        self.ctx.log(f"{dev.name} → xv6: {cmd}", "info")
        threading.Thread(target=self._xv6_reader, args=(rider_id, provider, cmd, sess),
                         daemon=True).start()
        return {"ok": True, "running": True}

    def _xv6_reader(self, rider_id: str, provider, cmd: str, sess: dict) -> None:
        """Type the command into the xv6 console and stream the new output back as rider snapshots
        (reusing the same rider_ran path the inspector Live tab renders)."""
        import time
        lines: list[str] = []

        def emit(running: bool) -> None:
            raw = "\n".join(lines[-300:])
            snap = {"ok": True, "running": running, "raw": raw,
                    "measurement": {"ok": bool(raw), "lines": len(lines)},
                    "summary": f"{len(lines)} lines"}
            self.ctx.rider_results[rider_id] = snap
            self.ctx.bus.rider_ran.emit(rider_id, snap)

        try:
            _, cur = provider.console_since(0)               # start after the current console tail
            provider.send_input(cmd + "\n")
            emit(True)
            deadline = time.time() + 12                      # stream a while, or until stopped
            while not sess.get("stop") and time.time() < deadline:
                try:
                    text, cur = provider.console_since(cur)
                except Exception:                            # noqa: BLE001 — bridge dropped
                    break
                if text:
                    lines.extend(text.split("\n"))
                    emit(True)
                time.sleep(0.3)
        except Exception as e:                               # noqa: BLE001
            self.ctx.log(f"xv6 rider error: {e}", "info")
        self._xv6_rider_sessions.pop(rider_id, None)
        emit(False)

    def _on_rider_state(self, rider_id: str, snap) -> None:
        """Reflect a rider's live state on its node chip: green 'running' while it streams, back to
        'ready' (donor up) or 'idle' (topology down) when it stops."""
        node = self.canvas.scene_.nodes.get(rider_id)
        if node is None:
            return
        if snap and snap.get("running"):
            node.set_status("running")
        else:
            node.set_status("ready" if self._running else "idle")

    def _retire_lab(self, attr: str) -> None:
        """Close and destroy a previously opened lab window before opening another.

        These dialogs are parented to MainWindow, so rebinding the attribute does NOT free them:
        Qt keeps every one alive as a child, timers and all. The Router Lab polls `docker compose
        exec` every 2.5s, so each abandoned window left a permanent background load on the machine
        — invisible, cumulative over a session, and eventually enough to make a slow box look like
        it had stalled.
        """
        old = getattr(self, attr, None)
        if old is None:
            return
        setattr(self, attr, None)
        try:
            old.close()
            old.setParent(None)
            old.deleteLater()
        except RuntimeError:
            pass                              # already gone; nothing to retire

    def _open_router_lab(self, device_id: str) -> None:
        from ..domain.router_modules import RouterProgram
        from ..services.compiler import _role
        from .router_lab import RouterLab
        dev = self.ctx.topology.devices[device_id]
        program = self._router_programs.setdefault(device_id, RouterProgram())
        # role-specialized face of the one gRouter engine: OVS -> SDN dashboard, Firewall ->
        # rules-first, plain Router -> full pipeline.
        face = ("ovs" if dev.type_key == "ovs"
                else "firewall" if dev.type_key == "firewall" else "router")
        sdn = face == "ovs"
        # When running, the Router Lab drives the REAL C gRouter's pipeline via `gpipe`
        # over its control socket (gr_rctl); offline it uses the local trace.
        cf = ((lambda c, n=dev.name: self.element_query(n, "gpipe " + c))
              if self._running else None)
        # raw CLI query used by the live panels: `openflow …` for an OVS, `route`/`arp`
        # for a router. Same control socket as the console.
        qf = ((lambda c, n=dev.name: self.element_query(n, c)) if self._running else None)
        self._retire_lab("_router_lab")
        self._router_lab = RouterLab(
            self, self.theme, dev, program,
            on_console=lambda: self._open_terminal(device_id),
            command_fn=cf, sdn=sdn, query_fn=qf, face=face)
        self._router_lab.show()
        self._router_lab.raise_()

    def _wire_xv6_providers(self) -> None:
        """After a run, build a live Xv6Bridge for each xv6 service from its published gdb (1234)
        and serial (4444) host ports, and reset any open MachineState so the Lab/agent read live
        state. Safe no-op if the bridge deps (gdb/qemu container) aren't reachable."""
        providers = getattr(self, "_xv6_providers", {})
        for s in getattr(self, "_last_services", []):
            if getattr(s, "type_key", None) != "xv6":
                continue
            agent = next((p["host"] for p in s.ports if p.get("label") == "agent"), None)
            if agent is None:
                continue
            did = next((d.id for d in self.ctx.topology.devices.values()
                        if d.name == s.name and d.type_key == "xv6"), None)
            if did is None:
                continue
            try:
                from ..runtime.xv6_bridge import connect
                q = int((self.ctx.topology.devices[did].properties or {}).get("Timeslice", "1"))
                bridge = connect(agent, quantum=q)              # HTTP to the in-container agent
                providers[did] = bridge
            except Exception as e:
                self.ctx.log(f"xv6 live bridge unavailable for {s.name}: {e}", "info")
                continue
            # Attach the live plane to any already-open state so its Real toggle now works (and a
            # Lab already in Real mode goes live immediately). Never force a demo user to Real.
            ms = self.ctx.machine_states.get(did)
            if ms is not None and hasattr(ms, "attach_real"):
                ms.attach_real(bridge, vm=getattr(bridge, "vm", None),
                               fs=getattr(bridge, "fs", None))
        self._xv6_providers = providers

    def _machine_state_for(self, device_id: str):
        """Get (or lazily create) the shared MachineState for an xv6 Machine — the bridge the
        Lab renders from and the Ask GINI agent reads from. When running, a live GDB-backed
        provider is used if one has been registered (Mac-side); otherwise the offline demo feed.
        Kept on ctx so the assistant can find it without the Lab dialog being open."""
        from ..domain.machine_state import MachineState
        from ..domain.xv6 import DemoScheduler
        states = self.ctx.machine_states
        ms = states.get(device_id)
        if ms is None:
            bridge = None
            if self._running:
                bridge = getattr(self, "_xv6_providers", {}).get(device_id)
            # Default DATA MODE (a user choice thereafter, never auto-switched): Real when a live
            # bridge exists at open, Demo otherwise. The bridge is the Real plane; the demo feed
            # is built lazily by MachineState when the user toggles to Demo.
            if bridge is not None:
                ms = MachineState(bridge, device_id=device_id, mode="real",
                                  vm=getattr(bridge, "vm", None), fs=getattr(bridge, "fs", None))
            else:
                dev = self.ctx.topology.devices[device_id]
                demo = DemoScheduler(
                    timeslice=int((dev.properties or {}).get("Timeslice", "1") or "1"))
                ms = MachineState(demo, device_id=device_id, mode="demo")
            # new teachable kernel events -> notify the assistant (proactive Coach)
            ms.on_event = lambda s, did=device_id: self.ctx.bus.machine_events.emit(did)
            states[device_id] = ms
        return ms

    def _xv6_for_peripheral(self, device_id: str) -> str | None:
        """The xv6 Machine a peripheral is wired to (grammar guarantees at most one), or None."""
        for l in self.ctx.topology.links.values():
            other = (l.target_id if l.source_id == device_id else
                     l.source_id if l.target_id == device_id else None)
            if other is None:
                continue
            od = self.ctx.topology.devices.get(other)
            if od is not None and od.type_key == "xv6":
                return other
        return None

    def _open_peripheral(self, device_id: str) -> None:
        """Open a peripheral's view bound to the xv6 Machine it's wired to. Terminal = the shell
        console, Storage Volume = the disk's file-system face. Needs a wired xv6 (and, for the
        Terminal, a running one)."""
        dev = self.ctx.topology.devices[device_id]
        xv6_id = self._xv6_for_peripheral(device_id)
        if xv6_id is None:
            self.ctx.log(f"Wire {dev.name} to an xv6 Machine first "
                         "(long-press the Machine to see where peripherals attach).", "info")
            return
        xv6 = self.ctx.topology.devices[xv6_id]
        ms = self._machine_state_for(xv6_id)
        if dev.type_key == "storage_volume":
            # the disk is the file system — reuse the Storage face against this Machine's FS reader
            from .storage_lab import StorageLab
            self._storage = StorageLab(self, self.theme, device=xv6, provider=ms.fs)
            self._storage.show(); self._storage.raise_()
            return
        if not getattr(ms, "live", False) or not hasattr(ms.provider, "console"):
            self.ctx.log(f"Start the topology (Run) so {xv6.name} is booted, then open "
                         f"{dev.name}.", "info")
            return
        from .peripherals import TerminalView
        self._peripheral = TerminalView(self, self.theme, ms.provider, dev)
        self._peripheral.show(); self._peripheral.raise_()

    def _open_machine_lab(self, device_id: str) -> None:
        """Open the Machine Lab on an xv6 Machine — the OS workbench (scheduler face).

        Renders from the shared MachineState (the bridge). When running, that state is fed by a
        live provider reading the kernel over QEMU's GDB stub (Mac-side); offline it uses a
        deterministic demo feed so the lab is always explorable."""
        from .machine_lab import MachineLab
        dev = self.ctx.topology.devices[device_id]
        ms = self._machine_state_for(device_id)
        try:
            self._retire_lab("_machine_lab")
            self._machine_lab = MachineLab(
                self, self.theme, dev, state=ms, live=getattr(ms, "live", False),
                on_console=lambda: self._open_terminal(device_id),
                on_log=lambda lvl, msg: self.ctx.bus.log.emit(lvl, msg))  # mirror to GINI Console
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.ctx.bus.log.emit("error", f"Machine Lab failed to open: {e}\n{tb}")
            try:
                from PySide6.QtWidgets import QMessageBox
                box = QMessageBox(self)
                box.setWindowTitle("Machine Lab failed to open")
                box.setIcon(QMessageBox.Warning)
                box.setText(f"The Machine Lab could not open:\n{e}")
                box.setDetailedText(tb)      # expandable, selectable traceback to copy
                box.exec()
            except Exception:
                pass
            return
        self._machine_lab.show()
        self._machine_lab.raise_()

    def _open_desktop_console(self, device_id: str) -> None:
        """Open a headful Desktop machine's graphical screen (X served over noVNC) embedded in a
        window — the same viewer the OS Zoo uses. Falls back to the system browser if QtWebEngine
        isn't available. Needs the topology running (the container publishes the noVNC port)."""
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running:
            self.ctx.log("Start the topology first (Run), then open the Desktop screen.", "info")
            return
        from ..services.compiler import _svc
        svc = _svc(dev.name)
        port = next((getattr(m, "novnc_port", 0) for m in getattr(self, "_last_machines", [])
                     if _svc(m.name) == svc and getattr(m, "novnc_port", 0)), 0)
        if not port:
            self.ctx.log(f"{dev.name}: no desktop console — is it running as a Desktop (gui) host?",
                         "info")
            return
        # resize=scale, not remote: `remote` asks the VNC server to change the framebuffer size to
        # match the window, and x11vnc serves a fixed Xvfb screen, so it refuses every time —
        # "Server did not accept the resize request: Resize is administratively prohibited" on the
        # console, once per connect. `scale` fits the same framebuffer to the window client-side,
        # which is what we actually wanted.
        url = f"http://localhost:{port}/vnc.html?autoconnect=1&resize=scale"
        try:
            from .zoo_lab import ZooLab
        except Exception as e:                    # QtWebEngine missing -> browser fallback
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))
            self.ctx.log(f"Opening {dev.name} in your browser (embedded view needs "
                         f"PySide6-Addons for QtWebEngine: {e}): {url}", "info")
            return
        self._retire_lab("_desktop_lab")
        self._desktop_lab = ZooLab(self, self.theme, dev, url)
        self._desktop_lab.show()
        self._desktop_lab.raise_()
        self.ctx.log(f"Opening {dev.name} desktop: {url}", "ok")

    def _open_zoo_lab(self, device_id: str) -> None:
        """Open the Zoo Lab on an OS Zoo element — the historical OS running under emulation,
        its screen embedded over noVNC. Needs the topology running (the container serves the
        framebuffer). If QtWebEngine isn't available, fall back to the system browser so the
        feature still works with no extra dependency."""
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running:
            self.ctx.log("Start the topology first (Run), then open the OS Zoo screen.", "info")
            return
        from ..services.compiler import _svc
        svc = _svc(dev.name)
        screen = None
        for s in getattr(self, "_last_services", []):
            if _svc(s.name) == svc:
                screen = next((p for p in s.ports
                               if p.get("web") and p.get("label") == "screen"), None)
                break
        if screen is None:
            self.ctx.log(f"{dev.name}: no OS Zoo screen port — rebuild the image (gini-oszoo).",
                         "info")
            return
        url = f"http://localhost:{screen['host']}{screen.get('path', '')}"
        try:
            from .zoo_lab import ZooLab
        except Exception as e:                    # QtWebEngine missing -> browser fallback
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))
            self.ctx.log(f"Opening {dev.name} in your browser (embedded view needs "
                         f"PySide6-Addons for QtWebEngine: {e}): {url}", "info")
            return
        self._retire_lab("_zoo_lab")
        self._zoo_lab = ZooLab(self, self.theme, dev, url)
        self._zoo_lab.show()
        self._zoo_lab.raise_()
        self.ctx.log(f"Opening {dev.name} (OS Zoo): {url}", "ok")

    def _open_console(self, device_id: str) -> None:
        """Open a service's web dashboard (Grafana, MinIO, RabbitMQ, …) in the browser.
        These ship full web UIs — the browser is their native interface — so we just
        launch the published console URL rather than rebuilding it natively."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running:
            self.ctx.log("Start the topology first (Run), then open the console.", "info")
            return
        from ..services.compiler import _svc
        svc = _svc(dev.name)
        web = None
        for s in getattr(self, "_last_services", []):
            if _svc(s.name) == svc:
                web = next((p for p in s.ports if p.get("web")), None)
                break
        if web is None:
            self.ctx.log(f"{dev.name} has no web console — try “Log in” or “View logs”.",
                         "info")
            return
        url = f"http://localhost:{web['host']}{web.get('path', '')}"
        QDesktopServices.openUrl(QUrl(url))
        self.ctx.log(f"Opening {dev.name} console: {url}", "ok")

    def _open_logs(self, device_id: str) -> None:
        """Open a terminal tailing this element's container logs — handy for watching
        the OpenFlow handshake (POX '[…] connected') and gRouter connect attempts."""
        from ..services import open_terminal
        from ..services.compiler import _role, _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running or not self._workdir:
            self.ctx.log("Start the topology first (Run), then right-click → View logs.",
                         "info")
            return
        role = _role(dev.type_key)
        if role == "switch":                  # plain switches live in the fabric container
            log_cmd = "docker compose logs --tail=200 -f fabric"
        elif role == "k8sworkload":           # a Pod has no container — tail it via kubectl
            cluster = self._k8s_cluster_svc(device_id)
            if not cluster:
                self.ctx.log(f"{dev.name} isn't connected to a K8s Cluster yet.", "info")
                return
            log_cmd = f"docker compose exec {cluster} kubectl logs deploy/{_svc(dev.name)} --tail=200 -f"
        elif role == "hpa":
            self.ctx.log(f"{dev.name} (autoscaler) has no logs — double-click it for status.",
                         "info")
            return
        elif role == "function":              # functions share the one `faas` runtime container
            log_cmd = "docker compose logs --tail=200 -f faas"
        else:
            log_cmd = f"docker compose logs --tail=200 -f {_svc(dev.name)}"
        ok, msg = open_terminal(f"GINI {dev.name} logs", self._workdir, log_cmd)
        self.ctx.log(f"Opening logs for {dev.name}…" if ok
                     else f"Could not open logs: {msg}", "info" if ok else "error")

    # -- log into a device -------------------------------------------------- #
    def _open_terminal(self, device_id: str) -> None:
        from ..services import open_terminal
        from ..services.compiler import _role, _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        role = _role(dev.type_key)
        if role == "group":
            self.ctx.log(f"{dev.name} is a grouping — nothing to log into.", "info")
            return
        if not self._running or not self._workdir:
            self.ctx.log("Start the topology first (Run), then double-click to log in.",
                         "info")
            return
        svc = _svc(dev.name)
        if role == "xv6":                  # the real xv6 console is QEMU's serial (published TCP)
            port = None
            for ss in getattr(self, "_last_services", []):
                if _svc(ss.name) == svc:
                    port = next((p["host"] for p in ss.ports if p.get("label") == "serial"), None)
                    break
            if port is None:
                self.ctx.log(f"{dev.name}: serial console not available yet.", "info")
                return
            cmd = f"nc localhost {port}"       # xv6 shell; Ctrl-P prints the process table
            kind = "xv6 serial console"
        elif role == "machine":
            cmd = f"docker compose exec {svc} sh"
            kind = "shell"
        elif role in ("router", "ovs"):   # real C gRouter CLI over its control socket
            cmd = (f"docker compose exec {svc} python3 "
                   f"/build/grouter-build/grconsole.py /run/{svc}.ctl")
            kind = "OpenFlow switch console" if role == "ovs" else "router console"
        elif role == "controller":   # POX container — a shell to inspect it / tail logs
            cmd = f"docker compose exec {svc} sh"
            kind = "controller shell"
        elif role in ("service", "compute"):   # cloud service / compute container — shell
            cmd = f"docker compose exec {svc} sh"
            kind = "service shell" if role == "service" else "instance shell"
        elif role in ("k8scluster", "k8snode"):   # the real k3s container — kubectl lives here
            cmd = f"docker compose exec {svc} sh"
            kind = "Kubernetes shell (run kubectl here)"
        elif role == "k8sworkload":   # a Pod = a Deployment; exec into one of its pods via the cluster
            cluster = self._k8s_cluster_svc(device_id)
            if not cluster:
                self.ctx.log(f"{dev.name} isn't connected to a K8s Cluster yet.", "info")
                return
            cmd = f"docker compose exec {cluster} kubectl exec -it deploy/{svc} -- sh"
            kind = "pod shell"
        elif role == "hpa":   # the Pod Autoscaler (HPA) — show its live status
            cluster = self._k8s_cluster_svc(device_id)
            hpa = next((_svc(self.ctx.topology.devices[o].name)
                        for l in self.ctx.topology.links.values()
                        for o in (l.target_id if l.source_id == device_id else
                                  l.source_id if l.target_id == device_id else None,)
                        if o and _role(self.ctx.topology.devices[o].type_key) == "k8sworkload"), None)
            if not cluster or not hpa:
                self.ctx.log(f"{dev.name} isn't attached to a Pod yet.", "info")
                return
            cmd = f"docker compose exec {cluster} kubectl describe hpa {hpa}"
            kind = "autoscaler status"
        elif role == "function":   # a handler in the shared `faas` runtime, not its own container
            cmd = "docker compose exec faas sh"
            kind = "faas runtime shell"
            self.ctx.log(
                f"{dev.name} runs in the shared faas runtime. Invoke it from this shell with:\n"
                f"  python -c \"import urllib.request as u; "
                f"print(u.urlopen('http://localhost:8000/{svc}').read().decode())\"", "info")
        else:  # plain switch — attach to that element's console in the fabric container
            cmd = f"docker compose exec fabric python -m dataplane.console {svc}"
            kind = "console"
        ok, msg = open_terminal(f"GINI {dev.name} {kind}", self._workdir, cmd)
        self.ctx.log(f"Opening {kind} for {dev.name}…" if ok
                     else f"Could not open terminal: {msg}", "info" if ok else "error")

    # -- reactions ---------------------------------------------------------- #
    def _on_scene_selection(self) -> None:
        # single source of truth for selection -> inspector (avoids the click race)
        from .canvas import GroupItem, NodeItem
        try:
            selected = self.canvas.scene_.selectedItems()
        except RuntimeError:
            return                              # scene torn down (window closing)
        nodes = [i for i in selected if isinstance(i, (NodeItem, GroupItem))]
        if nodes:
            self.ctx.select(nodes[0].inst.id)
        else:
            self.ctx.select(None)
            self.ctx.bus.present_clear.emit()   # clicking empty space dismisses the tutor
        self._update_delete_enabled()

    # -- delete devices ----------------------------------------------------- #
    def _make_delete_shortcut(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeySequence
        a = QAction("Delete device", self)
        a.setShortcuts([QKeySequence(QKeySequence.Delete), QKeySequence(Qt.Key_Backspace)])
        a.setShortcutContext(Qt.WidgetWithChildrenShortcut)   # only when the canvas has focus
        a.triggered.connect(self._delete_selected)
        self.canvas.addAction(a)

    def _update_delete_enabled(self) -> None:
        from .canvas import EdgeItem, GroupItem, NodeItem
        has_sel = any(isinstance(i, (NodeItem, GroupItem, EdgeItem))   # cards, boxes, AND wires
                      for i in self.canvas.scene_.selectedItems())
        busy = self._running or getattr(self, "_stopping", False)
        self._delete_act.setEnabled(has_sel and not busy)

    def _delete_selected(self) -> None:
        # delete selected CARDS *and* BOXES — GroupItems (VPC/Subnet/Region) were excluded, so a
        # selected VPC/Subnet left Delete + the trash button doing nothing. Selected WIRES
        # (EdgeItems) delete too — a link is removable without deleting its endpoints.
        from .canvas import EdgeItem, GroupItem, NodeItem
        selected = self.canvas.scene_.selectedItems()
        ids = [i.inst.id for i in selected if isinstance(i, (NodeItem, GroupItem))]
        lids = [i.link.id for i in selected if isinstance(i, EdgeItem)]
        self._remove_devices(ids)       # device removal prunes its links; do it first
        self._remove_links([l for l in lids if l in self.ctx.topology.links])

    def _remove_links(self, lids: list[str]) -> None:
        if not lids:
            return
        if self._running or getattr(self, "_stopping", False):
            self.ctx.log("Stop the topology before removing links.", "info")
            return
        names = []
        for lid in lids:
            link = self.ctx.topology.links.get(lid)
            if link:
                a = self.ctx.topology.devices.get(link.source_id)
                b = self.ctx.topology.devices.get(link.target_id)
                names.append(f"{a.name if a else '?'}–{b.name if b else '?'}")
                self.ctx.remove_link(lid)
        if names:
            self._recompute_addressing()    # subnets/IPs shift when a segment disappears
            self._update_status()
            self._update_delete_enabled()
            self.ctx.log(f"Removed link {', '.join(names)}.", "info")

    def _delete_device(self, device_id: str) -> None:
        self._remove_devices([device_id])

    def _remove_devices(self, ids: list[str]) -> None:
        if not ids:
            return
        if self._running or getattr(self, "_stopping", False):
            self.ctx.log("Stop the topology before removing devices "
                         "(running elements can't be deleted).", "info")
            return
        names = []
        for did in ids:
            dev = self.ctx.topology.devices.get(did)
            if dev:
                names.append(dev.name)
                self.ctx.remove_device(did)     # model + canvas (device_removed) + links
        if names:
            self.ctx.select(None)
            self._recompute_addressing()        # IPs shift when a device leaves
            self._update_status()
            self._update_delete_enabled()
            self.ctx.log(f"Removed {', '.join(names)}.", "info")

    # Windows other than this one are themed ONCE, when they open.
    #
    # The Labs bake their colours in at construction — 151 setStyleSheet calls across seven files
    # read `theme.theme` as they build and never look again. Switching the theme underneath an
    # open Lab therefore repaints the application stylesheet but not those baked colours, and the
    # window ends up half light and half dark. (The OS HUD and the canvas are fine: they read the
    # theme inside paintEvent, so they follow a switch for free.)
    #
    # The rule is "a window's theme is fixed for its lifetime; only the main window is live". That
    # is honest, costs nothing, and beats the alternatives — rebuilding open Labs makes them blink
    # and lose their page, and making 151 call sites re-runnable is a large change to code that
    # works. Labs already open with the CURRENT theme, so closing and reopening gets you there.
    _LIVE_THEMED = ("MainWindow",)

    def _open_windows(self) -> list:
        """Visible top-level windows of ours, other than this one. Menus, tooltips and popups are
        top-level too, so anything without a title bar is ignored."""
        from PySide6.QtWidgets import QApplication
        out = []
        for w in QApplication.topLevelWidgets():
            if w is self or not w.isVisible():
                continue
            if type(w).__name__ in self._LIVE_THEMED:
                continue
            if not (w.windowFlags() & Qt.Window):        # menus/tooltips/popups are not windows
                continue
            title = w.windowTitle()
            if title:
                out.append(title)
        return out

    def _pick_theme(self, name: str) -> None:
        """Theme menu entry point. Refuses while another window is open, and says which."""
        open_now = self._open_windows()
        if open_now:
            shown = ", ".join(open_now[:3]) + (" …" if len(open_now) > 3 else "")
            self.ctx.bus.log.emit(
                "error", f"Theme unchanged — close these first: {shown}. "
                         "Other windows are themed when they open, so they would not follow.")
            self._sync_theme_actions()               # un-check the entry the user just clicked
            return
        self.theme.set_theme(name)

    def _sync_theme_actions(self) -> None:
        """Put the checkmark back on the theme that is actually active."""
        active = self.theme.theme.name.lower()
        for nm, act in getattr(self, "_theme_actions", {}).items():
            act.setChecked(nm.lower() == active)

    def _on_theme_changed(self, name: str) -> None:
        # persist on EVERY theme switch (the toolbar palette menu used to change the theme
        # live but never save it, so it reverted on restart)
        self.ctx.settings.theme = name
        self._persist_settings()
        self.canvas.scene_.set_theme(self.theme.theme)
        self._refresh_icons()
        # the pill widget paints its own font — nudge it to re-measure/repaint at the new text size
        if getattr(self, "mode_indicator", None) is not None:
            self.mode_indicator.updateGeometry(); self.mode_indicator.update()
        # Widgets that style themselves from theme tokens subscribe to theme.themeChanged
        # themselves (see SourceBrowser and Dashboard), so there is nothing to poke here.
        self._update_status()

    def _on_log(self, level: str, message: str) -> None:
        tag = {"ok": "✓", "error": "✕", "chat": "›"}.get(level, "•")
        self.console.appendPlainText(f"{tag} {message}")
        if level == "error":                 # audible cue — the student checks the Console for why
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.beep()
            except Exception:
                pass

    def _update_status(self) -> None:
        s = self.api.summary()
        running = getattr(self, "_running", False)
        busy = running or getattr(self, "_stopping", False)
        self.status_conn.setText("  ● running" if running else "  ● idle")
        self.status_counts.setText(f"{s['devices']} devices · {s['links']} links   ")
        self.status_theme.setText(f"{self.theme.theme.name}   ")
        if hasattr(self, "_delete_act"):
            self._update_delete_enabled()        # disabled while running
        if hasattr(self, "_nav_btn"):            # can't swap the project out from under a run
            self._nav_btn.setEnabled(not busy)
            self._nav_btn.setToolTip("Stop the topology before switching projects" if busy
                                     else "Project — switch, create, save, or set the AI brief")

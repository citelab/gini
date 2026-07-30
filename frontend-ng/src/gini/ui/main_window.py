"""The gBuilder main window — shell that assembles every panel.

Layout: device palette (left dock) · canvas (center) · inspector + assistant
(right docks, tabbed) · console (bottom dock) · toolbar + status bar. All visuals
flow from the ThemeManager so Dark / Light / GINI Brand swap instantly.
"""
from __future__ import annotations

import math

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
        self._stopping = False
        self._workdir: str | None = None
        self._project_path: str | None = None
        self._project_dir: str | None = None       # active project folder (Projects)
        self._router_programs: dict = {}        # device id -> RouterProgram (Router Lab)
        self.ctx.bus.run_state.connect(self._on_run_state)
        self.ctx.bus.runtime_status.connect(self._on_runtime_status)
        self.ctx.bus.device_activated.connect(self._on_device_activated)
        from PySide6.QtCore import QTimer
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._poll_status)
        self._fabric_poll = QTimer(self)        # cloud-fabric app metrics
        self._fabric_poll.setInterval(2000)
        self._fabric_poll.timeout.connect(self._poll_fabric)
        self.ctx.bus.fabric_metrics.connect(self._on_fabric_metrics)
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
        # apply every value the dialog returned (a missing key must never abort the save)
        for k in ("theme", "text_size", "reduced_motion", "auto_internet",
                  "llm_enabled", "llm_url", "llm_model", "llm_think",
                  "name_prefixes", "prices", "show_help_on_launch",
                  "tc_url", "tc_course", "tc_student", "tc_token", "tc_allow_insecure"):
            if k in v:
                setattr(s, k, v[k])
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
            teacher = tc.is_teacher()
            if not teacher:                            # 'Due / Completed' is a student view
                self._add_mission_items(m, tc)
                m.addSeparator()
            self._add_teacher_items(m, tc)
            m.addAction("Messages…").triggered.connect(self._open_messages)
            m.addAction("Set my photo…").triggered.connect(self._set_photo)
            if not teacher:                            # groups + AI-proxy are student notions
                self._add_group_items(m, tc)
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
        cap = menu.addAction("Teacher tools")
        cap.setEnabled(False)
        menu.addAction("Fragment Manager…").triggered.connect(self._fragment_manager)
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
        makes a group view worth opening. Off the GUI thread; a failed beat is a non-event."""
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is None or not tc.signed_in():
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
        import threading
        threading.Thread(target=lambda: tc.heartbeat(progress), daemon=True).start()

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
        act("new", "new", "New project", self._new_project)
        act("open", "open", "Open project", self._open_project_dialog)
        act("save", "save", "Save project", self._save_project)
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
        self._delete_act = act("delete", "trash", "Delete selected device", self._delete_selected)
        self._delete_act.setEnabled(False)
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
            lay.addWidget(tray(("compile", "layout", "connect", "edges", "manualaddr", "delete")))
            lay.addWidget(self._tb_spacer(8))
            lay.addWidget(self.run_button)                    # morphing ▶/■ power button
            lay.addWidget(self._tb_spacer(8))
            lay.addWidget(tray(("zoom_in", "zoom_out")))
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
            a.triggered.connect(lambda _=False, n=name: self.theme.set_theme(n))
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
        from ..app.paths import projects_dir
        from ..services import list_projects
        menu.clear()
        header = menu.addAction(f"● {self._nav_btn.text()}")
        header.setEnabled(False)
        menu.addSeparator()
        menu.addAction("New project…", self._new_project)
        menu.addAction("Open project…", self._open_project_dialog)
        menu.addAction("Save", self._save_project)
        menu.addAction("Save As…", self._save_project_as)
        menu.addSeparator()
        recents = list_projects(projects_dir())[:6]
        if recents:
            r = menu.addAction("Recent projects"); r.setEnabled(False)
            for info in recents:
                act = menu.addAction("   " + info["name"])
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
        self._persist_current_project()          # save whatever we were on
        self._project_dir = str(folder)
        self._project_path = None
        self._router_programs.clear()
        self._set_topology(Topology(name))
        self.assistant.clear_conversation()
        self._set_project_label(name)
        self.setWindowTitle(f"gBuilder 6.0 — {name}")
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
        self._set_topology(data["topology"])
        self.assistant.set_brief(data["brief"])
        self.assistant.load_ai_state(data["ai_state"])   # swap the Ask GINI conversation
        self._set_project_label(data["name"])
        self.setWindowTitle(f"gBuilder 6.0 — {data['name']}")
        remember_project(data["path"])
        self.ctx.log(f"Opened project “{data['name']}”.", "ok")

    def _persist_current_project(self) -> None:
        """Write the active project folder (topology + brief + AI conversation)."""
        if not self._project_dir:
            return
        from pathlib import Path
        from ..app.paths import remember_project
        from ..services import save_project_dir
        name = Path(self._project_dir).name
        self.ctx.topology.name = name
        save_project_dir(self._project_dir, self.ctx.topology, name=name,
                         brief=self.assistant.brief(), ai_state=self.assistant.ai_state())
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
        ensure_dirs()
        self._project_dir = str(projects_dir() / name.strip())
        self._project_path = None
        self._set_project_label(name.strip())
        self.setWindowTitle(f"gBuilder 6.0 — {name.strip()}")
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
        self._project_dir = str(d)           # adopt the current (empty) canvas as Default
        self._set_project_label("Default")
        self.setWindowTitle("gBuilder 6.0 — Default")
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

    def closeEvent(self, e) -> None:
        self._persist_current_project()      # never lose the active project's work / chat
        super().closeEvent(e)

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
        self.dashboard.open_grafana_requested.connect(self._open_grafana)
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

        add(filem, "&New Project…", self._new_project, "Ctrl+N")
        add(filem, "&Open Project…", self._open_project_dialog, "Ctrl+O")
        add(filem, "&Save", self._save_project, "Ctrl+S")
        add(filem, "Save &As…", self._save_project_as, "Ctrl+Shift+S")
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

        helpm = mb.addMenu("&Help")
        tour_act = add(helpm, "&Feature Tour…", self.show_feature_tour)
        tour_act.setMenuRole(QAction.MenuRole.NoRole)

    def _new(self) -> None:
        from ..domain import Topology
        self._project_path = None
        self._router_programs.clear()
        self._set_topology(Topology("untitled"))
        self.setWindowTitle("gBuilder 6.0 — networks + cloud")
        self.ctx.log("New topology.", "info")

    def _open(self) -> None:
        from ..app.paths import projects_dir
        from ..services import PROJECT_EXT
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(projects_dir()),
            f"GINI project (*{PROJECT_EXT});;All files (*)")
        if path:
            self._load_from_path(path)

    def _save(self) -> None:
        if self._project_path:
            self._save_to_path(self._project_path)
        else:
            self._save_as()

    def _save_as(self) -> None:
        from ..app.paths import ensure_dirs, projects_dir
        from ..services import PROJECT_EXT
        from PySide6.QtWidgets import QFileDialog
        ensure_dirs()
        default = str(projects_dir() / f"untitled{PROJECT_EXT}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", default, f"GINI project (*{PROJECT_EXT})")
        if path:
            if not path.endswith(PROJECT_EXT):
                path += PROJECT_EXT
            self._save_to_path(path)

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
        self.ctx.topology = topo
        topo.prefix_overrides = dict(self.ctx.settings.name_prefixes)   # apply naming prefs
        self.ctx.selected_id = None
        if hasattr(self, "_manual_addr_act"):
            self._manual_addr_act.setChecked(topo.manual_addressing)
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
        self.ctx.log(f"Connectors: {'bent (rounded)' if bent else 'straight'}.", "info")

    def _toggle_manual_addr(self, on: bool) -> None:
        self.ctx.topology.manual_addressing = on
        self._recompute_addressing()      # re-derive with/without the manual overrides
        self._revalidate()
        self.ctx.log(
            "Manual addressing: on — set IPs in Inspector › Interfaces; blanks auto-fill."
            if on else "Manual addressing: off — IPs are auto-assigned.", "info")

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
        self._last_k8s = list(cfg.k8s)
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

        def worker(workdir=self._workdir, ai=auto_internet):
            ok, msg = self._gloader.up(cfg, workdir, auto_internet=ai)
            self.ctx.bus.run_state.emit(ok, msg)
        threading.Thread(target=worker, daemon=True).start()

    def _on_run_state(self, ok: bool, msg: str) -> None:
        if self._remote is not None:           # remote backend has its own (lighter) handling
            self._on_remote_run_state(ok, msg)
            return
        if ok:
            self._running = True
            self._stopping = False
            self._set_runtime_status("running")
            self._poll.start()                  # reconcile with real container state
            self.ctx.log("Topology running on Docker.", "ok")
            self._wire_xv6_providers()          # attach live GDB bridges to any xv6 kernels
            self._populate_overlay_hosts()      # names resolve over gini0, not the Docker bridge
            grafana = None
            for s in getattr(self, "_last_services", []):   # surface web consoles
                for p in s.ports:
                    if p.get("web"):
                        url = f"http://localhost:{p['host']}{p.get('path', '')}"
                        self.ctx.log(f"{s.name} ({p['label']}): {url}", "ok")
                        if getattr(s, "type_key", None) == "dashboard":
                            grafana = url
            # start the GINI $ meter billing the launched topology
            from ..domain.pricing import bill
            rate = bill(self.ctx.topology, self.ctx.settings.prices)["rate_per_hr"]
            self.dashboard.set_grafana_url(grafana)
            self.dashboard.start(rate)
            self.inspector.set_live_running(True)       # enable the Live metrics plots
            self.canvas.scene_.running = True           # enable console/logs/login actions
            self._fabric_poll.start()                   # poll cloud-fabric app metrics
            from PySide6.QtCore import QTimer
            QTimer.singleShot(6000, self._drive_loadgens)   # let Fortio boot, then load
            QTimer.singleShot(2000, self._log_startup_times)  # VM-vs-container startup signal
            if getattr(self, "_last_k8s", None):
                self._apply_k8s()                           # wait for k3s, kubectl apply
                self._k8s_poll.start()                      # poll deployment metrics
        else:
            self.run_button.set_state("error")
            self.ctx.log(f"Run failed: {msg}", "error")
        self._update_status()

    def _stop(self) -> None:
        import threading
        if not self._running:
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
            if self._running or self._stopping:
                self._running = False
                self._stopping = False
                self._poll.stop()
                self._set_runtime_status("idle")
                self.dashboard.stop()           # freeze the session's GINI $ bill
                self.dashboard.set_grafana_url(None)
                self._fabric_poll.stop()
                self._k8s_poll.stop()
                self.dashboard.set_fabric({})
                self.inspector.set_live_running(False)   # stop the Live metrics polling
                self.canvas.scene_.running = False        # grey out console/logs/login actions
                self.inspector.set_fabric_snapshot({})
                self.inspector.set_k8s_snapshot({})
                self.run_button.set_state("ready")
                self.ctx.log("All containers stopped.", "info")
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
                          "xv6"):
                st = states.get(_svc(node.inst.name))            # each its own container
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

    def _set_runtime_status(self, status: str) -> None:
        from ..services.compiler import _role
        for node in self.canvas.scene_.nodes.values():
            if _role(node.inst.type_key) in ("machine", "switch", "router", "ovs",
                                             "controller", "service", "compute", "function",
                                             "k8scluster", "k8sworkload", "hpa", "k8snode",
                                             "xv6", "peripheral"):
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
        accumulation. This is the 'small Phase-2.x': it makes reachability follow the topology."""
        import shlex
        import subprocess
        import threading
        from ..services.compiler import _role, _svc, overlay_hosts
        orch = getattr(self.ctx, "orchestrator", None)
        if orch is None:
            return
        hosts = overlay_hosts(getattr(self.ctx, "addressing", {}) or {})
        if len(hosts) < 2:
            return
        dc = list(getattr(orch, "_dc", ["docker", "compose"]))
        wd = getattr(orch, "workdir", None)
        devs = [d for d in self.ctx.topology.devices.values()
                if _role(d.type_key) in ("machine", "router", "compute")]

        # include SELF, and PREPEND — Docker puts the container's own name→bridge line at the TOP of
        # /etc/hosts, so a getent/ping for the machine's own name would hit the bridge unless our
        # overlay line comes first. `> /etc/hosts` truncates-in-place (works on the bind mount; `mv`
        # wouldn't). Docker's original lines stay below, so nothing that needs the bridge id breaks.
        block = "\n".join(f"{ip}\t{nm}" for nm, ip in hosts.items())

        def work():
            for dev in devs:
                cmd = [*dc, "exec", "-T", _svc(dev.name), "sh", "-lc",
                       "{ printf '%%s\\n' %s; cat /etc/hosts; } > /tmp/gini_hosts && "
                       "cat /tmp/gini_hosts > /etc/hosts" % shlex.quote(block)]
                try:
                    subprocess.run(cmd, cwd=str(wd) if wd else None,
                                   capture_output=True, timeout=8)
                except Exception:                # noqa: BLE001 — best-effort; a slow box just misses
                    pass
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

    def _open_grafana(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url = self.dashboard.grafana_url()
        if not url:
            self.ctx.log("No Grafana running — add a Dashboards element and Run.", "info")
            return
        QDesktopServices.openUrl(QUrl(url))
        self.ctx.log(f"Opening Grafana: {url}", "ok")

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
            if is_router:
                # the real C gRouter: run one CLI command over its control socket
                cmd = ["docker", "compose", "exec", "-T", svc, "python3",
                       "/build/grouter-build/grconsole.py", f"/run/{svc}.ctl",
                       "--once", command]
            else:
                cmd = ["docker", "compose", "exec", "-T", "fabric",
                       "python", "-m", "dataplane.console", svc, command]
            r = subprocess.run(cmd, cwd=self._workdir, capture_output=True,
                               text=True, timeout=15)
            return (r.stdout or r.stderr or "").strip() or "(no output)"
        except Exception as e:
            return f"(query failed: {e})"

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
                                   text=True, timeout=30)
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
                providers[did] = connect(agent, quantum=q)      # HTTP to the in-container agent
            except Exception as e:
                self.ctx.log(f"xv6 live bridge unavailable for {s.name}: {e}", "info")
                continue
            self.ctx.machine_states.pop(did, None)     # rebuilt live on next open
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
            provider = None
            if self._running:
                provider = getattr(self, "_xv6_providers", {}).get(device_id)
            live = provider is not None
            if provider is None:
                dev = self.ctx.topology.devices[device_id]
                provider = DemoScheduler(
                    timeslice=int((dev.properties or {}).get("Timeslice", "1") or "1"))
            # a live bridge brings its own vm/fs readers; the demo feed lets MachineState default
            # them to DemoVm/DemoDisk.
            ms = MachineState(provider, device_id=device_id,
                              vm=getattr(provider, "vm", None),
                              fs=getattr(provider, "fs", None))
            ms.live = live
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
        self._machine_lab = MachineLab(
            self, self.theme, dev, state=ms, live=getattr(ms, "live", False),
            on_console=lambda: self._open_terminal(device_id))
        self._machine_lab.show()
        self._machine_lab.raise_()

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
        from .canvas import GroupItem, NodeItem
        has_sel = any(isinstance(i, (NodeItem, GroupItem))     # boxes (VPC/Subnet/Region) too
                      for i in self.canvas.scene_.selectedItems())
        busy = self._running or getattr(self, "_stopping", False)
        self._delete_act.setEnabled(has_sel and not busy)

    def _delete_selected(self) -> None:
        # delete selected CARDS *and* BOXES — GroupItems (VPC/Subnet/Region) were excluded, so a
        # selected VPC/Subnet left Delete + the trash button doing nothing.
        from .canvas import GroupItem, NodeItem
        ids = [i.inst.id for i in self.canvas.scene_.selectedItems()
               if isinstance(i, (NodeItem, GroupItem))]
        self._remove_devices(ids)

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
        self._update_status()

    def _on_log(self, level: str, message: str) -> None:
        tag = {"ok": "✓", "error": "✕", "chat": "›"}.get(level, "•")
        self.console.appendPlainText(f"{tag} {message}")

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

"""Application context + typed event bus.

Replaces the legacy global `mainWidgets` / `options` dictionaries. One AppContext
owns the topology and settings; components talk to each other through the typed
signal bus rather than reaching into global state by string key.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ..domain import Topology


class EventBus(QObject):
    """Typed cross-component signals."""
    topology_changed = Signal()
    device_added = Signal(str)        # device id
    device_removed = Signal(str)      # device id
    device_changed = Signal(str)      # device id (properties/position)
    device_resized = Signal(str)      # device id (size tier changed -> maybe live CPU update)
    link_added = Signal(str)          # link id
    link_removed = Signal(str)        # link id
    selection_changed = Signal(object)  # device id or None
    canvas_background_clicked = Signal()  # left-click on empty canvas (exit sticky modes)
    llm_reachable = Signal(str, bool)  # (model, reachable) — async LLM health probe result
    enrolment_changed = Signal(str, bool, int)  # (student, course-server online, missions due)
    boards_changed = Signal()         # a real GINI32 board came online or went quiet
    board_status_ready = Signal(object)  # relay snapshot from the poll worker (None = no answer)
    theme_changed = Signal(str)       # theme name
    log = Signal(str, str)            # level, message
    assistant_message = Signal(str, str)  # role, text
    run_state = Signal(bool, str)     # ok, message (from the orchestrator worker thread)
    mem_metrics = Signal(object)      # {"stats": {svc:{mem_used,..}}, "vm": mib, "t": s} — memory watchdog
    device_activated = Signal(str)    # device id (double-clicked -> open terminal/console)
    machine_events = Signal(str)      # xv6 device id — new teachable kernel events (proactive Coach)
    device_delete_requested = Signal(str)  # device id (right-click -> Delete)
    warning_explain_requested = Signal(str)  # device id (clicked its lint warning badge)
    device_logs_requested = Signal(str)      # device id (right-click -> View logs)
    device_console_requested = Signal(str)   # device id (right-click -> Open console in browser)
    function_invoke_requested = Signal(str, str, str)  # device id, method, body (Invoke a Function)
    function_invoke_result = Signal(str, str)          # device id, result text (back to inspector)
    rider_ran = Signal(str, object)   # rider device id, result dict {ok, raw, measurement, summary}
    function_deploy_requested = Signal()               # redeploy the faas runtime (AWS-style Deploy)
    runtime_status = Signal(object)   # {service: state} from the status poller
    mission_changed = Signal(object)  # Wizard objective (Mission) set/cleared -> guide the canvas
    wizard_ghosts_requested = Signal(str)     # device id -> resolve goal-relevant neighbours (LLM)
    wizard_ghosts_ready = Signal(str, object)  # device id, [(type_key, reason)] -> draw ghosts
    fabric_metrics = Signal(object)   # normalized cloud-fabric app metrics snapshot
    k8s_metrics = Signal(object)      # per-deployment kubernetes metrics snapshot
    addressing_changed = Signal()     # compiler-derived IP/MAC map refreshed
    warnings_changed = Signal()       # advisory topology-lint results refreshed
    mission_flags_changed = Signal()  # Mission move-legality flags refreshed -> red badges/glow
    focus_requested = Signal(object)  # device ids (or None = all) -> bring them into view
    edges_restyled = Signal()         # connector style (bent/straight) changed -> reroute edges
    # --- AI tutor "present" channel (the stage the AI draws on) ---
    present_spotlight = Signal(object)    # list[device_id] to spotlight, or None to clear
    present_highlight = Signal(object)    # list[device_id] to ring, or None to clear
    present_callout = Signal(str, str)    # device_id, text
    present_narrate = Signal(str)         # narration text
    present_packet = Signal(object)       # list[device_id] path to animate
    present_clear = Signal()              # clear all overlays


@dataclass
class Settings:
    theme: str = "dark"
    grid: bool = True
    connector_style: str = "orthogonal"   # "orthogonal" (bent, rounded) | "straight"
    snap_to_grid: bool = True
    show_minimap: bool = True
    flow_hud_window_s: int = 60     # Flow HUD: seconds of cwnd history shown (scrolling)
    # OS HUD: seconds of kernel events drawn. The kernel's rings are fixed-size circular buffers
    # (bounded memory), but each poll re-reports their WHOLE contents — so without a window on
    # this side the same events pile up on screen and never leave. Short is good here: a program
    # launch is over in microseconds, so 1-5s keeps one launch legible.
    os_hud_window_s: int = 10
    # How far back the OS HUD's scrub timeline reaches. Separate from the window above: the
    # window is how much is ON SCREEN, this is how much is RECORDED and can be scrubbed back to.
    os_hud_scrub_s: int = 120
    autosave: bool = False
    server: str = "localhost"
    remote_port: int = 10000
    local_port: int = 10001
    # accessibility / motion
    reduced_motion: bool = False
    text_size: str = "Normal"              # UI font scale: Normal | Large | Extra Large
    high_contrast: bool = False
    sound: bool = False
    tutor_mode: bool = False
    # onboarding: the Cue Cards feature tour
    show_help_on_launch: bool = True
    # self-hosted LLM (Ollama by default)
    llm_enabled: bool = False
    llm_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1"
    llm_think: bool = False          # ask reasoning models (e.g. gemma4:e2b) to think
    llm_num_ctx: int = 8192          # context window (Ollama defaults to only 2048 + truncates)
    # Reasoning 2.0: the deterministic Reasoning Twin audits mission-tutor turns for coverage
    # (docs/REASONING_2.0_DESIGN.md). Off by default while phase A proves out.
    twin_enabled: bool = False
    # Build a missing backend image (gRouter / POX) automatically instead of printing the
    # `docker build …` line and refusing to Run. Off by default: the first build takes a couple
    # of minutes, and silently burning that on someone's first Run is a poor surprise.
    autobuild_images: bool = False
    # backend: run the lab on the LOCAL Docker daemon, or a remote brokered GINI server
    # (a Kata-enabled Linux host reached over an authenticated API — see gini/server/).
    backend: str = "local"               # "local" | "gini-server"
    gini_server_host: str = ""           # the GINI server host (for "gini-server")
    gini_server_port: int = 10000
    gini_server_user: str = ""           # username; password is entered at connect, never stored
    # Teaching Center (the course server): released lessons, profile sync, submissions.
    # Empty url = not enrolled -> Missions falls back to the local practice catalog.
    tc_url: str = ""                     # e.g. https://gini.cs.example.edu (HTTPS only)
    tc_course: str = ""                  # e.g. cs4480-fall26
    tc_student: str = ""                 # the student's id in the course
    tc_token: str = ""                   # one-time ENROLMENT token (spent when you claim the account)
    # Passwords are never stored — a session token lives in the cache instead. This flag is the
    # conscious override that lets a password go over plain HTTP to a remote host (demos only).
    # auto-internet: every container gets a default eth to the internet (Docker NAT).
    # Off = "faithful mode": no internet unless an Internet element is drawn + wired.
    auto_internet: bool = False
    # GINI32 hardware: the lab Wi-Fi a board joins to reach this machine. It is the
    # same network for every board in a class, so it is remembered once here and
    # then written to each board over USB by Hardware > Set Up a Board. The password
    # is stored because a board cannot be set up without it and there is nowhere else
    # to keep it; it is the shared lab network, not a personal credential.
    board_wifi_ssid: str = ""
    board_wifi_password: str = ""
    # Boards this laptop has set up over USB. Kept so the canvas can OFFER an id
    # instead of asking the student to retype one from memory — a mistyped BoardID
    # produces a board that is online and healthy but invisible to the topology,
    # which is the single most confusing failure in the whole GINI32 story.
    known_boards: list = field(default_factory=list)
    # --- GINI32 hardware ---------------------------------------------------- #
    # This install's identity to a board. A board records the id of the laptop that
    # claimed it and then ignores every other laptop, which is what stops thirty
    # students in one room from stealing each other's hardware. Minted on first use.
    laptop_id: str = ""
    # Boards this laptop has claimed: board name -> {mac, claimed_at}. Deliberately a
    # SETTING, not part of a topology: opening a colleague's .gini file must not hand
    # you their hardware.
    claimed_boards: dict[str, dict] = field(default_factory=dict)
    # per-type auto-name prefix overrides, e.g. {"host": "Mach_"} -> Mach_1, Mach_2, …
    name_prefixes: dict[str, str] = field(default_factory=dict)
    # per-type GINI $/hr rental price overrides for the cost dashboard, e.g. {"database": 20}
    prices: dict[str, float] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)


class AppContext:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.settings = Settings()
        self.topology = Topology("untitled")
        self.selected_id: str | None = None
        self.addressing: dict[str, dict] = {}   # device name -> {interfaces:[…]}
        self.warnings: dict[str, list] = {}     # device name -> [lint messages]
        self.mission_flags: dict[str, str] = {}  # device id -> reason (Mission off-task / bad-link)
        self.mission = None                     # active Wizard objective (domain.missions.Mission)
        self.teaching_center = None             # agent.teaching_center.TeachingCenterClient | None
        # Staff session for marking (services/tc_staff). IN MEMORY ONLY, for the life of the
        # window: the password is never stored, the server expires the token after twelve hours,
        # and a marker on a shared machine leaves nothing behind.
        self.staff_session = ""
        self.staff_who = ""
        self.staff_role = ""
        self.orchestrator = None                # services.orchestrator.Orchestrator (probes exec here)
        # live xv6 kernel state per Machine (domain.machine_state.MachineState) — the bridge the
        # Machine Lab renders from and the Ask GINI agent reads for OS help. device_id -> state.
        self.machine_states: dict = {}
        # last run of each Source/Sink rider: device_id -> result dict (measurement etc.)
        self.rider_results: dict = {}
        self._rider_sessions = None             # services.rider_session.RiderSessions (lazy)
        # xv6 riders run over the console, not docker — MainWindow (which owns the live xv6 bridges)
        # registers these hooks so toggle/is_running route to the serial path for qemu-serial riders.
        self.xv6_rider_toggle = None            # callable(rider_id) -> result dict
        self.xv6_rider_running = None           # callable(rider_id) -> bool

    # convenience wrappers that emit the right events ----------------------- #
    def add_device(self, type_key: str, x: float = 0.0, y: float = 0.0, **kw):
        inst = self.topology.add_device(type_key, x=x, y=y, **kw)
        self.bus.device_added.emit(inst.id)
        self.bus.topology_changed.emit()
        return inst

    def add_link(self, source_id: str, target_id: str, label: str = ""):
        # xv6 has no networking: hard-block xv6<->non-peripheral (and peripheral<->non-xv6) links.
        from ..domain.connection_rules import link_blocked
        s, t = self.topology.devices.get(source_id), self.topology.devices.get(target_id)
        if s is not None and t is not None:
            reason = link_blocked(s.type_key, t.type_key)
            if reason:
                self.log(reason, "info")
                raise ValueError(reason)
        link = self.topology.add_link(source_id, target_id, label)
        self.bus.link_added.emit(link.id)
        self.bus.topology_changed.emit()
        return link

    def connect(self, a_id: str, b_id: str, label: str = ""):
        """Wire two elements the right way: if either end is a Source/Sink rider, mount it on the
        other with an ATTACH edge; otherwise a normal network link. The canvas calls this so the
        teacher never has to know which kind an edge is — the grammar decides."""
        from ..domain.connection_rules import is_rider
        a = self.topology.devices.get(a_id)
        b = self.topology.devices.get(b_id)
        if a is not None and b is not None and (is_rider(a.type_key) or is_rider(b.type_key)):
            rider_id, donor_id = (a_id, b_id) if is_rider(a.type_key) else (b_id, a_id)
            return self.add_attach(rider_id, donor_id, label)
        return self.add_link(a_id, b_id, label)

    def add_attach(self, rider_id: str, donor_id: str, label: str = ""):
        """Mount a Source/Sink rider onto its donor (a dotted, non-network edge)."""
        from ..domain.connection_rules import attach_blocked
        r, d = self.topology.devices.get(rider_id), self.topology.devices.get(donor_id)
        if r is not None and d is not None:
            reason = attach_blocked(r.type_key, d.type_key)
            if reason:
                self.log(reason, "info")
                raise ValueError(reason)
        link = self.topology.add_attach(rider_id, donor_id, label)
        self.bus.link_added.emit(link.id)
        self.bus.topology_changed.emit()
        return link

    def run_rider(self, rider_id: str) -> dict:
        """Execute a Source/Sink on its donor, stream the raw output to the console, remember the
        measurement, and emit `rider_ran`. Safe to call from a worker thread (it only touches the
        bus, whose signals marshal to the GUI). Returns the result dict."""
        from ..domain.connection_rules import is_rider
        dev = self.topology.devices.get(rider_id)
        if dev is None or not is_rider(dev.type_key):
            return {"ok": False, "error": "not a Source/Sink"}
        if self.orchestrator is None:
            res = {"ok": False, "error": "Press Run to start the topology, then run this."}
        else:
            from ..services.rider_runner import RiderRunner
            runner = RiderRunner(self.orchestrator)
            if not runner.available():
                res = {"ok": False, "error": "The topology isn't running — press Run first."}
            else:
                res = runner.run(self.topology, rider_id)

        # ONE concise line in the shared console (the full raw stream + measurement go to the
        # inspector's Live tab, scoped to this node, so the console stays readable).
        if res.get("ok"):
            self.rider_results[rider_id] = res
            tail = res.get("summary", "")
            if res.get("inferred_target"):
                tail += f"  (→ {res['inferred_target']})"
            self.log(f"{dev.name} on {res.get('donor', '?')} → {tail}", "info")
        else:
            self.log(f"{dev.name}: {res.get('error', 'run failed')}", "info")
        self.bus.rider_ran.emit(rider_id, res)
        return res

    # -- continuous Source/Sink sessions (double-click toggles start/stop) --- #
    def _sessions(self):
        if self._rider_sessions is None and self.orchestrator is not None:
            from ..services.rider_session import RiderSessions
            self._rider_sessions = RiderSessions(self.orchestrator)
        return self._rider_sessions

    def _is_xv6_rider(self, rider_id: str) -> bool:
        dev = self.topology.devices.get(rider_id)
        return bool(dev and getattr(dev.type, "driver", "") == "qemu-serial")

    def is_rider_running(self, rider_id: str) -> bool:
        if self._is_xv6_rider(rider_id):
            return bool(self.xv6_rider_running and self.xv6_rider_running(rider_id))
        s = self._rider_sessions
        return bool(s and s.is_running(rider_id))

    def toggle_rider(self, rider_id: str) -> dict:
        """Double-click behaviour: start the rider if idle, stop it if running. xv6 riders route to
        the console (serial) path; everything else to the docker session path."""
        if self._is_xv6_rider(rider_id):
            if self.xv6_rider_toggle is not None:
                return self.xv6_rider_toggle(rider_id)
            return {"ok": False, "error": "Run the xv6 Machine first."}
        if self.is_rider_running(rider_id):
            return self.stop_rider(rider_id)
        return self.start_rider(rider_id)

    def start_rider(self, rider_id: str) -> dict:
        from ..domain.connection_rules import is_rider
        dev = self.topology.devices.get(rider_id)
        if dev is None or not is_rider(dev.type_key):
            return {"ok": False, "error": "not a Source/Sink"}
        sess = self._sessions()
        if sess is None or not sess.available():
            res = {"ok": False, "error": "Press Run to start the topology, then start this."}
            self.log(f"{dev.name}: {res['error']}", "info")
            self.bus.rider_ran.emit(rider_id, {"ok": False, "running": False, **res})
            return res
        res = sess.start(self.topology, rider_id, self._on_rider_update)
        if res.get("ok"):
            tail = f" (→ {res['inferred_target']})" if res.get("inferred_target") else ""
            self.log(f"{dev.name} started on {res.get('donor', '?')}{tail}", "info")
        else:
            self.log(f"{dev.name}: {res.get('error', 'could not start')}", "info")
            self.bus.rider_ran.emit(rider_id, {"ok": False, "running": False, **res})
        return res

    def stop_rider(self, rider_id: str) -> dict:
        sess = self._rider_sessions
        dev = self.topology.devices.get(rider_id)
        if sess is not None:
            sess.stop(rider_id)
        if dev is not None:
            self.log(f"{dev.name} stopped", "info")
        return {"ok": True, "running": False}

    def stop_all_riders(self) -> None:
        if self._rider_sessions is not None:
            self._rider_sessions.stop_all()

    def _on_rider_update(self, rider_id: str, snapshot: dict) -> None:
        """Called from the session reader thread on each new line (and at stop). Store + emit; the
        Qt signal marshals to the GUI thread, so touching the bus here is safe."""
        self.rider_results[rider_id] = snapshot
        self.bus.rider_ran.emit(rider_id, snapshot)

    def remove_device(self, device_id: str) -> None:
        if self.is_rider_running(device_id):      # never orphan a running rider on delete
            self.stop_rider(device_id)
        self.topology.remove_device(device_id)
        if self.selected_id == device_id:
            self.select(None)
        self.bus.device_removed.emit(device_id)
        self.bus.topology_changed.emit()

    def remove_link(self, link_id: str) -> None:
        self.topology.remove_link(link_id)
        self.bus.link_removed.emit(link_id)
        self.bus.topology_changed.emit()

    def clear_topology(self) -> int:
        """Wipe the board (used by staged missions, which must start from an exactly-known canvas).
        Goes through remove_device so every view tears its node down the normal way. Returns how
        many devices were removed."""
        ids = list(self.topology.devices)
        for did in ids:
            self.remove_device(did)
        self.topology.manual_addressing = False     # a fresh board is auto-addressed again
        self.addressing.clear()
        self.warnings.clear()
        self.mission_flags.clear()
        return len(ids)

    def select(self, device_id: str | None) -> None:
        self.selected_id = device_id
        self.bus.selection_changed.emit(device_id)

    # -- Teaching Center ----------------------------------------------------- #
    def connect_teaching_center(self):
        """(Re)build the Teaching Center client from Settings. Returns the client, or None when the
        student isn't enrolled (no URL) — in which case Missions shows the local practice catalog.
        Never raises: an unreachable Center degrades to the offline cache."""
        s = self.settings
        if not (s.tc_url and s.tc_course and s.tc_student):
            self.teaching_center = None
            # Half-configured is a trap (the Settings placeholders LOOK like values), so say which
            # fields are actually missing instead of silently falling back to the local catalog.
            given = any((s.tc_url, s.tc_course, s.tc_student, s.tc_token))
            if given:
                missing = [n for n, v in (("course server", s.tc_url), ("course", s.tc_course),
                                          ("student id", s.tc_student)) if not v]
                self.log("Teaching Center not connected — still missing: "
                         + ", ".join(missing) + " (Settings → Teaching Center).", "info")
            return None
        try:
            from ..agent.teaching_center import TeachingCenterClient
            self.teaching_center = TeachingCenterClient(
                s.tc_url, course=s.tc_course, student_id=s.tc_student, token=s.tc_token)
        except Exception as e:                            # noqa: BLE001
            self.log(f"Teaching Center: {e}", "info")
            self.teaching_center = None
        return self.teaching_center

    def set_mission(self, mission) -> None:
        """Set (or clear, with None) the Wizard objective and notify the canvas/palette."""
        self.mission = mission
        self.bus.mission_changed.emit(mission)

    def log(self, message: str, level: str = "info") -> None:
        self.bus.log.emit(level, message)

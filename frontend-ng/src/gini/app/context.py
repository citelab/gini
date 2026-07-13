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
    selection_changed = Signal(object)  # device id or None
    canvas_background_clicked = Signal()  # left-click on empty canvas (exit sticky modes)
    llm_reachable = Signal(str, bool)  # (model, reachable) — async LLM health probe result
    theme_changed = Signal(str)       # theme name
    log = Signal(str, str)            # level, message
    assistant_message = Signal(str, str)  # role, text
    run_state = Signal(bool, str)     # ok, message (from the orchestrator worker thread)
    device_activated = Signal(str)    # device id (double-clicked -> open terminal/console)
    machine_events = Signal(str)      # xv6 device id — new teachable kernel events (proactive Coach)
    device_delete_requested = Signal(str)  # device id (right-click -> Delete)
    warning_explain_requested = Signal(str)  # device id (clicked its lint warning badge)
    device_logs_requested = Signal(str)      # device id (right-click -> View logs)
    device_console_requested = Signal(str)   # device id (right-click -> Open console in browser)
    function_invoke_requested = Signal(str, str, str)  # device id, method, body (Invoke a Function)
    function_invoke_result = Signal(str, str)          # device id, result text (back to inspector)
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
    autosave: bool = False
    server: str = "localhost"
    remote_port: int = 10000
    local_port: int = 10001
    # accessibility / motion
    reduced_motion: bool = False
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
    # backend: run the lab on the LOCAL Docker daemon, or a remote brokered GINI server
    # (a Kata-enabled Linux host reached over an authenticated API — see gini/server/).
    backend: str = "local"               # "local" | "gini-server"
    gini_server_host: str = ""           # the GINI server host (for "gini-server")
    gini_server_port: int = 10000
    gini_server_user: str = ""           # username; password is entered at connect, never stored
    # auto-internet: every container gets a default eth to the internet (Docker NAT).
    # Off = "faithful mode": no internet unless an Internet element is drawn + wired.
    auto_internet: bool = False
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
        # live xv6 kernel state per Machine (domain.machine_state.MachineState) — the bridge the
        # Machine Lab renders from and the Ask GINI agent reads for OS help. device_id -> state.
        self.machine_states: dict = {}

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

    def remove_device(self, device_id: str) -> None:
        self.topology.remove_device(device_id)
        if self.selected_id == device_id:
            self.select(None)
        self.bus.device_removed.emit(device_id)
        self.bus.topology_changed.emit()

    def select(self, device_id: str | None) -> None:
        self.selected_id = device_id
        self.bus.selection_changed.emit(device_id)

    def set_mission(self, mission) -> None:
        """Set (or clear, with None) the Wizard objective and notify the canvas/palette."""
        self.mission = mission
        self.bus.mission_changed.emit(mission)

    def log(self, message: str, level: str = "info") -> None:
        self.bus.log.emit(level, message)

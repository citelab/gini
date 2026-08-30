"""The recorder: turns gBuilder's live signals into chain entries.

Sits between the app bus and `domain.proof`. It owns the armed/unarmed state, the caches that let
a *removal* still know what was removed, and the promise that recording can never take the app
down with it.

Deliberately free of Qt. It calls `signal.connect(handler)` on whatever bus it is handed and
touches nothing else, so the whole recorder — subscriptions included — is unit-testable against a
fake bus with no PySide6 installed. That is the same seam `services/probe_runner.py` uses for
Docker.

**Nothing here may raise into a bus handler.** An exception in a slot lands in the Qt event loop,
and a student who loses their session because the *provenance* feature threw is worse off than one
with no provenance at all. Every handler is guarded; a broken recorder degrades to a silent one
and says so once in the console.
"""
from __future__ import annotations

import threading
import time

from ..domain import proof as _proof
from ..domain import proof_events as ev
from ..domain import ticket as _ticket

# Signals the recorder subscribes to. Kept as names rather than attributes so a bus that predates
# one of them (or a fake bus in a test) simply skips it instead of failing to construct.
_SUBSCRIPTIONS = (
    ("device_added", "_on_device_added"),
    ("device_removed", "_on_device_removed"),
    ("device_changed", "_on_device_changed"),
    ("link_added", "_on_link_added"),
    ("link_removed", "_on_link_removed"),
    ("run_state", "_on_run_state"),
    ("device_activated", "_on_device_activated"),
    ("rider_ran", "_on_rider_ran"),
    ("function_invoke_result", "_on_invoke_result"),
)

# A continuous Source/Sink session re-emits `rider_ran` on every line it reads. Recording each one
# would bury the chain in a single ping's output, so a *running* rider contributes at most one
# entry per this many seconds; the final snapshot (running False) is always recorded, because that
# one carries the measurement an instructor would actually read.
_MEASURE_MIN_GAP = 30.0

# How many recording failures to report before going quiet. One tells the student something is
# wrong; thirty during a drag would be its own outage.
_MAX_COMPLAINTS = 3


def gini_version() -> str:
    # From gini.version, not `gini.__init__`: `gini` is a namespace package shared with gini-core,
    # so it has no __init__ to hold a version any more.
    try:
        from ..version import gini_version as _v
        return _v()
    except Exception:                       # noqa: BLE001 — a version is a nicety, never a blocker
        return ""


class ProofRecorder:
    """Records a student's work into a hash chain keyed by their assignment code.

    Unarmed it is inert: the handlers stay connected but return immediately, so no code entered
    means no chain and no proof. That has to be *visible* in the UI (see ui/proof_strip.py) —
    a student must never do three hours of work and only then discover nothing was recorded.
    """

    def __init__(self, ctx, *, store=None, on_change=None, now=None) -> None:
        self.ctx = ctx
        self.store = store if store is not None else _proof.ChainStore()
        self._on_change = on_change            # called after every state change (UI repaint)
        self._now = now or time.time
        self._chain: _proof.Chain | None = None
        self._ticket: _ticket.Ticket | None = None
        # WHICH lab this code is for ("course/lab"), as the Teaching Center reported it at arm
        # time. The strip has always received it and only ever printed it; keeping it is what lets
        # the tutor know which of a course's activities the student is actually being marked on.
        self._activity = ""
        self._activity_title = ""
        # Not every signal reaches us on the GUI thread. `rider_ran` is emitted from a rider's
        # reader thread, and Qt delivers to a plain (non-QObject) slot directly in the emitting
        # thread — so two appends really can race, and an interleaved append would compute `prev`
        # from the wrong head and break the chain for good.
        self._lock = threading.Lock()
        self._complaints = 0
        self.last_error = ""
        # Caches: by the time `device_removed` / `link_removed` fire, the thing is already gone
        # from the topology, so the chain would have nothing but an id to record. These keep the
        # names and types the narration needs.
        self._devices: dict[str, tuple[str, str]] = {}      # id -> (name, type_key)
        self._props: dict[str, dict] = {}                   # id -> last seen properties
        self._links: dict[str, tuple[str, str]] = {}        # link id -> (a name, b name)
        self._last_measure: dict[str, float] = {}           # rider id -> when last recorded
        self._obj_status: dict[str, str] = {}               # objective id -> last seen status
        self._objectives: list = []                         # last results, for the submit entry
        # Opening a project REPLAYS `device_added`/`link_added` for everything in the file (see
        # MainWindow._set_topology). Left alone, an import would therefore write a perfect fake
        # construction sequence — the exact thing this feature exists to detect. `note_load` names
        # the ids that arrived in the file, and the handlers below record them as caches only.
        self._loaded_devices: set[str] = set()
        self._loaded_links: set[str] = set()

    def set_on_change(self, callback) -> None:
        """Who to tell when the chain grows. The UI is built after the recorder (it needs one to
        display), so the callback is set rather than passed in."""
        self._on_change = callback

    # -- state -------------------------------------------------------------- #
    @property
    def armed(self) -> bool:
        return self._chain is not None

    @property
    def ticket(self) -> _ticket.Ticket | None:
        return self._ticket

    @property
    def count(self) -> int:
        """Entries recorded so far. Genesis is not an *event*, so it does not count — a student
        who has done nothing should see 0, not 1."""
        return max(0, len(self._chain) - 1) if self._chain else 0

    def status(self) -> dict:
        """Everything the dashboard strip needs, in one plain dict (no Qt types)."""
        return {"armed": self.armed,
                "ticket": self._ticket.pretty if self._ticket else "",
                "short": self._ticket.short if self._ticket else "",
                "count": self.count,
                "submitted": bool(self._chain and self._chain.has_submitted()),
                "activity": self._activity,
                "activity_title": self._activity_title,
                "error": self.last_error}

    # -- arming ------------------------------------------------------------- #
    def arm(self, code: str, assignment: str = "") -> tuple[bool, str]:
        """Validate a code and start (or resume) its chain. Returns (ok, message to show)."""
        try:
            tk = _ticket.parse(code)
        except _ticket.TicketError as e:
            return False, str(e)
        try:
            chain = self.store.load(tk.code)
        except _proof.ChainError as e:
            # Never quietly start a second chain over a damaged one: that would discard whatever
            # work the first chain still holds, which is the opposite of this feature's job.
            return False, (f"There is already work recorded under {tk.pretty}, and its chain "
                           f"cannot be read ({e}). Ask your instructor before recording again — "
                           f"do not delete it.")
        fresh = chain is None
        if fresh:
            chain = _proof.Chain.start(
                tk.code, assignment=assignment or self._assignment(),
                gini_version=gini_version())
            self.store.write_chain(tk.code, chain)
        self._chain, self._ticket = chain, tk
        self._snapshot()
        if fresh:
            # Say what the canvas already held, at the moment it was armed. A student who builds
            # first and arms afterwards has not cheated, but the chain cannot claim to have
            # watched that work — so it states the truth rather than implying otherwise.
            self._record(ev.preexisting(self._topology_dict()))
        else:
            # Picking a code back up joins one session to another with a hole between them, and
            # the hole belongs in the chain. `away` is measured from the last entry, which is the
            # `stopped` one when the student cancelled from inside gBuilder.
            head = chain.head
            away = max(0.0, self._now() - float(head.t)) if head else 0.0
            self._record(ev.resumed(self._topology_dict(), away=away))
        self.last_error = ""
        self._changed()
        n = self.count
        return True, (f"Recording under {tk.pretty}." if fresh else
                      f"Resumed recording under {tk.pretty} — {n} event(s) already in the chain.")

    def note_activity(self, activity: str, title: str = "") -> None:
        """Remember which lab the armed code belongs to, from the course server's arm reply.

        Set from OUTSIDE, because the recorder never talks to the network — a code is
        self-verifying, so a student with no connection still records perfectly well and simply
        has no activity to name.
        """
        self._activity = str(activity or "")
        self._activity_title = str(title or "")

    def cancel(self) -> None:
        """Leave recording mode. The chain stays on disk; entering the same code resumes it.

        Recording used to be a one-way door — nothing disarmed, not even generating a proof — so a
        student who armed the wrong code had to restart gBuilder. It was then a *pause*, with the
        code held and offered back on a button, which made leaving the mode only half a departure:
        the strip still named the code, and gBuilder still behaved as though the session were
        merely suspended.

        Cancel is the whole departure. Nothing is remembered in memory and nothing about the last
        code is shown afterwards. Nothing is LOST either — the chain is on disk under its code, and
        typing that code again appends to it — so a student is never trading their morning's work
        for the ability to stop being recorded.

        The last thing written is that recording stopped, because a gap the chain does not mention
        is a gap nobody can weigh.
        """
        if self._chain is not None:
            self._record(ev.stopped(self._topology_dict()))
        self._chain = None
        self._ticket = None
        self._activity = self._activity_title = ""
        self._changed()


    def _assignment(self) -> str:
        """What this chain is *for*, when the caller does not say.

        Phase 1 has no assignment registry, so the current experiment's name is the honest answer:
        it is what the student called the work, it appears in the narration, and — because it is
        inside the genesis entry — it cannot be edited afterwards to claim a different lab.
        """
        return str(getattr(getattr(self.ctx, "topology", None), "name", "") or "")

    # -- bus wiring --------------------------------------------------------- #
    def attach(self, bus=None) -> None:
        """Subscribe to the bus. Safe to call once at start-up whether or not anything is armed —
        the handlers return immediately while unarmed, so there is no state to toggle later."""
        bus = bus if bus is not None else getattr(self.ctx, "bus", None)
        if bus is None:
            return
        for signal_name, handler in _SUBSCRIPTIONS:
            sig = getattr(bus, signal_name, None)
            if sig is not None and hasattr(sig, "connect"):
                sig.connect(getattr(self, handler))

    # -- recording ---------------------------------------------------------- #
    def _record(self, built) -> None:
        """Append one (kind, data) pair. `built` may be None — the event builders return None for
        a signal that turned out to carry nothing worth recording (a bare drag, say)."""
        if built is None or self._chain is None or self._ticket is None:
            return
        kind, data = built
        with self._lock:
            entry = self._chain.append(kind, data)
            self.store.append(self._ticket.code, entry)
        self._changed()

    def _changed(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception as e:                              # noqa: BLE001
            self._complain(e)

    def _complain(self, exc: Exception) -> None:
        self.last_error = str(exc)
        self._complaints += 1
        if self._complaints > _MAX_COMPLAINTS:
            return
        log = getattr(self.ctx, "log", None)
        if callable(log):
            try:
                log(f"Proof recording hit a problem and skipped an event: {exc}", "warn")
            except Exception:                               # noqa: BLE001
                pass

    def _guard(self, fn, *args) -> None:
        """Run a handler and swallow anything it throws (see the module docstring)."""
        if self._chain is None:
            return
        try:
            fn(*args)
        except Exception as e:                              # noqa: BLE001
            self._complain(e)

    # -- caches ------------------------------------------------------------- #
    def _topology(self):
        return getattr(self.ctx, "topology", None)

    def _topology_dict(self) -> dict:
        t = self._topology()
        return t.to_dict() if t is not None else {}

    def _snapshot(self) -> None:
        """Refresh the name/property caches from the live topology (on arm, and after a load)."""
        self._devices.clear()
        self._props.clear()
        self._links.clear()
        t = self._topology()
        if t is None:
            return
        for d in getattr(t, "devices", {}).values():
            self._devices[d.id] = (d.name, d.type_key)
            self._props[d.id] = dict(getattr(d, "properties", {}) or {})
        for l in getattr(t, "links", {}).values():
            self._links[l.id] = self._endpoint_names(l)

    def _endpoint_names(self, link) -> tuple[str, str]:
        t = self._topology()
        devs = getattr(t, "devices", {}) if t is not None else {}
        a = devs.get(link.source_id)
        b = devs.get(link.target_id)
        return (a.name if a is not None else link.source_id,
                b.name if b is not None else link.target_id)

    # -- Tier 1: construction ----------------------------------------------- #
    def _on_device_added(self, device_id) -> None:
        self._guard(self._device_added, device_id)

    def _device_added(self, device_id) -> None:
        d = getattr(self._topology(), "devices", {}).get(device_id)
        if d is None:
            return
        self._devices[d.id] = (d.name, d.type_key)
        self._props[d.id] = dict(getattr(d, "properties", {}) or {})
        if d.id in self._loaded_devices:
            self._loaded_devices.discard(d.id)      # it came from a file; the `load` entry said so
            return
        self._record(ev.place(d.id, d.type_key, d.name))

    def _on_device_removed(self, device_id) -> None:
        self._guard(self._device_removed, device_id)

    def _device_removed(self, device_id) -> None:
        name, _type = self._devices.pop(device_id, (str(device_id), ""))
        self._props.pop(device_id, None)
        # Removing a device drops its links without a link_removed signal; forget them here or the
        # cache would answer for cables that no longer exist.
        for lid in [k for k, _ in list(self._links.items())
                    if k not in getattr(self._topology(), "links", {})]:
            self._links.pop(lid, None)
        self._record(ev.remove(device_id, name))

    def _on_device_changed(self, device_id) -> None:
        self._guard(self._device_changed, device_id)

    def _device_changed(self, device_id) -> None:
        d = getattr(self._topology(), "devices", {}).get(device_id)
        if d is None:
            return
        after = dict(getattr(d, "properties", {}) or {})
        changes = ev.diff_properties(self._props.get(device_id, {}), after)
        self._props[device_id] = after
        self._devices[device_id] = (d.name, d.type_key)
        self._record(ev.configure(device_id, d.name, changes))

    def _on_link_added(self, link_id) -> None:
        self._guard(self._link_added, link_id)

    def _link_added(self, link_id) -> None:
        t = self._topology()
        link = getattr(t, "links", {}).get(link_id)
        if link is None:
            return
        devs = getattr(t, "devices", {})
        a, b = devs.get(link.source_id), devs.get(link.target_id)
        names = self._endpoint_names(link)
        self._links[link_id] = names
        if link_id in self._loaded_links:
            self._loaded_links.discard(link_id)
            return
        self._record(ev.connect(names[0], a.type_key if a else "",
                                names[1], b.type_key if b else "",
                                kind=getattr(link, "kind", "link")))

    def _on_link_removed(self, link_id) -> None:
        self._guard(self._link_removed, link_id)

    def _link_removed(self, link_id) -> None:
        a, b = self._links.pop(link_id, (str(link_id), ""))
        self._record(ev.disconnect(a, b))

    # -- Tier 2: operation --------------------------------------------------- #
    def _on_run_state(self, ok, message="") -> None:
        self._guard(self._run_state, ok, message)

    def _run_state(self, ok, message) -> None:
        self._record(ev.run(bool(ok), str(message or "")))

    def note_stop(self, message: str = "") -> None:
        """Record that the lab was stopped.

        Not wired to a signal: the only thing that reports a stop is `runtime_status`, which the
        design excludes from the chain (it is a poller, and would append on every tick). Exposed
        as a call so whoever owns the Stop action can record the fact in one line.
        """
        self._guard(lambda: self._record(ev.stop(message)))

    def _on_device_activated(self, device_id) -> None:
        self._guard(self._device_activated, device_id)

    def _device_activated(self, device_id) -> None:
        name, _type = self._devices.get(device_id, (str(device_id), ""))
        self._record(ev.open_console(device_id, name))

    def _on_rider_ran(self, rider_id, result) -> None:
        self._guard(self._rider_ran, rider_id, result)

    def _rider_ran(self, rider_id, result) -> None:
        result = dict(result or {})
        running = bool(result.get("running"))
        now = self._now()
        if running and now - self._last_measure.get(rider_id, 0.0) < _MEASURE_MIN_GAP:
            return                                  # see _MEASURE_MIN_GAP
        self._last_measure[rider_id] = now
        name, _type = self._devices.get(rider_id, (str(rider_id), ""))
        self._record(ev.measure(name, result))

    def _on_invoke_result(self, device_id, result_text="") -> None:
        self._guard(lambda: self._record(ev.invoke(device_id, result_text)))

    # -- Tier 3: witnessed --------------------------------------------------- #
    def note_command(self, device: str, cmd: str, out=None) -> None:
        """Record a command run in gBuilder's terminal on a lab element, and what it printed.

        Called from the Terminal panel, which assembles it (services/console_tap). Off the GUI
        thread is fine — `_guard` takes the lock, like every other entry point here.

        This is the evidence the report used to say was missing: a chain could read "Started the
        lab" and then "Witnessed on the running network — none" while the student had in fact
        pinged across their subnets and watched it work.
        """
        cmd = (cmd or "").strip()
        if not cmd:
            return
        self._guard(self._note_command, str(device or ""), cmd, list(out or []))

    def _note_command(self, device: str, cmd: str, out: list) -> None:
        self._record(ev.command(device, cmd, out))

    def note_check(self, results, objectives=None) -> None:
        """Record what GINI measured when the student pressed Run / Check.

        `results` are `objectives.ObjectiveResult`s; `objectives` are the authored `Objective`s,
        supplied so a behavioural result can be recorded as the *probe string* it came from — the
        probe is the fact GINI measured, the objective is only the label on it.

        Objective entries record **transitions**, not states: a chain that logged nine "still
        unmet" lines on every press would be unreadable, and the interesting moment is the one
        where something started working.
        """
        self._guard(self._note_check, list(results or []), list(objectives or []))

    def _note_check(self, results, objectives) -> None:
        probes = {getattr(o, "id", ""): getattr(o, "probe", "") for o in objectives}
        verdict = {"met": "ok", "unmet": "fail", "pending": "pending"}
        for r in results:
            rid = getattr(r, "id", "")
            status = getattr(r, "status", "")
            if getattr(r, "kind", "") == "behavioral":
                probe = probes.get(rid) or rid
                self._record(ev.witness(probe, verdict.get(status, status)))
            before = self._obj_status.get(rid)
            if before != status:
                self._obj_status[rid] = status
                self._record(ev.objective(rid, getattr(r, "say", ""), before or "new", status))
        self._objectives = list(results)

    # -- provenance ---------------------------------------------------------- #
    def note_load(self, source: str, topology=None) -> None:
        """Record that a topology arrived from a file.

        This is the entry that makes an imported `.gini` visible for what it is: the narration
        shows one import rather than a construction sequence, and every element that came in with
        it is excluded from "built under this code" when the submission is accounted for.

        **Call this with the loaded topology BEFORE installing it on the canvas.** Installing it
        replays `device_added`/`link_added` for every element in the file; this call is what tells
        the recorder those replays are an import and not thirty seconds of very fast building.
        """
        self._guard(self._note_load, source, topology)

    def _note_load(self, source, topology) -> None:
        loaded = topology if topology is not None else self._topology()
        data = loaded.to_dict() if loaded is not None else {}
        self._record(ev.load(source, data))
        self._loaded_devices = {str(d.get("id", "")) for d in data.get("devices", []) or []}
        self._loaded_links = {str(l.get("id", "")) for l in data.get("links", []) or []}
        if loaded is self._topology():
            # Called after the swap (the ids' add-signals have already been and gone): the caches
            # are the only thing left to fix up, and there is nothing to suppress.
            self._snapshot()
            self._loaded_devices.clear()
            self._loaded_links.clear()

    # -- submission ---------------------------------------------------------- #
    def generate_proof(self, objectives=None) -> dict:
        """Append the `submit` entry and write the proof file.

        Returns a plain dict — `{ok, message, path, receipt, proof}` — rather than raising, so the
        dashboard button has one thing to render whatever happened.
        """
        if self._chain is None or self._ticket is None:
            return {"ok": False, "message": "Enter your assignment code first — nothing is being "
                                            "recorded, so there is nothing to prove."}
        try:
            # Captured ONCE and returned, not re-read by the caller. A second `_topology_dict()`
            # after the student nudges a node would hash differently from the one the chain
            # committed to, and the server would rightly refuse the upload as not matching.
            topology = self._topology_dict()
            artifact = _proof.artifact_summary(topology)
            rows = objectives if objectives is not None else self._objectives
            self._record(ev.submit(artifact, rows))
            proof = _proof.build_proof(self._chain, gini_version())
            path = self.store.write_proof(self._ticket.code, proof)
        except Exception as e:                              # noqa: BLE001
            self._complain(e)
            return {"ok": False, "message": f"Could not write the proof: {e}"}
        return {"ok": True, "path": str(path), "proof": proof, "topology": topology,
                "receipt": _proof.receipt_code(proof),
                "message": f"Proof written to {path}"}

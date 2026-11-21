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

**A recording is paused or finished, never cancelled.** Students asked to be able to stop in the
middle of a lab and carry on another day, and the chain always allowed it — it lives on disk under
its code, and arming the code again appends to it. What was missing was the word: the button said
*Cancel*, which to a student means "throw it away". So the two ways out of recording are now
`pause()`, which writes a `stopped` mark and keeps the code as a bookmark the strip offers back as
*Resume*, and `generate_proof()`, which hands the work in and CLOSES the code: a finished recording
cannot be reopened, and a student who wants to do the lab again needs a new code. A lab can be
declared *one sitting* by the teacher, in which case the arm reply says so and there is no Pause.
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
    ("link_changed", "_on_link_changed"),
    ("link_state_changed", "_on_link_state_changed"),
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
        self._gini_lab = ""
        self._activity_title = ""
        # What the lab ASKS FOR, in the teacher's own words. The server has always sent it with
        # the arm reply and it was dropped on arrival, so the panel could name the lab but never
        # say what it was for — the one thing a student most wants on screen while working.
        self._activity_brief = ""
        # Whether THIS lab may be paused, from the arm reply. True until a course server says
        # otherwise: a code armed offline is pausable, because the alternative is a student who
        # lost the wifi also losing the ability to stop for the night. The server's report counts
        # sittings either way, so a one-sitting lab done in three is visible to the teacher.
        self._pausable = True
        # The lab's questions, as the arm reply carried them. In memory only — see note_questions.
        self._questions: list = []
        # (device, face) already recorded under this code — see note_lab_open.
        self._faces_seen: set = set()
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
        self._link_props: dict[str, dict] = {}              # link id -> last seen cost/failure
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

    @property
    def pausable(self) -> bool:
        return self._pausable

    def paused_code(self) -> str:
        """The code whose recording is paused, as the student would type it, or "" when none is.
        Only meaningful while unarmed: arming anything clears the bookmark."""
        code = self.store.paused() if not self.armed else ""
        return _ticket.Ticket(code).pretty if code else ""

    def status(self) -> dict:
        """Everything the dashboard strip needs, in one plain dict (no Qt types)."""
        paused = self.paused_code()
        return {"armed": self.armed,
                "ticket": self._ticket.pretty if self._ticket else "",
                "short": self._ticket.short if self._ticket else "",
                "count": self.count,
                "submitted": bool(self._chain and self._chain.has_submitted()),
                "pausable": self._pausable,
                # The bookmark: a code the student paused and has not come back to. The strip
                # shows it and offers Resume, so coming back is one click rather than a printout.
                "paused": paused,
                "paused_short": paused[:_ticket.GROUP] if paused else "",
                "activity": self._activity,
                "activity_title": self._activity_title,
                "activity_brief": self._activity_brief,
                "error": self.last_error}

    # -- arming ------------------------------------------------------------- #
    def arm(self, code: str, assignment: str = "") -> tuple[bool, str]:
        """Validate a code and start (or resume) its chain. Returns (ok, message to show).

        A code that has been handed in is refused. Its chain is closed by the `submit` entry — an
        answer or a build appended after it would be in no proof anyone reads — and the course
        server refuses a second submission under one code anyway. Saying so HERE, before any work
        is done, is the difference between a student losing a minute and losing an evening.
        """
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
        if self.store.has_proof(tk.code) or (chain is not None and chain.has_submitted()):
            return False, self._finished_message(tk)
        fresh = chain is None
        if fresh:
            chain = _proof.Chain.start(
                tk.code, assignment=assignment or self._assignment(),
                gini_version=gini_version())
            self.store.write_chain(tk.code, chain)
        self._chain, self._ticket = chain, tk
        # Which GINI lab this code is for comes from the course server's arm reply
        # (`note_activity`), which follows this. Cleared here so a code armed offline is not
        # graded as the previous code's lab.
        self._gini_lab = ""
        # Arming anything — this code or another — spends the bookmark. Pausing is "I will be
        # back for this one"; typing a different code is the answer to that.
        self.store.clear_paused()
        # Pausable until the course server says otherwise. Reset per arm so one lab's one-sitting
        # rule does not follow the student into the next lab armed offline.
        self._pausable = True
        # A new code is a new lab. Without this, a face opened under the PREVIOUS code would count
        # as already recorded and the new chain would never mention it. (Handing in closes the
        # code and clears it too; this covers a code armed over another without a pause.)
        self._faces_seen = set()
        self._snapshot()
        if fresh:
            # Say what the canvas already held, at the moment it was armed. A student who builds
            # first and arms afterwards has not cheated, but the chain cannot claim to have
            # watched that work — so it states the truth rather than implying otherwise.
            self._record(ev.preexisting(self._topology_dict()))
        else:
            # Picking a code back up joins one session to another with a hole between them, and
            # the hole belongs in the chain. `away` is measured from the last entry, which is the
            # `stopped` one when the student paused from inside gBuilder.
            head = chain.head
            away = max(0.0, self._now() - float(head.t)) if head else 0.0
            self._record(ev.resumed(self._topology_dict(), away=away))
        self.last_error = ""
        self._changed()
        n = self.count
        return True, (f"Recording under {tk.pretty}." if fresh else
                      f"Resumed recording under {tk.pretty} — {n} event(s) already in the chain.")

    def _finished_message(self, tk: _ticket.Ticket) -> str:
        receipt = self._receipt_for(tk.code)
        held = f" (receipt {receipt})" if receipt else ""
        return (f"{tk.pretty} has already been handed in{held}. A finished recording cannot be "
                f"reopened — to do this lab again, get a new code from your course page.")

    def _receipt_for(self, code: str) -> str:
        """The receipt a finished code was handed in under, or "" — a courtesy in a refusal, so
        a proof file that cannot be read costs the sentence its receipt and nothing else."""
        try:
            return _proof.receipt_code(_proof.load_proof(self.store.proof_path(code)))
        except Exception:                               # noqa: BLE001
            return ""

    def note_pausable(self, pausable: bool) -> None:
        """Whether the course allows this lab to be paused, from the arm reply — like
        `note_activity`, set from outside because the recorder never talks to the network. The
        strip hides Pause when it is False; nothing about the chain changes either way."""
        self._pausable = bool(pausable)
        self._changed()

    def note_activity(self, activity: str, title: str = "", brief: str = "",
                      gini_lab: str = "") -> None:
        """Remember which lab the armed code belongs to, from the course server's arm reply.

        Set from OUTSIDE, because the recorder never talks to the network — a code is
        self-verifying, so a student with no connection still records perfectly well and simply
        has no activity to name.

        `brief` is what the lab asks for. In memory only, like the questions and for the same
        reason: the chain records what the student DID, and the assignment text is not that.
        """
        self._activity = str(activity or "")
        self._gini_lab = str(gini_lab or "")
        self._activity_title = str(title or "")
        self._activity_brief = str(brief or "")

    @property
    def gini_lab(self) -> str:
        """The GINI lab this code is for ("a-lab-04", "t-lab-02"), as the course server said when
        it was armed; "" when it did not say. Hand-in checks this lab and no other."""
        return self._gini_lab

    @property
    def activity_title(self) -> str:
        return self._activity_title

    @property
    def activity_brief(self) -> str:
        return self._activity_brief

    def note_questions(self, questions) -> None:
        """The lab's questions, from the same arm reply. Held in memory, NOT written to the chain.

        Set from outside for the same reason as `note_activity` — the recorder never talks to the
        network. Not persisted, either: the chain is a record of what the student DID, and a list
        of questions nobody has answered yet is not that. The cost is that a restart while armed
        loses them until the next arm, which the panel handles by offering to fetch — the same path
        it already needs for a code armed with no server in reach.

        The ANSWERS are a different matter entirely, and those do go in the chain: see
        `note_answer`.
        """
        self._questions = list(questions or [])

    @property
    def questions(self) -> list:
        return list(getattr(self, "_questions", []))

    def answers(self) -> dict:
        """What has been answered so far, read back out of the chain.

        The chain is the state. Anything held beside it is a second copy that can disagree with the
        one that gets submitted and marked.
        """
        from gini.domain import lab_questions as _q
        return _q.answers_in(self._chain.entries if self._chain else [])

    def pause(self) -> None:
        """Leave recording mode, keeping the code as a bookmark. Resume is arming it again.

        This has been through three names. Recording was first a one-way door — nothing disarmed,
        not even generating a proof — so a student who armed the wrong code restarted gBuilder.
        Then it was *Cancel*, chosen over a pause on the principle that a strip which answers
        "stop recording me" by printing the code back has not really stopped. That principle was
        right about surveillance and wrong about students: to a student *Cancel* means "throw my
        morning away", and several asked for a way to stop for the night that did not sound like
        that. So it is *Pause*: the chain stays on disk, the paused code is written beside it
        (`ChainStore.mark_paused`), and the strip shows it with a Resume button — across a restart
        too, which is the case a pause exists for.

        Nothing is lost, and nothing is hidden from the chain either. The last thing written is
        that recording stopped, because a gap the chain does not mention is a gap nobody can
        weigh; resuming writes how long the gap was. The teacher's report counts the sittings.

        Pausing is allowed whether or not the course said the lab is pausable. The strip hides the
        button for a one-sitting lab; the recorder does not second-guess a call it has no way to
        verify offline, and the chain tells the teacher what happened.
        """
        if self._chain is not None:
            self._record(ev.stopped(self._topology_dict()))
            if self._ticket is not None:
                self.store.mark_paused(self._ticket.code)
        self._close()

    def _close(self) -> None:
        """Forget the armed chain and everything that came with its code. The questions go too:
        a student who pauses and arms a DIFFERENT code must not be shown the last lab's questions
        with a fresh chain underneath them."""
        self._chain = None
        self._ticket = None
        self._activity = self._activity_title = self._activity_brief = ""
        self._pausable = True
        self._questions = []
        self._faces_seen = set()
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
        from ..domain import link_props as LP                # baseline, so a later edit diffs
        self._link_props[link_id] = {k: str(v) for k, v in LP.values(link).items()}   # from THIS
        if link_id in self._loaded_links:
            self._loaded_links.discard(link_id)
            return
        self._record(ev.connect(names[0], a.type_key if a else "",
                                names[1], b.type_key if b else "",
                                kind=getattr(link, "kind", "link")))

    def _on_link_changed(self, link_id) -> None:
        self._guard(self._link_changed, link_id)

    def _link_changed(self, link_id) -> None:
        """A link's cost or failure model, diffed against what was recorded last, exactly as a
        device's properties are -- with the defaults filled in, so setting a value back to the
        default still reads as an edit."""
        from ..domain import link_props as LP
        link = getattr(self._topology(), "links", {}).get(link_id)
        if link is None:
            return
        after = {k: str(v) for k, v in LP.values(link).items()}
        before = self._link_props.get(link_id) or {k: str(v) for k, v in LP.DEFAULTS.items()}
        changes = ev.diff_properties(before, after)
        self._link_props[link_id] = after
        a, b = self._endpoint_names(link)
        self._record(ev.configure_link(link_id, a, b, changes))

    def _on_link_state_changed(self, link_id, up, why="") -> None:
        self._guard(self._link_state_changed, link_id, up, why)

    def _link_state_changed(self, link_id, up, why) -> None:
        link = getattr(self._topology(), "links", {}).get(link_id)
        if link is None:
            return
        a, b = self._endpoint_names(link)
        self._record(ev.link_state(a, b, bool(up), why))

    def _on_link_removed(self, link_id) -> None:
        self._guard(self._link_removed, link_id)

    def _link_removed(self, link_id) -> None:
        self._link_props.pop(link_id, None)
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

    # -- the OS labs ------------------------------------------------------- #
    #
    # Called from the Machine Lab and its faces. Every one of them goes through `_guard`, which
    # swallows whatever it throws: a Load that stopped working because the proof chain hiccuped
    # would be a far worse bug than a missing entry, and Load is the most consequential button in
    # the lab. Same reasoning as terminal_panel._pump.

    def note_lab_open(self, device: str, face: str) -> None:
        """A face was opened. Recorded ONCE per face per armed session.

        The de-duplication lives here rather than in the widget because a face can be opened from
        several places — a card, a menu, a keyboard shortcut — and each of them would otherwise
        have to remember. Cleared on arm and disarm with everything else.
        """
        key = (str(device or ""), str(face or ""))
        if key in self._faces_seen:
            return
        self._faces_seen.add(key)
        self._guard(lambda: self._record(ev.lab_open(key[0], key[1])))

    def note_tune(self, device: str, knob: str, before, after) -> None:
        """A kernel knob moved on a running machine. No-ops are dropped by `ev.tune`."""
        self._guard(lambda: self._record(ev.tune(str(device or ""), str(knob or ""),
                                                 before, after)))

    def note_spawn(self, device: str, what: str, action: str = "launch",
                   pid: int | None = None, out=None, test: bool = False, run: str = "") -> None:
        """A program launched, or a process killed.

        `out` is what it printed, when GINI ran it and waited — see `ev.spawn`. `test` marks the
        assignment's prescribed test, which is the run a marker is looking for.
        """
        self._guard(lambda: self._record(
            ev.spawn(str(device or ""), str(what or ""), action, pid,
                     out=list(out) if out is not None else None, test=bool(test),
                     run=str(run or ""))))

    def note_build(self, device: str, shadow: str, ok: bool, sources=None,
                   log=None, action: str = "load", checks=None) -> None:
        """The student compiled their own kernel code — successes AND failures.

        `sources` is `{filename: {"sha256", "lines"}}` as the files stood when the build ran. It
        is what binds this entry to the code that travels with the submission.
        """
        self._guard(lambda: self._record(
            ev.build(str(device or ""), str(shadow or ""), bool(ok), dict(sources or {}),
                     list(log or []), action,
                     list(checks) if checks is not None else None)))

    def note_measure(self, name: str, result) -> None:
        """One reading GINI took and stands behind — an A-Lab metric, or anything else measured
        deliberately rather than observed in passing.

        Separate from `_rider_ran`, which is driven off the bus and rate-limited because a rider
        streams. A grading run happens when a student asks for it, so every metric it produces is
        recorded: a metric that could not be measured is evidence too, and `pending` carries that
        rather than it arriving as a failure.
        """
        self._guard(lambda: self._record(ev.measure(str(name or ""), dict(result or {}))))

    # -- the T-Labs ----------------------------------------------------------- #
    def note_schedule(self, lab: str, level: str, version: str, start: int, code, moves,
                      goal: str, result: str, threads=()) -> None:
        """A schedule the student played reached one of the level's goals. Once per goal per
        level — `sched_lab.Session` keeps the set, so Undo and replay do not repeat it."""
        self._guard(lambda: self._record(
            ev.schedule(str(lab or ""), str(level or ""), str(version or ""), int(start or 0),
                        code, moves, str(goal or ""), str(result or ""), threads)))

    def note_proved(self, lab: str, level: str, version: str, code, ok: bool, reason: str = "",
                    starts=(), summary=None) -> None:
        """Try every schedule, on a version that can be proved: the verdict, held or not."""
        self._guard(lambda: self._record(
            ev.proved(str(lab or ""), str(level or ""), str(version or ""), code, bool(ok),
                      str(reason or ""), starts, summary)))

    #: T-Lab 03 and 04 entries: the window's event kind → the `proof_events` builder.
    TLAB_KINDS = {"allocate": "allocate", "bank": "bank", "orders": "orders",
                  "fs_walk": "fs_walk", "fs_read": "fs_read", "fs_make": "fs_make",
                  "fs_trace": "fs_trace"}

    def note_tlab(self, kind: str, payload: dict) -> None:
        """A T-Lab 03 or 04 goal (or Try every order verdict), from the lab's window.

        One method for all of them, because the windows already send a payload named exactly as
        the builder's parameters: each builder is called with the fields it takes, so a window
        cannot slip an extra field into the chain, and a missing one becomes the builder's
        default. An unknown kind is ignored — it would be a window newer than this recorder.
        """
        import inspect
        name = self.TLAB_KINDS.get(str(kind or ""))
        if name is None:
            return
        build = getattr(ev, name)
        p = dict(payload or {})
        args = {k: p[k] for k in inspect.signature(build).parameters if k in p}
        self._guard(lambda: self._record(build(**args)))

    def note_observed(self, device: str, kind: str, detail: str,
                      pid: int | None = None) -> None:
        """A phenomenon the kernel showed — from MachineState's watcher, off the poll thread.

        The watcher is edge-triggered (once per condition per episode, re-arming when it clears),
        which is what makes it safe to record from a path that runs every second.
        """
        self._guard(lambda: self._record(
            ev.observe(str(device or ""), str(kind or ""), str(detail or ""), pid)))

    def note_answer(self, question_id: str, prompt: str, text: str) -> bool:
        """Record the student's answer to one of the lab's questions.

        Returns whether it went in, so the panel can say so rather than guess. It does NOT go in
        when nothing is being recorded, or when the work has already been handed in — an answer
        appended after the `submit` entry is not in the proof that was sent, so accepting it would
        let a student type into a box that no marker will ever read.

        The prompt travels WITH the answer rather than being looked up later: a teacher may edit or
        retire a question between the lab and the marking, and an answer whose question changed
        underneath it is worse than no answer at all.

        Answering twice appends twice, deliberately. The chain is append-only, a student may think
        again, and the report shows the last pass and says how many there were.
        """
        if not self.armed or (self._chain and self._chain.has_submitted()):
            return False
        self._guard(self._note_answer, str(question_id or ""), str(prompt or ""), str(text or ""))
        return True

    def _note_answer(self, question_id: str, prompt: str, text: str) -> None:
        self._record(ev.answer(question_id, prompt, text))

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
        """Append the `submit` entry, write the proof file, and close the code.

        Returns a plain dict — `{ok, message, path, receipt, proof}` — rather than raising, so the
        dashboard button has one thing to render whatever happened.

        CLOSES the recording on success. Handing in is the end of a code: the chain's `submit`
        entry is what the proof commits to, so anything recorded after it is in no proof anyone
        reads, and the course server refuses a second submission under the same code. Leaving the
        strip armed used to invite exactly that — a student pressing Generate twice and holding
        two receipts, one of which the server would never accept. The upload itself does not need
        the recorder: the result carries everything, and the outbox retries from the file.
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
        result = {"ok": True, "path": str(path), "proof": proof, "topology": topology,
                  # An OS lab's deliverable. Scoped to the xv6 machines in THIS topology — the
                  # shadow directories are per-machine and outlive the topology that made them,
                  # so gathering everything under ~/.gini/xv6-shadows/ would put one lab's work
                  # into another lab's submission. Never fatal: a proof that could not be written
                  # is a failure, a proof whose sources could not be read is still a proof.
                  "shadows": self._collect_shadows(topology),
                  "receipt": _proof.receipt_code(proof),
                  "message": f"Proof written to {path}"}
        self.store.clear_paused()
        self._close()
        return result

    def _collect_shadows(self, topology: dict) -> dict:
        """Every kernel file the student owns on the xv6 machines in THIS topology.

        Two sources, one dict. The shadow directory is the deliverable of a shadow lab; the lab
        folder is the deliverable of an A-Lab, and its file list belongs to the assignment rather
        than being fixed at three. A syscall assignment submitted through the shadow path alone
        arrives with three files the student never opened and none of the ten they did.

        Which assignment each machine was carrying is resolved inside `collect`, per machine,
        because arming is per machine: a topology with M1 on A-Lab 01 and M2 on A-Lab 02 hands in
        both, each attributed to the machine it came from.

        The key is still "shadows" on the wire because the server reads it, and because what it
        has always meant is "the student's kernel source". Filenames do not collide between the
        two — a shadow is gini_*.c and nothing else is.

        Never fatal, either half: a proof that could not be written is a failure, a proof whose
        sources could not be read is still a proof.
        """
        out: dict = {}
        try:
            from .xv6_shadows import collect, xv6_machines
            machines = xv6_machines(topology)
            out.update(collect(machines))
        except Exception as e:                              # noqa: BLE001
            self._complain(e)
            return out
        try:
            from .xv6_lab import collect as collect_lab
            out.update(collect_lab(machines))
        except Exception as e:                              # noqa: BLE001
            self._complain(e)
        return out

"""The event vocabulary: which of GINI's signals become chain entries, and as what.

Nothing here senses anything new. gBuilder already emits every fact worth recording — a device
was added, a link was made, the lab ran, a probe returned a verdict — so this module is only the
*translation*, kept pure and away from the bus so it can be tested without Qt.

Three tiers, in ascending order of what they prove:

  1. **construction** — place / remove / connect / disconnect / configure. A connect entry also
     carries `connection_rules.can_connect()`'s teaching reason, which is what lets the narration
     say "connected M1 to S1 — join a LAN" instead of "link-7 created".
  2. **operation** — run / open_console / measure / invoke. Evidence that the thing was used.
  3. **witnessed** — witness / objective. The strongest entries: facts GINI measured on the
     *running* network. A witness cannot be produced by importing a file, because there was no
     running network when the file was made.

Two entry kinds are not translations of a signal but statements about provenance — `load` (a
topology arrived from a file) and `preexisting` (the canvas was not empty when recording started).
They exist so the narration can say plainly where the submitted work came from.

**Deliberately not recorded** (see IGNORED): view and selection churn, which is noise, and
`assistant_message`. Recording help-seeking would push students away from the tutor, which costs
more pedagogically than the provenance is worth.
"""
from __future__ import annotations

from . import connection_rules as _rules
from .proof import PREEXISTING, SUBMIT, clip

# -- the vocabulary ---------------------------------------------------------- #
PLACE, REMOVE, CONNECT, DISCONNECT, CONFIGURE = (
    "place", "remove", "connect", "disconnect", "configure")
RUN, STOP, OPEN_CONSOLE, MEASURE, INVOKE = (
    "run", "stop", "open_console", "measure", "invoke")
COMMAND = "command"
WITNESS, OBJECTIVE = "witness", "objective"
LOAD = "load"
STOPPED, RESUMED = "stopped", "resumed"

CONSTRUCTION = (PLACE, REMOVE, CONNECT, DISCONNECT, CONFIGURE)
OPERATION = (RUN, STOP, OPEN_CONSOLE, MEASURE, INVOKE, COMMAND)
WITNESSED = (WITNESS, OBJECTIVE)
PROVENANCE = (LOAD, PREEXISTING, SUBMIT, STOPPED, RESUMED)

# bus signal -> the kind it becomes. The recorder subscribes to exactly these.
SIGNAL_KINDS = {
    "device_added": PLACE,
    "device_removed": REMOVE,
    "link_added": CONNECT,
    "link_removed": DISCONNECT,
    "device_changed": CONFIGURE,
    "run_state": RUN,
    "device_activated": OPEN_CONSOLE,
    "rider_ran": MEASURE,
    "function_invoke_result": INVOKE,
}

# Signals that must never reach the chain. Listed rather than merely omitted so the exclusion is
# a decision on the record — particularly `assistant_message` (see the module docstring).
IGNORED = frozenset({
    "selection_changed", "canvas_background_clicked", "theme_changed", "warnings_changed",
    "edges_restyled", "llm_reachable", "runtime_status", "mem_metrics", "assistant_message",
    "focus_requested", "present_spotlight", "present_highlight", "present_callout",
    "present_narrate", "present_packet", "present_clear", "topology_changed",
    "device_resized", "addressing_changed", "mission_flags_changed", "boards_changed",
    "enrolment_changed", "log", "machine_events", "fabric_metrics", "k8s_metrics",
})


def is_recorded(signal: str) -> bool:
    return signal in SIGNAL_KINDS


# -- construction ------------------------------------------------------------ #
def place(device_id: str, type_key: str, name: str) -> tuple[str, dict]:
    return PLACE, {"id": str(device_id), "type": clip(type_key, 40), "name": clip(name, 64)}


def remove(device_id: str, name: str) -> tuple[str, dict]:
    return REMOVE, {"id": str(device_id), "name": clip(name, 64)}


def why_connected(a_type: str, b_type: str) -> str:
    """The teaching reason for an a–b link, in whichever direction the grammar states it.

    The grammar is directed (it is authored from one side), but a cable is not, so a link drawn
    "backwards" must still get its reason — otherwise half of a student's chain reads as bare
    topology and the narration loses exactly the thing that makes it semantic.
    """
    return _rules.can_connect(a_type, b_type) or _rules.can_connect(b_type, a_type) or ""


def connect(a_name: str, a_type: str, b_name: str, b_type: str,
            kind: str = "link") -> tuple[str, dict]:
    data = {"a": clip(a_name, 64), "b": clip(b_name, 64),
            "why": clip(why_connected(a_type, b_type))}
    if kind and kind != "link":
        # An attach edge (a Source/Sink riding its donor) is not a cable and should never read
        # as one in the narration.
        data["edge"] = clip(kind, 24)
    return CONNECT, data


def disconnect(a_name: str, b_name: str) -> tuple[str, dict]:
    return DISCONNECT, {"a": clip(a_name, 64), "b": clip(b_name, 64)}


# Properties whose changes say nothing about the work. Position is the important one: dragging a
# node emits `device_changed` on every mouse move, and a chain full of coordinates would bury the
# handful of entries that matter.
_VOLATILE_PROPS = frozenset({"x", "y", "w", "h", "size", "parent_id"})


def diff_properties(before: dict, after: dict) -> dict:
    """Which properties changed, and to what. Added keys count; removed keys are reported as ""
    so a cleared field is still visible as an edit."""
    before, after = dict(before or {}), dict(after or {})
    changes = {}
    for key in set(before) | set(after):
        if key in _VOLATILE_PROPS:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[str(key)] = clip("" if new is None else new, 96)
    return changes


def configure(device_id: str, name: str, changes: dict) -> tuple[str, dict] | None:
    """A property edit — or None when nothing recordable changed.

    Returning None matters: `device_changed` also fires for a drag, and an entry per mouse move
    would make the chain unreadable and the file enormous.
    """
    if not changes:
        return None
    return CONFIGURE, {"id": str(device_id), "name": clip(name, 64), "changes": dict(changes)}


# -- operation --------------------------------------------------------------- #
def run(ok: bool, message: str = "") -> tuple[str, dict]:
    return RUN, {"ok": bool(ok), "msg": clip(message)}


def stop(message: str = "") -> tuple[str, dict]:
    return STOP, {"msg": clip(message)}


def open_console(device_id: str, name: str) -> tuple[str, dict]:
    """They went *into* the element.

    This used to say "deliberately not what they typed — a proof of activity is not a keylogger".
    That reasoning was about a student's own machine, and it still holds there. It does not reach
    gBuilder's own terminal into a container gBuilder started, opened as part of the assignment
    with a loud REC indicator running: see `command`, which records those.
    """
    return OPEN_CONSOLE, {"id": str(device_id), "name": clip(name, 64)}


def command(device: str, cmd: str, output: list[str] | None = None) -> tuple[str, dict]:
    """A command run in gBuilder's own terminal on a lab element, and what it printed.

    `open_console` records only that they went in, and said so because "a proof of activity is not
    a keylogger". That reasoning stands for a student's own machine and does not reach here: this
    is gBuilder's terminal into a container gBuilder started, opened deliberately as part of the
    assignment, while a loud REC indicator says the session is being recorded.

    It closes the gap the report itself names. A chain could say "Started the lab" and then
    "Witnessed on the running network — none", when the student had in fact pinged across their
    subnets and watched it work; the evidence existed and vanished. This is that evidence.

    Output is TRUNCATED, not summarised — the first few lines, verbatim, with a count of what was
    dropped. `ping` says what a marker needs in its first lines and then repeats itself; guessing
    which later line mattered would be inventing evidence.
    """
    lines = [clip(x, 200) for x in (output or [])]
    return COMMAND, {"on": clip(device, 64), "cmd": clip(cmd, 200), "out": lines}


def measure(name: str, result: dict) -> tuple[str, dict]:
    """A Source/Sink rider's structured reading. The measurement is the point; the raw stream is
    not recorded, because it is long, noisy and adds nothing an instructor would read."""
    result = dict(result or {})
    return MEASURE, {"name": clip(name, 64), "ok": bool(result.get("ok")),
                     "measurement": {str(k): clip(v, 64)
                                     for k, v in (result.get("measurement") or {}).items()},
                     "summary": clip(result.get("summary", ""))}


def invoke(device_id: str, result_text: str) -> tuple[str, dict]:
    return INVOKE, {"id": str(device_id), "result": clip(result_text)}


# -- witnessed --------------------------------------------------------------- #
def witness(probe: str, verdict: str) -> tuple[str, dict]:
    """A probe GINI evaluated against the live network. `verdict` is ok | fail | pending —
    "pending" is recorded too, because "they pressed Check with nothing running" is itself a fact
    about the attempt, and dropping it would make the narration read as if they never tried."""
    return WITNESS, {"probe": clip(probe), "verdict": str(verdict)}


def objective(objective_id: str, say: str, before: str, after: str) -> tuple[str, dict]:
    return OBJECTIVE, {"id": clip(objective_id, 64), "say": clip(say),
                       "from": str(before or ""), "to": str(after or "")}


# -- provenance -------------------------------------------------------------- #
def load(source: str, topo_dict: dict) -> tuple[str, dict]:
    """A whole topology arrived at once, from a file.

    This is the entry that makes "imported a friend's work" visible. It records the ids as well as
    the count, so the submission can be accounted for element by element (see
    `proof.account_for_artifact`) rather than merely flagged.
    """
    devices = list((topo_dict or {}).get("devices", []) or [])
    return LOAD, {"source": clip(source, 120),
                  "devices": len(devices),
                  "links": len(list((topo_dict or {}).get("links", []) or [])),
                  "ids": sorted(str(d.get("id", "")) for d in devices),
                  "names": sorted(clip(d.get("name", ""), 64) for d in devices)}


def preexisting(topo_dict: dict) -> tuple[str, dict]:
    """What was already on the canvas when the student armed recording.

    Recorded honestly rather than ignored: a student who builds first and arms afterwards has not
    cheated, but the chain cannot claim to have watched that work, and the narration says so.
    """
    devices = list((topo_dict or {}).get("devices", []) or [])
    return PREEXISTING, {"devices": len(devices),
                         "links": len(list((topo_dict or {}).get("links", []) or [])),
                         "ids": sorted(str(d.get("id", "")) for d in devices),
                         "names": sorted(clip(d.get("name", ""), 64) for d in devices)}


def stopped(topo_dict: dict) -> tuple[str, dict]:
    """The student stopped recording. Written INTO the chain, as the last thing before it closes.

    A gap in a provenance chain has to be part of the chain, or it is not provenance. Without this
    entry a student stops, works for an hour unwatched, starts again under the same code, and the
    chain reads as one continuous session — the elements would show up unaccounted for, but with
    nothing to say why. This says when the watching stopped and what was on the canvas at that
    moment, so the same reading holds whether or not anybody ever asks.
    """
    devices = list((topo_dict or {}).get("devices", []) or [])
    return STOPPED, {"devices": len(devices),
                     "links": len(list((topo_dict or {}).get("links", []) or [])),
                     "ids": sorted(str(d.get("id", "")) for d in devices)}


def resumed(topo_dict: dict, away: float = 0.0) -> tuple[str, dict]:
    """Recording picked back up under the same code, and how long it was off for.

    The ids are recorded but deliberately NOT counted as `preexisting` by
    `proof.account_for_artifact`: work that appeared while nobody was watching stays unaccounted
    for. Treating it as "already there" would turn stop-and-resume into a way to launder an
    import, which is the exact forgery the chain exists to catch.
    """
    devices = list((topo_dict or {}).get("devices", []) or [])
    return RESUMED, {"devices": len(devices),
                     "links": len(list((topo_dict or {}).get("links", []) or [])),
                     "away": round(max(0.0, float(away or 0.0)), 3),
                     "ids": sorted(str(d.get("id", "")) for d in devices)}


def submit(artifact: dict, objectives) -> tuple[str, dict]:
    """The last entry before a proof is generated: what was handed in, and how it scored.

    Binding the artifact digest into the chain is what closes the "borrowed topology, own proof"
    gap — the objectives table is what the instructor reads first.
    """
    rows = [{"id": clip(_field(r, "id"), 64), "say": clip(_field(r, "say")),
             "kind": str(_field(r, "kind")), "status": str(_field(r, "status"))}
            for r in objectives or []]
    return SUBMIT, {"artifact": dict(artifact or {}), "objectives": rows}


def _field(row, key: str):
    """Read a field from an `objectives.ObjectiveResult` or the plain dict a test (or a re-read
    proof) supplies. Duck-typed on purpose: this module must not import the objective engine just
    to copy four strings out of it."""
    if isinstance(row, dict):
        return row.get(key, "")
    return getattr(row, key, "")

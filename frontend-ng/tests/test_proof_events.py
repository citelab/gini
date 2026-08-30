"""The event vocabulary, and the recorder that speaks it.

The recorder is written against `signal.connect(...)` and nothing else, which is what lets this
file drive a whole recorded session — place, wire, run, check, submit — with no PySide6 and no
Docker anywhere near it. The fake bus below is the same shape as `app.context.EventBus`; if a
signal is renamed there, these tests keep passing and the recorder silently records less, which is
why `test_the_recorder_subscribes_to_signals_the_real_bus_has` pins the names against the real one.
"""
import time

import pytest

from gini.domain import proof as P
from gini.domain import proof_events as ev
from gini.domain.objectives import Objective, ObjectiveResult
from gini.domain import ticket as _ticket
from gini.domain.ticket import mint
from gini.domain.topology import Topology
from gini.services.proof_recorder import ProofRecorder

CODE = mint(lambda n: bytes((i * 17 + 5) % 256 for i in range(n))).pretty


# ---- the vocabulary --------------------------------------------------------- #
def test_help_seeking_is_never_recorded():
    """Deliberate: recording who asked the tutor for help would teach students to stop asking."""
    assert "assistant_message" in ev.IGNORED
    assert not ev.is_recorded("assistant_message")


def test_view_churn_is_never_recorded():
    for signal in ("selection_changed", "theme_changed", "focus_requested", "runtime_status"):
        assert signal in ev.IGNORED and not ev.is_recorded(signal)


def test_the_recorded_and_ignored_sets_do_not_overlap():
    assert not (set(ev.SIGNAL_KINDS) & ev.IGNORED)


def test_a_connection_carries_its_teaching_reason():
    kind, data = ev.connect("M1", "host", "S1", "switch")
    assert kind == ev.CONNECT
    assert "LAN" in data["why"]                       # the grammar's reason, not "link-7 created"


def test_a_connection_drawn_backwards_still_gets_its_reason():
    """The grammar is authored from one side; a cable is not directed, and half a student's chain
    must not read as bare topology because of which end they dragged from."""
    assert ev.connect("S1", "switch", "M1", "host")[1]["why"] == \
           ev.connect("M1", "host", "S1", "switch")[1]["why"]


def test_an_unknown_pairing_records_the_link_without_inventing_a_reason():
    assert ev.connect("A", "host", "B", "freedos")[1]["why"] == ""


def test_an_attach_edge_is_not_described_as_a_cable():
    assert ev.connect("src", "iperf_client", "M1", "host", kind="attach")[1]["edge"] == "attach"


def test_a_drag_records_nothing():
    """`device_changed` fires on every mouse move; an entry per move would bury the chain."""
    unchanged = ev.diff_properties({"OS": "linux"}, {"OS": "linux"})
    assert ev.configure("host-1", "M1", unchanged) is None


def test_a_real_edit_records_what_changed():
    changes = ev.diff_properties({"OS": "linux", "Interfaces": "1"},
                                 {"OS": "linux", "Interfaces": "2"})
    kind, data = ev.configure("host-1", "M1", changes)
    assert kind == ev.CONFIGURE and data["changes"] == {"Interfaces": "2"}


def test_a_cleared_field_is_still_visible_as_an_edit():
    assert ev.diff_properties({"IP": "10.0.0.1"}, {})["IP"] == ""


def test_a_console_entry_records_that_they_went_in_not_what_they_typed():
    _kind, data = ev.open_console("host-1", "M1")
    assert set(data) == {"id", "name"}            # a proof of activity is not a keylogger


def test_a_pending_check_is_recorded_too():
    """'They pressed Check with nothing running' is a fact about the attempt; dropping it would
    make the narration read as if they never tried."""
    assert ev.witness("reach(a -> b) == ok", "pending")[1]["verdict"] == "pending"


def test_long_values_are_clipped_not_dropped():
    _kind, data = ev.invoke("fn-1", "x" * 5000)
    assert len(data["result"]) <= P.MAX_TEXT and data["result"].endswith("…")


# ---- a fake bus, the same shape as app.context.EventBus --------------------- #
class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        for fn in list(self._slots):
            fn(*args)


class FakeBus:
    def __init__(self):
        for name in ev.SIGNAL_KINDS:
            setattr(self, name, _Signal())


class FakeCtx:
    """Just enough AppContext for the recorder: a topology, a bus, and a log."""

    def __init__(self):
        self.topology = Topology("lan-basics")
        self.bus = FakeBus()
        self.logs = []

    def log(self, message, level="info"):
        self.logs.append((level, message))

    # the mutators the canvas would call, emitting the same signals AppContext emits
    def add_device(self, type_key):
        d = self.topology.add_device(type_key)
        self.bus.device_added.emit(d.id)
        return d

    def add_link(self, a, b):
        l = self.topology.add_link(a.id, b.id)
        self.bus.link_added.emit(l.id)
        return l

    def remove_link(self, link_id):
        self.topology.remove_link(link_id)
        self.bus.link_removed.emit(link_id)

    def remove_device(self, device_id):
        self.topology.remove_device(device_id)
        self.bus.device_removed.emit(device_id)

    def change(self, device, **props):
        device.properties.update(props)
        self.bus.device_changed.emit(device.id)


@pytest.fixture()
def rec(tmp_path):
    ctx = FakeCtx()
    r = ProofRecorder(ctx, store=P.ChainStore(tmp_path))
    r.attach()
    return r


def _build_a_lan(rec):
    ctx = rec.ctx
    m = ctx.add_device("host")
    s = ctx.add_device("switch")
    ctx.add_link(m, s)
    return m, s


def _kinds(rec):
    return [e.kind for e in rec._chain.entries]


# ---- arming ----------------------------------------------------------------- #
def test_the_recorder_subscribes_to_signals_the_real_bus_has():
    """Guards against the vocabulary drifting away from app.context.EventBus. Skipped where Qt is
    absent (the bus is a QObject), which is most of this file's normal habitat."""
    context = pytest.importorskip("gini.app.context")
    bus = context.EventBus()
    for name in ev.SIGNAL_KINDS:
        assert hasattr(bus, name), f"{name} is no longer a bus signal"


def test_nothing_is_recorded_until_a_code_is_entered(rec):
    _build_a_lan(rec)
    assert not rec.armed and rec.count == 0
    assert rec.status()["armed"] is False


def test_a_valid_code_arms_recording(rec):
    ok, message = rec.arm(CODE)
    assert ok and rec.armed and "Recording" in message
    assert rec.status()["short"] == rec.ticket.short


def test_an_invalid_code_is_refused_with_a_reason(rec):
    ok, message = rec.arm("nope")
    assert not ok and not rec.armed
    assert "12 characters" in message                 # the reason, not "invalid"


def test_an_empty_code_is_refused_with_a_reason(rec):
    ok, message = rec.arm("")
    assert not ok and "instructor" in message


def test_arming_records_what_was_already_on_the_canvas(rec):
    _build_a_lan(rec)                                  # built BEFORE arming
    rec.arm(CODE)
    genesis, first = rec._chain.entries[0], rec._chain.entries[1]
    assert genesis.kind == P.GENESIS
    assert first.kind == P.PREEXISTING and first.data["devices"] == 2


def test_the_assignment_defaults_to_the_experiment_name(rec):
    rec.arm(CODE)
    assert rec._chain.assignment == "lan-basics"


def test_recording_resumes_across_a_restart(rec, tmp_path):
    """The same chain, continued — plus one entry saying it was picked back up. gBuilder closing
    is a gap in the watching exactly as cancelling is, and the chain says so either way."""
    rec.arm(CODE)
    _build_a_lan(rec)
    before = rec.count

    later = ProofRecorder(FakeCtx(), store=P.ChainStore(tmp_path))
    ok, message = later.arm(CODE)
    assert ok and "Resumed" in message
    assert later.count == before + 1
    assert later._chain.entries[-1].kind == ev.RESUMED
    assert later._chain.kinds().get(ev.PLACE, 0) == 2      # not a second chain
    assert P.verify_entries(later._chain.entries).ok


# ---- tier 1: construction ---------------------------------------------------- #
def test_building_a_lan_records_the_construction_sequence(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    assert _kinds(rec) == [P.GENESIS, P.PREEXISTING, ev.PLACE, ev.PLACE, ev.CONNECT]
    connect = rec._chain.entries[-1]
    assert connect.data["a"] == "M1" and connect.data["b"] == "S1" and connect.data["why"]


def test_removals_still_know_what_was_removed(rec):
    """By the time the signal fires the device is already out of the topology, so the recorder's
    cache is the only thing that can name it."""
    rec.arm(CODE)
    m, _s = _build_a_lan(rec)
    rec.ctx.remove_device(m.id)
    last = rec._chain.entries[-1]
    assert last.kind == ev.REMOVE and last.data["name"] == "M1"


def test_disconnecting_names_both_ends(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    link_id = next(iter(rec.ctx.topology.links))
    rec.ctx.remove_link(link_id)
    last = rec._chain.entries[-1]
    assert last.kind == ev.DISCONNECT and {last.data["a"], last.data["b"]} == {"M1", "S1"}


def test_moving_a_node_records_nothing(rec):
    rec.arm(CODE)
    m, _s = _build_a_lan(rec)
    n = rec.count
    m.x, m.y = 40.0, 90.0
    rec.ctx.bus.device_changed.emit(m.id)
    assert rec.count == n


def test_editing_a_property_records_the_change(rec):
    rec.arm(CODE)
    m, _s = _build_a_lan(rec)
    rec.ctx.change(m, OS="alpine")
    last = rec._chain.entries[-1]
    assert last.kind == ev.CONFIGURE and last.data["changes"] == {"OS": "alpine"}


# ---- tier 2: operation -------------------------------------------------------- #
def test_running_the_lab_is_recorded(rec):
    rec.arm(CODE)
    rec.ctx.bus.run_state.emit(True, "up")
    assert rec._chain.entries[-1].kind == ev.RUN


def test_a_failed_run_is_recorded_as_a_failed_run(rec):
    rec.arm(CODE)
    rec.ctx.bus.run_state.emit(False, "docker is not running")
    assert rec._chain.entries[-1].data["ok"] is False


def test_opening_a_console_is_recorded(rec):
    rec.arm(CODE)
    m, _s = _build_a_lan(rec)
    rec.ctx.bus.device_activated.emit(m.id)
    last = rec._chain.entries[-1]
    assert last.kind == ev.OPEN_CONSOLE and last.data["name"] == "M1"


def test_a_rider_measurement_is_recorded(rec):
    rec.arm(CODE)
    m, _s = _build_a_lan(rec)
    rec.ctx.bus.rider_ran.emit(m.id, {"ok": True, "summary": "0% loss",
                                      "measurement": {"loss_pct": 0}})
    last = rec._chain.entries[-1]
    assert last.kind == ev.MEASURE and last.data["summary"] == "0% loss"


def test_a_continuous_rider_does_not_flood_the_chain(tmp_path):
    """A running Source re-emits on every line it reads; one entry per line would bury the chain
    in a single ping's output."""
    clock = [1000.0]
    ctx = FakeCtx()
    r = ProofRecorder(ctx, store=P.ChainStore(tmp_path), now=lambda: clock[0])
    r.attach()
    r.arm(CODE)
    m = ctx.add_device("host")
    for _ in range(50):
        clock[0] += 0.2
        ctx.bus.rider_ran.emit(m.id, {"ok": True, "running": True, "summary": "…"})
    assert sum(1 for k in _kinds(r) if k == ev.MEASURE) == 1
    ctx.bus.rider_ran.emit(m.id, {"ok": True, "running": False, "summary": "0% loss"})
    assert sum(1 for k in _kinds(r) if k == ev.MEASURE) == 2   # the final reading always lands


# ---- tier 3: witnessed --------------------------------------------------------- #
def _check(rec, status):
    objectives = [Objective("reach", "M1 reaches S1", kind="behavioral",
                            probe="reach(host -> host) == ok")]
    results = [ObjectiveResult("reach", "M1 reaches S1", "behavioral", status)]
    rec.note_check(results, objectives)
    return results


def test_a_live_check_records_the_probe_and_the_verdict(rec):
    rec.arm(CODE)
    _check(rec, "met")
    witness = next(e for e in rec._chain.entries if e.kind == ev.WITNESS)
    assert witness.data["probe"] == "reach(host -> host) == ok"
    assert witness.data["verdict"] == "ok"


def test_objectives_record_transitions_not_states(rec):
    """Nine 'still unmet' lines on every press would be unreadable; the interesting moment is the
    one where something started working."""
    rec.arm(CODE)
    _check(rec, "unmet")
    _check(rec, "unmet")
    _check(rec, "met")
    moves = [e.data for e in rec._chain.entries if e.kind == ev.OBJECTIVE]
    assert [(m["from"], m["to"]) for m in moves] == [("new", "unmet"), ("unmet", "met")]


def test_a_full_session_produces_all_three_tiers(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.ctx.bus.run_state.emit(True, "up")
    _check(rec, "met")
    kinds = set(_kinds(rec))
    assert {ev.PLACE, ev.CONNECT} <= kinds          # construction
    assert ev.RUN in kinds                          # operation
    assert {ev.WITNESS, ev.OBJECTIVE} <= kinds      # witnessed


# ---- submission ----------------------------------------------------------------- #
def test_generating_a_proof_produces_one_that_verifies(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.ctx.bus.run_state.emit(True, "up")
    _check(rec, "met")
    result = rec.generate_proof()
    assert result["ok"] and result["receipt"]
    verdict = P.verify_proof(result["proof"], expect_ticket=CODE)
    assert verdict.ok, verdict.reason


def test_a_generated_proof_accounts_for_everything_that_was_built(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    proof = rec.generate_proof()["proof"]
    acc = P.account_for_artifact(P.entries_of(proof))
    assert acc.ok and set(acc.built) == {"M1", "S1"}


def test_the_objectives_table_reaches_the_proof(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    _check(rec, "met")
    submit = rec.generate_proof()["proof"]["entries"][-1]
    assert submit["data"]["objectives"] == [
        {"id": "reach", "say": "M1 reaches S1", "kind": "behavioral", "status": "met"}]


def test_no_code_means_no_proof(rec):
    _build_a_lan(rec)
    result = rec.generate_proof()
    assert not result["ok"] and "assignment code" in result["message"]


def test_an_imported_topology_is_recorded_as_an_import(rec):
    rec.arm(CODE)
    borrowed = Topology("borrowed")
    a = borrowed.add_device("host")
    b = borrowed.add_device("switch")
    borrowed.add_link(a.id, b.id)
    rec.ctx.topology = borrowed                    # what File → Import does
    rec.note_load("lab3.gini", borrowed)

    acc = P.account_for_artifact(P.entries_of(rec.generate_proof()["proof"]))
    assert not acc.ok and len(acc.imported) == 2 and not acc.built


def test_opening_a_project_does_not_fake_a_construction_sequence(rec):
    """MainWindow._set_topology REPLAYS device_added/link_added for every element in the file. Left
    alone that would write a perfect fake build — the very thing this feature exists to detect."""
    rec.arm(CODE)
    borrowed = Topology("borrowed")
    a = borrowed.add_device("host")
    b = borrowed.add_device("switch")
    borrowed.add_link(a.id, b.id)

    rec.note_load("lab3.gini", borrowed)           # before the swap, as MainWindow must call it
    rec.ctx.topology = borrowed
    for d in borrowed.devices.values():            # …what _set_topology then emits
        rec.ctx.bus.device_added.emit(d.id)
    for l in borrowed.links.values():
        rec.ctx.bus.link_added.emit(l.id)

    assert _kinds(rec) == [P.GENESIS, P.PREEXISTING, ev.LOAD]
    acc = P.account_for_artifact(P.entries_of(rec.generate_proof()["proof"]))
    assert not acc.ok and len(acc.imported) == 2


def test_building_on_top_of_an_import_is_still_recorded(rec):
    """Suppression is per-element and one-shot: what the student adds AFTER the import is theirs."""
    rec.arm(CODE)
    borrowed = Topology("borrowed")
    borrowed.add_device("host")
    rec.note_load("lab3.gini", borrowed)
    rec.ctx.topology = borrowed
    for d in list(borrowed.devices.values()):
        rec.ctx.bus.device_added.emit(d.id)

    added = borrowed.add_device("router")
    rec.ctx.bus.device_added.emit(added.id)
    assert _kinds(rec)[-1] == ev.PLACE

    acc = P.account_for_artifact(P.entries_of(rec.generate_proof()["proof"]))
    assert acc.built == (added.name,) and len(acc.imported) == 1


# ---- it must never take the app down ------------------------------------------- #
def test_a_broken_topology_does_not_break_the_bus(rec):
    """An exception in a slot lands in the Qt event loop. Losing a session because the provenance
    feature threw is worse than having no provenance."""
    rec.arm(CODE)

    class Exploding:
        @property
        def devices(self):
            raise RuntimeError("boom")

    rec.ctx.topology = Exploding()
    rec.ctx.bus.device_added.emit("host-1")         # must not raise
    assert rec.last_error == "boom"
    assert ("warn", "Proof recording hit a problem and skipped an event: boom") in rec.ctx.logs


def test_the_chain_is_still_intact_after_a_swallowed_error(rec):
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.ctx.bus.device_added.emit("no-such-device")  # a stale id: recorded as nothing
    assert P.verify_entries(rec._chain.entries).ok


def test_a_failing_ui_callback_does_not_stop_recording(rec):
    rec.arm(CODE)
    rec.set_on_change(lambda: 1 / 0)
    _build_a_lan(rec)
    assert sum(1 for k in _kinds(rec) if k == ev.PLACE) == 2


# ---- cancelling, and coming back --------------------------------------------- #
def test_cancelling_leaves_the_mode_and_keeps_the_chain(rec):
    """Recording used to be a one-way door: nothing disarmed — not even generating a proof — so a
    student who armed the wrong code had to restart gBuilder to get out. Then it was a *pause*,
    which held the code and offered it back on a button: the mode was left but not exited. Cancel
    is the whole departure, and it is only safe to offer because nothing is lost."""
    rec.arm(CODE)
    _build_a_lan(rec)
    before = rec.count
    assert before > 0

    rec.cancel()
    assert rec.armed is False
    ok, message = rec.arm(CODE)                     # the way back is the code, same as the way in
    assert ok and rec.armed is True
    assert "Resumed" in message
    kinds = rec._chain.kinds()
    assert kinds.get(ev.PLACE, 0) == 2              # the SAME chain, not a second one


def test_cancelling_shows_nothing_about_the_code_afterwards(rec):
    """The point of the change. A strip that answers 'stop recording me' by printing the code back
    has not stopped; the status the widget paints from must carry no trace of it."""
    rec.arm(CODE)
    rec.cancel()
    st = rec.status()
    assert st["armed"] is False
    assert st["ticket"] == "" and st["short"] == ""
    flat = " ".join(str(v) for v in st.values())
    assert CODE.replace("-", "") not in flat.replace("-", "")


def test_nothing_is_recorded_after_cancelling(rec):
    """A stop that still recorded would be a lie told in the one place that must not lie."""
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.cancel()
    rec.ctx.add_device("router")                    # work done off the record
    rec.arm(CODE)
    assert rec._chain.kinds().get(ev.PLACE, 0) == 2   # the two from _build_a_lan, not three


def test_the_chain_says_where_recording_stopped(rec):
    """A gap the chain does not mention is a gap nobody can weigh. Written while still armed, so
    it is inside the hashed chain rather than a note beside it."""
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.cancel()
    chain = rec.store.load(_ticket.parse(CODE).code)
    assert chain.entries[-1].kind == ev.STOPPED


def test_the_chain_says_where_recording_came_back(rec):
    rec.arm(CODE)
    rec.cancel()
    rec.arm(CODE)
    kinds = [e.kind for e in rec._chain.entries]
    assert kinds[-1] == ev.RESUMED
    assert kinds.count(ev.PREEXISTING) == 1, "resuming is not a fresh start"


def test_the_gap_is_recorded_as_a_length_of_time(rec):
    """An instructor reading 'resumed after 3h' can weigh it; 'resumed' alone cannot be weighed."""
    rec.arm(CODE)
    rec.cancel()
    rec._now = lambda: time.time() + 7200
    rec.arm(CODE)
    assert rec._chain.entries[-1].data["away"] >= 7100


def test_work_done_while_recording_was_off_stays_unaccounted_for(rec):
    """The forgery this must not open. If resuming recorded what was on the canvas the way ARMING
    does, then cancel → import a classmate's file → resume would relabel their work as 'already
    there' — laundering an import through a pause. It must stay unknown."""
    from gini.domain import proof as P
    rec.arm(CODE)
    rec.cancel()
    smuggled = rec.ctx.add_device("router")
    rec.arm(CODE)
    entries = rec._chain.entries
    assert entries[-1].kind == ev.RESUMED
    assert smuggled.id in entries[-1].data["ids"], "the chain still sees what is on the canvas"

    rec._record(ev.submit(P.artifact_summary(rec.ctx.topology.to_dict()), []))
    acct = P.account_for_artifact(rec._chain.entries)
    assert smuggled.name in acct.unexplained
    assert smuggled.name not in acct.preexisting


def test_arming_a_different_code_starts_that_codes_own_chain(rec):
    """Two codes, two chains. Nothing about the first survives into the second."""
    other = mint(lambda n: bytes((i * 7 + 3) % 256 for i in range(n))).pretty
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.cancel()
    assert rec.arm(other)[0] is True
    assert rec._chain.ticket == _ticket.parse(other).code
    assert rec._chain.kinds().get(ev.PLACE, 0) == 0


def test_coming_back_to_a_chain_that_is_gone_starts_a_fresh_one(rec, tmp_path):
    """A chain deleted underneath us must not make re-arming fail — a student who lost their file
    should be recording again, not stuck."""
    rec.arm(CODE)
    rec.cancel()
    for f in tmp_path.rglob("*.jsonl"):
        f.unlink()
    ok, message = rec.arm(CODE)
    assert ok and "Recording under" in message


def test_generating_a_proof_leaves_the_recording_state_visible(rec):
    """The strip reads `submitted` to say so. It never did, which is why 'recording' stayed on
    screen with no way out after a hand-in."""
    rec.arm(CODE)
    _build_a_lan(rec)
    assert rec.status()["submitted"] is False
    assert rec.generate_proof()["ok"] is True
    assert rec.status()["submitted"] is True
    assert rec.armed is True                        # still armed — pausing is how you leave


# ---- what they ran on the live lab -------------------------------------------- #
def test_a_terminal_command_becomes_evidence_in_the_chain(rec):
    """The gap the report named itself: a chain could say "Started the lab" and then "Witnessed on
    the running network — none" while the student had pinged across their subnets and watched it
    work."""
    rec.arm(CODE)
    rec.note_command("M3", "ping -c 1 10.0.2.10", ["64 bytes from 10.0.2.10: seq=0 time=0.8 ms"])
    assert rec._chain.kinds().get("command") == 1
    entry = rec._chain.entries[-1]
    assert entry.data["on"] == "M3" and "ping" in entry.data["cmd"]
    assert entry.data["out"] == ["64 bytes from 10.0.2.10: seq=0 time=0.8 ms"]


def test_nothing_is_recorded_from_a_terminal_while_unarmed(rec):
    """The REC indicator is the student's notice that this is happening. With it off, it is not."""
    rec.note_command("M3", "ping 10.0.2.10", ["ok"])
    assert rec._chain is None


def test_an_empty_command_is_not_an_entry(rec):
    rec.arm(CODE)
    before = rec.count
    rec.note_command("M3", "   ", ["noise"])
    assert rec.count == before


def test_the_narration_shows_the_command_and_its_output(rec):
    from gini.domain import narration as N
    rec.arm(CODE)
    rec.note_command("M3", "ip route", ["default via 10.0.2.1", "10.0.2.0/24 dev eth0"])
    said = N.describe(rec._chain.entries[-1])
    assert "On M3: ip route" in said
    assert "default via 10.0.2.1" in said and "10.0.2.0/24 dev eth0" in said


def test_commands_are_counted_in_the_operation_summary(rec):
    from gini.domain import narration as N
    rec.arm(CODE)
    _build_a_lan(rec)
    rec.note_command("M1", "ping -c 1 M2", ["1 packets received"])
    text = N.narrate(rec._chain.entries)
    assert "command(s) run" in text and "1 command(s) run" in text

"""The proof-of-activity chain: append-only work log, integrity check, proof envelope.

A student arms recording with their assignment code; from then on gBuilder appends one hashed
entry per meaningful action. Each entry commits to its predecessor, so the log can be extended
but not rewritten, and the instructor can tell "built here" from "arrived from somewhere else".

**What this stops, and what it does not.** gBuilder runs on the student's machine, so a *patched
client* can fabricate entries and nothing here prevents that. The design aims one rung lower, at
the forgeries that actually happen: submitting a classmate's proof (the chain is bound to the
code), importing a friend's topology (the chain shows an import, and the submitted artifact is
not accounted for by any construction), and editing a proof after the fact (the hash chain plus
the MAC). The principle is that the cheapest forgery should be indistinguishable from doing the
work — and to produce a convincing construction chain you have to build the topology.

**Integrity, Phase 1.** ``mac = HMAC(K_app, head ‖ ticket)`` with `K_app` a constant in the app.
That is deterrence, not secrecy: it stops hand-written proofs and casual editing, and a determined
student can extract it. Phase 2 has the GINI server countersign chain heads, the anchor signature
replaces the MAC as the trust anchor, and the client then needs no secret at all — which is why
the envelope already carries an empty ``anchors`` list and why nothing below assumes the MAC is
the only possible anchor.

Pure Python (hashlib/hmac/json/pathlib), no Qt and no Docker, so all of it is unit-testable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .ticket import ALPHABET

FORMAT = "gini-proof"
VERSION = 1

# Entry 0 has no predecessor. A fixed all-zero digest rather than "" or None so every entry has
# exactly the same shape — the hash is then computed one way, never two.
GENESIS_PREV = "0" * 64

GENESIS, PREEXISTING, SUBMIT = "genesis", "preexisting", "submit"

# Phase 1 deterrence key. Deliberately in the source and deliberately not hidden: obfuscating it
# would buy a few hours against one student and cost every future reader an afternoon. See the
# module docstring for what replaces it.
_APP_KEY = b"gini-proof-of-activity/1"

# A single entry's `data` is bounded so a runaway signal (a chatty rider, a huge invoke response)
# cannot turn a chain into a gigabyte. Truncation is visible — the text ends in an ellipsis — so
# nobody later mistakes a clipped value for the real one.
MAX_TEXT = 240


class ChainError(ValueError):
    """A chain file that cannot be read as a chain."""


# ---- canonical serialization ---------------------------------------------- #
def canonical_json(obj) -> str:
    """The one serialization every hash is taken over: sorted keys, no whitespace, real UTF-8.

    Canonical means *reproducible* — the instructor's machine must hash an entry to exactly the
    digest the student's machine did. Sorting keys removes dict-ordering luck, dropping whitespace
    removes formatter luck, and `ensure_ascii=False` keeps a student's non-ASCII element name as
    the characters it actually is rather than an escape sequence whose casing could drift.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def clip(text, limit: int = MAX_TEXT) -> str:
    """A string bounded to `limit`, marked when it was cut (see MAX_TEXT)."""
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ---- entries --------------------------------------------------------------- #
@dataclass(frozen=True)
class Entry:
    seq: int
    t: float
    kind: str
    data: dict
    prev: str

    def payload(self) -> dict:
        """The exact dict that gets hashed and written. Nothing derived, nothing omitted — if a
        field is not in here it is not protected by the chain."""
        return {"seq": self.seq, "t": self.t, "kind": self.kind,
                "data": self.data, "prev": self.prev}

    def hash(self) -> str:
        return hashlib.sha256(canonical_json(self.payload()).encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, d: dict) -> Entry:
        try:
            return cls(seq=int(d["seq"]), t=float(d["t"]), kind=str(d["kind"]),
                       data=dict(d["data"]), prev=str(d["prev"]))
        except (KeyError, TypeError, ValueError) as e:
            raise ChainError(f"malformed entry: {e}") from e


def now_t() -> float:
    """Wall-clock seconds, rounded to milliseconds.

    Rounded because the value is hashed: a float must survive JSON round-tripping unchanged or the
    instructor recomputes a different digest. Milliseconds is far finer than any distinction a
    narration draws, and keeps the number short enough to read in a raw chain file.

    Phase 1 timestamps are the *student's* clock and are worth exactly what that is worth — they
    order the story, they do not prove when it happened. Server anchors are what will make them
    evidence (Phase 2).
    """
    return round(time.time(), 3)


class Chain:
    """An append-only sequence of hashed entries. Entry 0 is always the genesis that binds the
    whole chain to one assignment code."""

    def __init__(self, entries=None) -> None:
        self.entries: list[Entry] = list(entries or [])

    # -- construction ------------------------------------------------------- #
    @classmethod
    def start(cls, ticket: str, assignment: str = "", gini_version: str = "",
              t: float | None = None) -> Chain:
        chain = cls()
        chain.entries.append(Entry(
            seq=0, t=now_t() if t is None else round(float(t), 3), kind=GENESIS,
            data={"ticket": str(ticket), "assignment": clip(assignment),
                  "gini_version": clip(gini_version, 40)},
            prev=GENESIS_PREV))
        return chain

    def append(self, kind: str, data: dict, t: float | None = None) -> Entry:
        if not self.entries:
            raise ChainError("a chain must start with a genesis entry")
        entry = Entry(seq=len(self.entries), t=now_t() if t is None else round(float(t), 3),
                      kind=str(kind), data=dict(data or {}), prev=self.head_hash())
        self.entries.append(entry)
        return entry

    # -- reading ------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self.entries)

    @property
    def head(self) -> Entry | None:
        return self.entries[-1] if self.entries else None

    def head_hash(self) -> str:
        return self.entries[-1].hash() if self.entries else GENESIS_PREV

    @property
    def genesis(self) -> dict:
        return dict(self.entries[0].data) if self.entries else {}

    @property
    def ticket(self) -> str:
        return str(self.genesis.get("ticket", ""))

    @property
    def assignment(self) -> str:
        return str(self.genesis.get("assignment", ""))

    def kinds(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def has_submitted(self) -> bool:
        return any(e.kind == SUBMIT for e in self.entries)

    # -- serialization ------------------------------------------------------ #
    def to_jsonl(self) -> str:
        return "".join(canonical_json(e.payload()) + "\n" for e in self.entries)

    @classmethod
    def from_jsonl(cls, text: str) -> Chain:
        entries = []
        for n, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(Entry.from_payload(json.loads(line)))
            except (ValueError, ChainError) as e:
                raise ChainError(f"line {n} of the chain is not readable: {e}") from e
        return cls(entries)


# ---- verification ---------------------------------------------------------- #
@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    broken_seq: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "PASS" if self.ok else "FAIL"


def verify_entries(entries) -> Verdict:
    """Is this a well-formed, unbroken chain?

    When an entry is altered, the *next* entry's `prev` stops matching — so the entry we name is
    the altered one, not the one that noticed. That is the difference between "entry 12 (connect)
    has been altered" and "entry 13 doesn't verify", and the first is the only one an instructor
    can act on.
    """
    entries = list(entries)
    if not entries:
        return Verdict(False, "this proof carries no entries at all")
    g = entries[0]
    if g.kind != GENESIS or g.seq != 0 or g.prev != GENESIS_PREV:
        return Verdict(False, "the chain does not start with a genesis entry", 0)
    if not g.data.get("ticket"):
        return Verdict(False, "the genesis entry names no assignment code", 0)

    warnings: list[str] = []
    for i in range(1, len(entries)):
        cur, prev = entries[i], entries[i - 1]
        if cur.seq != i:
            return Verdict(False, f"entry {i} is numbered {cur.seq} — entries are missing "
                                  f"or have been reordered", i)
        expect = prev.hash()
        if cur.prev != expect:
            return Verdict(False, f"entry {prev.seq} ({prev.kind}) has been altered — entry "
                                  f"{cur.seq} does not follow from it", prev.seq)
        if cur.t < prev.t:
            # Not a failure: laptops sleep, clocks resync, and a student is not to blame for
            # either. It IS worth showing, because a chain that jumps backwards is also what a
            # clumsy forgery looks like.
            warnings.append(f"the clock goes backwards at entry {cur.seq}")
    return Verdict(True, "", None, tuple(warnings))


def compute_mac(head: str, ticket: str) -> str:
    """HMAC over the chain head bound to the code it was issued to.

    Binding the ticket in is what stops a student handing their proof to a classmate: the head
    alone would verify anywhere, the pair verifies only against the code it was issued to.
    """
    return hmac.new(_APP_KEY, f"{head}|{ticket}".encode("utf-8"), hashlib.sha256).hexdigest()


def receipt_code(proof: dict) -> str:
    """A short, sayable cross-check on a proof: 8 Crockford symbols of its MAC, as XXXX-XXXX.

    Not a second security mechanism — the file is the proof. It exists so a student can read
    something back over a help desk, and so an instructor glancing at a submission can see at once
    that two students did not hand in the same file.
    """
    mac = str(proof.get("mac", ""))
    if not mac:
        return ""
    n = int(mac[:10], 16)                       # 40 bits = exactly 8 base32 symbols
    sym = "".join(ALPHABET[(n >> (5 * i)) & 31] for i in range(7, -1, -1))
    return f"{sym[:4]}-{sym[4:]}"


def build_proof(chain: Chain, gini_version: str = "") -> dict:
    """Serialize a chain into the envelope an instructor loads.

    Everything outside `entries` is either derived from the chain or bound by the MAC, so there is
    nothing here an editor can quietly change: `ticket`/`assignment` are cross-checked against the
    genesis entry on verification, `head` is re-derived, and `mac` covers head and ticket.
    """
    head = chain.head_hash()
    return {
        "format": FORMAT,
        "version": VERSION,
        "ticket": chain.ticket,
        "assignment": chain.assignment,
        "gini_version": clip(gini_version or chain.genesis.get("gini_version", ""), 40),
        "entries": [e.payload() for e in chain.entries],
        "head": head,
        # Phase 2 fills this with GINI-server countersignatures over chain heads. Present and
        # empty from day one so a Phase-1 proof and a Phase-2 proof are the same shape of file.
        "anchors": [],
        "mac": compute_mac(head, chain.ticket),
    }


def verify_proof(proof: dict, expect_ticket: str | None = None) -> Verdict:
    """Full check of a loaded proof: shape, chain integrity, head, MAC, and — when the instructor
    supplies one — the assignment code it should have been issued to."""
    if not isinstance(proof, dict):
        return Verdict(False, "that is not a GINI proof file")
    if proof.get("format") != FORMAT:
        return Verdict(False, "that is not a GINI proof file")
    if int(proof.get("version", 0) or 0) > VERSION:
        return Verdict(False, f"this proof was written by a newer gBuilder "
                              f"(format {proof.get('version')})")
    try:
        entries = [Entry.from_payload(p) for p in proof.get("entries", [])]
    except ChainError as e:
        return Verdict(False, str(e))

    verdict = verify_entries(entries)
    if not verdict.ok:
        return verdict

    ticket = str(proof.get("ticket", ""))
    genesis = entries[0].data
    if ticket != str(genesis.get("ticket", "")):
        return Verdict(False, "the code on the outside of this proof is not the code the chain "
                              "was started with", 0)
    if str(proof.get("assignment", "")) != str(genesis.get("assignment", "")):
        return Verdict(False, "the assignment named on this proof is not the one the chain was "
                              "started with", 0)

    head = entries[-1].hash()
    if str(proof.get("head", "")) != head:
        return Verdict(False, f"entry {entries[-1].seq} ({entries[-1].kind}) has been altered — "
                              f"it does not match the head this proof commits to", entries[-1].seq)
    if not hmac.compare_digest(str(proof.get("mac", "")), compute_mac(head, ticket)):
        return Verdict(False, "the integrity code does not match — this proof was edited after "
                              "it was generated, or was not produced by gBuilder")

    if expect_ticket is not None:
        from .ticket import normalize
        want = normalize(expect_ticket)
        if want and want != ticket:
            return Verdict(False, f"this proof was issued to {ticket[:4]}…, not to "
                                  f"{want[:4]}… — it belongs to a different student")
    return Verdict(True, "", None, verdict.warnings)


# ---- the artifact: what was handed in, and where it came from -------------- #
def artifact_summary(topo_dict: dict) -> dict:
    """The fingerprint of the submitted topology that goes into the `submit` entry.

    The digest is what makes "borrowed topology, own proof" detectable at all; the element map is
    what makes the answer *legible* — it lets the narration say which four elements were never
    built here, instead of only that two hashes differ.
    """
    devices = list(topo_dict.get("devices", []) or [])
    links = list(topo_dict.get("links", []) or [])
    return {
        "sha256": hashlib.sha256(canonical_json(topo_dict).encode("utf-8")).hexdigest(),
        "devices": len(devices),
        "links": len(links),
        "elements": {str(d.get("id", "")): clip(d.get("name", ""), 64) for d in devices},
    }


@dataclass(frozen=True)
class Accounting:
    """Where each element of the submitted topology came from."""
    built: tuple[str, ...] = ()          # placed, action by action, under this code
    imported: tuple[str, ...] = ()       # arrived in one `load` — a file, not a construction
    preexisting: tuple[str, ...] = ()    # already on the canvas when recording was armed
    unexplained: tuple[str, ...] = ()    # in the submission, and the chain never saw it appear
    total: int = 0

    @property
    def ok(self) -> bool:
        """True only when every element in the submission was built under this code. This is the
        question the whole feature exists to answer, so it gets to be the plain-named property."""
        return bool(self.total) and len(self.built) == self.total

    @property
    def suspect(self) -> tuple[str, ...]:
        return self.imported + self.preexisting + self.unexplained


def account_for_artifact(entries) -> Accounting:
    """Match the submitted topology against the construction the chain records.

    Matching is by element **id**, not name: ids are stable across a rename, and a student who
    tidies up their names at the end of a lab must not be accused of importing their own work.
    Names are what comes back out, because an id means nothing to an instructor.
    """
    entries = list(entries)
    submit = next((e for e in reversed(entries) if e.kind == SUBMIT), None)
    if submit is None:
        return Accounting()
    elements = dict((submit.data.get("artifact", {}) or {}).get("elements", {}) or {})

    placed, imported, present = set(), set(), set()
    for e in entries:
        if e.kind == "place" and e.data.get("id"):
            placed.add(str(e.data["id"]))
        elif e.kind == "load":
            imported.update(str(i) for i in (e.data.get("ids", []) or []))
        elif e.kind == PREEXISTING:
            present.update(str(i) for i in (e.data.get("ids", []) or []))

    built, from_file, was_there, unknown = [], [], [], []
    for dev_id, name in sorted(elements.items(), key=lambda kv: kv[1]):
        label = name or dev_id
        if dev_id in placed:
            built.append(label)
        elif dev_id in imported:
            from_file.append(label)
        elif dev_id in present:
            was_there.append(label)
        else:
            unknown.append(label)
    return Accounting(tuple(built), tuple(from_file), tuple(was_there), tuple(unknown),
                      len(elements))


# ---- storage --------------------------------------------------------------- #
def _home() -> Path:
    # Same rule as app.paths.gini_home, replicated so `domain` stays free of an `app` import.
    return Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()


def proofs_root() -> Path:
    return _home() / "proofs"


class ChainStore:
    """The chain on disk: ``<root>/<ticket>/chain.jsonl``, one entry per line, append-only.

    JSONL rather than one JSON document because recording has to survive a crash mid-lab. An
    append that never rewrites what is already there cannot corrupt yesterday's work, and a
    half-written final line fails verification loudly instead of silently losing the tail.

    Keying the directory by ticket is what lets recording span sessions: relaunching gBuilder and
    typing the same code continues the same chain rather than starting a rival one.
    """

    def __init__(self, root=None) -> None:
        self.root = Path(root) if root is not None else proofs_root()

    def dir_for(self, ticket: str) -> Path:
        return self.root / str(ticket)

    def chain_path(self, ticket: str) -> Path:
        return self.dir_for(ticket) / "chain.jsonl"

    def proof_path(self, ticket: str) -> Path:
        return self.dir_for(ticket) / "proof.json"

    def exists(self, ticket: str) -> bool:
        return self.chain_path(ticket).is_file()

    def load(self, ticket: str) -> Chain | None:
        """The chain recorded under `ticket`, or None if there is none yet. Raises ChainError on a
        file that exists but cannot be read as a chain — the caller must decide what to tell the
        student, because silently starting a fresh chain would throw their work away."""
        p = self.chain_path(ticket)
        if not p.is_file():
            return None
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            raise ChainError(f"could not read {p}: {e}") from e
        return Chain.from_jsonl(text)

    def append(self, ticket: str, entry: Entry) -> None:
        p = self.chain_path(ticket)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(entry.payload()) + "\n")

    def write_chain(self, ticket: str, chain: Chain) -> Path:
        """Write a whole chain (used when a chain is created, not on every append)."""
        p = self.chain_path(ticket)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(chain.to_jsonl(), encoding="utf-8")
        return p

    def write_proof(self, ticket: str, proof: dict) -> Path:
        p = self.proof_path(ticket)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Indented, unlike the chain: a proof is a thing a human opens, mails and inspects, and
        # the hashes inside it are computed over the entries, never over this file's layout.
        p.write_text(json.dumps(proof, indent=2, ensure_ascii=False), encoding="utf-8")
        return p


def load_proof(path) -> dict:
    """Read a proof file. Raises ChainError with a showable reason (teacher mode displays it)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise ChainError(f"could not open that file: {e}") from e
    except ValueError as e:
        raise ChainError(f"that file is not a GINI proof (it is not valid JSON): {e}") from e


def parse_proof(text: str) -> dict:
    """Read a proof pasted into a text box rather than loaded from disk."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ChainError(f"that is not a GINI proof (it is not valid JSON): {e}") from e
    if not isinstance(data, dict):
        raise ChainError("that is not a GINI proof")
    return data


def entries_of(proof: dict) -> list[Entry]:
    """The proof's entries as Entry objects — for narration, which does not care whether the
    chain verified. Unreadable entries are skipped: a broken proof must still be *readable*, or
    the instructor is told FAIL and shown nothing to judge it by."""
    out = []
    for p in proof.get("entries", []) or []:
        try:
            out.append(Entry.from_payload(p))
        except ChainError:
            continue
    return out

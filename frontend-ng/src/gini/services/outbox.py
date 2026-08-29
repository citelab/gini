"""Submissions that have not reached the Teaching Center yet.

The hole this fills: gBuilder generates a proof, shows the student a receipt, and the upload fails
because the wifi dropped. The student walks away holding a receipt that is *correct* — the server
computes it from the proof's MAC exactly as gBuilder does — but the Teaching Center has never heard
of it. The teacher types it in and is told "no submission with that receipt", and the two of them
have no way to tell who is wrong.

So every submission is written here BEFORE it is attempted, and stays until the server has it.
gBuilder flushes the outbox on launch and whenever it next talks to the course server, so the usual
recovery is that the student reopens gBuilder on campus and it quietly catches up.

**Nothing is ever deleted because the server said no.** An entry leaves only when the work is
demonstrably at the far end — accepted, or refused as a duplicate, which means an earlier attempt
already landed. A refusal for any other reason keeps the entry with its last error, so a student
whose code expired overnight still has a complete package to hand a teacher rather than an
apology. These files are a few KB; a lost evening is not recoverable.

Deliberately Qt-free: this is where the decisions live, so they can be tested without a display,
and the UI layer only has to marshal the result back to the GUI thread.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..domain.proof import _home, receipt_code

# Refusals that mean "it is already there". Anything else is kept for a human.
SETTLED = {"duplicate", "already_used"}


def outbox_root() -> Path:
    return _home() / "outbox"


def _path(root: Path, receipt: str) -> Path:
    # Keyed by receipt, so re-queuing the same proof overwrites rather than piling up duplicates:
    # a student who presses Generate twice has one submission, not two.
    return root / f"{receipt.replace('-', '')}.json"


def queue(proof: dict, topology: dict | None = None, *, root: Path | None = None,
          now: float | None = None) -> Path:
    """Record a submission as pending. Called before the upload is attempted, never after."""
    root = root if root is not None else outbox_root()
    root.mkdir(parents=True, exist_ok=True)
    receipt = receipt_code(proof)
    p = _path(root, receipt)
    # Keep the original queue time across retries: how long a submission has been stuck is the
    # thing a teacher actually wants to know.
    queued = now if now is not None else time.time()
    if p.exists():
        try:
            queued = float(json.loads(p.read_text(encoding="utf-8")).get("queued", queued))
        except Exception:                                          # noqa: BLE001
            pass
    p.write_text(json.dumps({
        "receipt": receipt,
        "code": str(proof.get("ticket", "")),
        "assignment": str(proof.get("assignment", "")),
        "proof": proof,
        "topology": topology,
        "queued": queued,
        "attempts": 0,
        "last_error": "",
    }, indent=2), encoding="utf-8")
    return p


def pending(root: Path | None = None) -> list[dict]:
    """Everything still waiting, oldest first."""
    root = root if root is not None else outbox_root()
    out = []
    for f in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            out.append(dict(json.loads(f.read_text(encoding="utf-8")), _path=str(f)))
        except Exception:                                          # noqa: BLE001
            continue          # a half-written file is not worth crashing a launch over
    return sorted(out, key=lambda e: e.get("queued", 0))


def forget(receipt: str, root: Path | None = None) -> None:
    root = root if root is not None else outbox_root()
    _path(root, receipt).unlink(missing_ok=True)


def summary(root: Path | None = None, now: float | None = None) -> dict:
    """One dict describing everything still waiting, for the UI to render.

    Queueing already recorded `attempts`, `last_error` and `queued` per entry, and none of it ever
    reached the screen — so a submission that had been retried and refused eleven times looked
    exactly like one that had never been tried. "Saved, not sent" is only reassuring if the student
    can also see that it is still being worked on, and how long it has been stuck.
    """
    items = pending(root)
    now = now if now is not None else time.time()
    if not items:
        return {"count": 0, "oldest_age_s": 0.0, "attempts": 0, "last_error": "", "receipts": []}
    oldest = min(float(e.get("queued", now) or now) for e in items)
    last_error = ""
    for e in items:                       # the most recent non-empty error wins
        if e.get("last_error"):
            last_error = str(e["last_error"])
    return {"count": len(items),
            "oldest_age_s": max(0.0, now - oldest),
            "attempts": max(int(e.get("attempts", 0) or 0) for e in items),
            "last_error": last_error,
            "receipts": [str(e.get("receipt", "")) for e in items]}


def flush(url: str, send, *, root: Path | None = None, now: float | None = None) -> dict:
    """Try every pending submission. Returns `{sent, kept, errors}`.

    `send(url, code, proof, topology) -> dict` is injected rather than imported so this can be
    tested without a server, and so a caller can flush through any transport.
    """
    root = root if root is not None else outbox_root()
    sent, kept, errors = [], [], []
    for entry in pending(root):
        receipt = entry.get("receipt", "")
        try:
            answer = send(url, entry.get("code", ""), entry.get("proof") or {},
                          entry.get("topology"))
        except Exception as e:                                     # noqa: BLE001
            answer = {"ok": False, "error": str(e)}
        if answer.get("ok") or answer.get("reason") in SETTLED:
            forget(receipt, root)
            sent.append(receipt)
            continue
        kept.append(receipt)
        errors.append(answer.get("error", "refused"))
        _bump(Path(entry["_path"]), answer.get("error", ""), now)
    return {"sent": sent, "kept": kept, "errors": errors}


def _bump(path: Path, error: str, now: float | None) -> None:
    """Record that an attempt happened and why it failed. Best effort — a failure to write the
    bookkeeping must never lose the entry itself."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["attempts"] = int(data.get("attempts", 0)) + 1
        data["last_error"] = error
        data["last_attempt"] = now if now is not None else time.time()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:                                              # noqa: BLE001
        pass

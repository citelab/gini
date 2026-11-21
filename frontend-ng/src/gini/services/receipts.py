"""Every receipt this computer has handed in, read back off disk.

A receipt was shown exactly once — in the "Handed in" dialog — and the student was told to give it
to their instructor. Students closed that dialog without writing it down and then had no way to see
it again, so the request reached the instructor instead, who had no way to look it up either: the
Teaching Center finds work BY receipt.

Nothing new has to be stored to fix that. Every hand-in leaves `proofs/<code>/proof.json` behind
(it is also how a finished code is refused when typed again), and the receipt is a pure function of
that file's MAC — `proof.receipt_code`, which the server computes the same way. So the list is
derived, not kept: it cannot disagree with what was sent, and it covers every hand-in made before
this existed.

Whether it reached the server is read from the outbox. A submission is queued there before it is
attempted and leaves only once the server has it (`services/outbox`), so "not in the outbox" is
"sent". That is the one thing a student holding a receipt needs to know alongside it.

Qt-free, so it is tested without a display.
"""
from __future__ import annotations

from pathlib import Path

from ..domain import proof as _proof
from ..domain import ticket as _ticket
from . import outbox


def _pretty(code: str) -> str:
    try:
        return _ticket.Ticket(code).pretty
    except Exception:                                   # noqa: BLE001 — show it as it is
        return code


def _handed_in_at(proof: dict, path: Path) -> float:
    """When it was handed in: the `submit` entry's time, which is in the sealed chain. The file's
    mtime is only a fallback for a proof whose last entry is something else."""
    entries = proof.get("entries") or []
    last = entries[-1] if entries else {}
    if isinstance(last, dict) and last.get("kind") == _proof.SUBMIT:
        try:
            return float(last.get("t", 0))
        except (TypeError, ValueError):
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def handed_in(root: Path | None = None, outbox_root: Path | None = None) -> list[dict]:
    """One dict per proof on this computer, newest first:
    `{receipt, code, assignment, when, sent, path}`.

    A proof that cannot be read is skipped rather than reported: this is a list a student scans for
    one line, and a broken file is not a receipt anyone was given.
    """
    root = Path(root) if root is not None else _proof.proofs_root()
    waiting = {e.get("receipt") for e in outbox.pending(outbox_root)}
    out = []
    for path in root.glob("*/proof.json") if root.is_dir() else []:
        try:
            proof = _proof.load_proof(path)
        except _proof.ChainError:
            continue
        receipt = _proof.receipt_code(proof)
        if not receipt:
            continue
        code = str(proof.get("ticket", "") or path.parent.name)
        out.append({"receipt": receipt,
                    "code": _pretty(code),
                    "assignment": str(proof.get("assignment", "")),
                    "when": _handed_in_at(proof, path),
                    "sent": receipt not in waiting,
                    "path": str(path)})
    return sorted(out, key=lambda r: r["when"], reverse=True)

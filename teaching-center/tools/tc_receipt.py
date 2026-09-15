#!/usr/bin/env python3
"""Read a proof.json a student e-mailed you: its receipt, its code, and whether it holds up.

    python3 tools/tc_receipt.py proof.json
    python3 tools/tc_receipt.py ~/Downloads/*.json        # a morning's mail, one table

**Students do not write the receipt down.** They finish, the receipt appears, they close the
window, and what arrives is the file. That is not a problem to solve at the student's end — the
file already contains everything:

  * the receipt is `base32(mac[:40 bits])`, and the mac is in the file;
  * the ticket is in the file too, despite being easy to assume it is not, because the mac binds
    to it and verification cross-checks it against the genesis entry;
  * so nothing here needs the course server, a network, or the code the student was vended.

Offline by design. This runs on a laptop with a mailbox open and no VPN, which is when it is
needed. What it CANNOT tell you is whether the Center already has that submission — look the
receipt up in the console for that, which is the one thing the console is already good at.

Reports, never judges: a proof that fails verification still prints, with the reason, because
"this file is damaged" is a thing a marker needs to see rather than have hidden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# gini-core, which the Teaching Center already depends on.
try:
    from gini.domain.proof import receipt_code, verify_proof
except ImportError:                                        # pragma: no cover - a plain env
    sys.exit("gini-core is not importable. Run this from a checkout with\n"
             "    PYTHONPATH=core/src python3 teaching-center/tools/tc_receipt.py <file>")


def read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"file": path.name, "error": f"{type(e).__name__}: {e}"}
    if not isinstance(data, dict):
        return {"file": path.name, "error": "not a GINI proof (the file is not an object)"}
    v = verify_proof(data)
    return {
        "file": path.name,
        "receipt": receipt_code(data) or "(no mac — not a proof)",
        "ticket": str(data.get("ticket") or ""),
        "assignment": str(data.get("assignment") or ""),
        "entries": len(data.get("entries") or []),
        "gini": str(data.get("gini_version") or ""),
        "ok": bool(getattr(v, "ok", False)),
        "why": "" if getattr(v, "ok", False) else str(getattr(v, "reason", "") or "did not verify"),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__.strip().splitlines()[0])
        print("\nusage: tc_receipt.py <proof.json> [more.json …]")
        return 2

    rows = [read(Path(a)) for a in args]
    width = max((len(r["file"]) for r in rows), default=4)
    for r in rows:
        if r.get("error"):
            print(f"  {r['file']:<{width}}  !! {r['error']}")
            continue
        mark = "ok " if r["ok"] else "BAD"
        print(f"  {r['file']:<{width}}  {r['receipt']}  {mark}  "
              f"{r['assignment'] or '(no assignment)'}  ticket={r['ticket'] or '(none)'}  "
              f"{r['entries']} entries  gBuilder {r['gini'] or '?'}")
        if not r["ok"]:
            print(f"  {'':<{width}}     why: {r['why']}")
    bad = sum(1 for r in rows if r.get("error") or not r.get("ok"))
    if len(rows) > 1:
        print(f"\n  {len(rows)} file(s), {bad} that a marker should look at by hand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

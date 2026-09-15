"""Reading a proof.json a student e-mailed, with no server and no receipt written down.

Students finish, the receipt appears, they close the window, and what arrives is the file. That is
not a problem to fix at the student's end: the file already contains everything, and it is easy to
assume otherwise — the ticket looks like something only the server should know, and the receipt
looks like something only the server should issue. Neither is true.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "teaching-center" / "tools"
pytestmark = pytest.mark.skipif(not _TOOLS.is_dir(), reason="teaching-center not checked out")
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from gini.domain import proof as P                                      # noqa: E402


def _proof(ticket="LAB3-7QF2-M91K", assignment="comp310/lab3"):
    c = P.Chain.start(ticket=ticket, assignment=assignment, gini_version="6.13.1")
    c.append("note_lab_open", {"device": "M1"})
    return P.build_proof(c, "6.13.1")


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")
    return p


def test_the_receipt_is_in_the_file_and_needs_nothing_else(tmp_path):
    """No server, no network, and not the code the student was vended."""
    import tc_receipt

    pr = _proof()
    row = tc_receipt.read(_write(tmp_path, "proof.json", pr))
    assert row["receipt"] == P.receipt_code(pr)
    assert row["ok"] is True


def test_the_receipt_does_not_use_the_ticket(tmp_path):
    """`receipt_code` reads only the mac. Worth pinning, because "we do not have the ticket" is the
    reason people assume the receipt cannot be recovered."""
    pr = _proof()
    assert P.receipt_code({"mac": pr["mac"]}) == P.receipt_code(pr)


def test_the_ticket_is_in_the_file_too(tmp_path):
    """Also easy to assume otherwise. The mac binds to it and verification cross-checks it against
    the genesis entry, so it has to be there."""
    import tc_receipt

    row = tc_receipt.read(_write(tmp_path, "proof.json", _proof()))
    assert row["ticket"] == "LAB3-7QF2-M91K"
    assert row["assignment"] == "comp310/lab3"


def test_a_tampered_proof_still_prints_and_says_why(tmp_path):
    """Reports, never judges. "This file is damaged" is a thing a marker needs to SEE, not have
    hidden — and the receipt still prints, because it is what they will search the console for."""
    import tc_receipt

    bad = _proof()
    bad["head"] = "0" * 64
    row = tc_receipt.read(_write(tmp_path, "tampered.json", bad))
    assert row["ok"] is False
    assert row["why"]
    assert row["receipt"], "a damaged proof still has a receipt to look up"


def test_something_that_is_not_a_proof_is_not_a_crash(tmp_path):
    import tc_receipt

    assert "error" in tc_receipt.read(_write(tmp_path, "notes.json", "{]"))
    assert "error" in tc_receipt.read(_write(tmp_path, "list.json", [1, 2, 3]))
    assert "error" in tc_receipt.read(tmp_path / "missing.json")


def test_a_mailbox_full_of_files_reads_as_one_table(tmp_path, capsys):
    import tc_receipt

    _write(tmp_path, "a.json", _proof(ticket="AAAA-1111-BBBB"))
    _write(tmp_path, "b.json", _proof(ticket="CCCC-2222-DDDD"))
    _write(tmp_path, "junk.json", "not json")
    assert tc_receipt.main([str(tmp_path / n) for n in ("a.json", "b.json", "junk.json")]) == 0
    out = capsys.readouterr().out
    assert "AAAA-1111-BBBB" in out and "CCCC-2222-DDDD" in out
    assert "1 that a marker should look at by hand" in out


def test_two_students_who_did_different_work_get_different_receipts(tmp_path):
    """The thing the receipt is actually for: an instructor glancing at two submissions sees at
    once that they are not the same file."""
    import tc_receipt

    one = tc_receipt.read(_write(tmp_path, "one.json", _proof(ticket="AAAA-1111-BBBB")))
    two = tc_receipt.read(_write(tmp_path, "two.json", _proof(ticket="CCCC-2222-DDDD")))
    assert one["receipt"] != two["receipt"]

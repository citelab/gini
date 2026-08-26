"""The chain and the proof envelope — the part that has to hold up when someone edits it.

These tests are the threat model written as code. Each one is a forgery a student might actually
attempt in a classroom: rewrite an entry, hand in a classmate's proof, generate a proof over a
topology that arrived in a file. The one attack deliberately absent is a patched gBuilder, which
no client-side scheme stops and which the design says so plainly.
"""
import json

import pytest

from gini.domain import proof as P
from gini.domain.ticket import mint

TICKET_A = mint(lambda n: bytes((i * 11 + 3) % 256 for i in range(n))).code
TICKET_B = mint(lambda n: bytes((i * 29 + 7) % 256 for i in range(n))).code


def _chain(ticket: str = TICKET_A) -> P.Chain:
    """A small, ordinary session: two elements placed, wired, run, checked, submitted."""
    c = P.Chain.start(ticket, assignment="lan-basics", gini_version="1.2.3", t=1000.0)
    c.append("place", {"id": "host-1", "type": "host", "name": "M1"}, t=1010.0)
    c.append("place", {"id": "switch-2", "type": "switch", "name": "S1"}, t=1020.0)
    c.append("connect", {"a": "M1", "b": "S1", "why": "Join a LAN."}, t=1030.0)
    c.append("run", {"ok": True, "msg": "started"}, t=1100.0)
    c.append("witness", {"probe": "reach(host -> host) == ok", "verdict": "ok"}, t=1140.0)
    return c


def _artifact(names) -> dict:
    devices = [{"id": i, "name": n, "type_key": "host"} for i, n in names.items()]
    return P.artifact_summary({"name": "lab", "devices": devices, "links": []})


def _submitted(chain: P.Chain, names=None) -> P.Chain:
    names = names if names is not None else {"host-1": "M1", "switch-2": "S1"}
    chain.append("submit", {"artifact": _artifact(names),
                            "objectives": [{"id": "reach", "say": "M1 reaches S1",
                                            "kind": "behavioral", "status": "met"}]}, t=1200.0)
    return chain


# ---- canonical form -------------------------------------------------------- #
def test_canonical_json_is_sorted_and_compact():
    assert P.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert " " not in P.canonical_json({"a": [1, 2]})


def test_canonical_json_keeps_non_ascii_as_itself():
    # A student's element name must hash the same on both machines, escapes or not.
    assert P.canonical_json({"n": "Réseau"}) == '{"n":"Réseau"}'


def test_entry_hash_is_reproducible_and_content_bound():
    e = P.Entry(1, 5.0, "place", {"name": "M1"}, P.GENESIS_PREV)
    assert e.hash() == P.Entry(1, 5.0, "place", {"name": "M1"}, P.GENESIS_PREV).hash()
    assert e.hash() != P.Entry(1, 5.0, "place", {"name": "M2"}, P.GENESIS_PREV).hash()


def test_timestamps_survive_the_json_round_trip():
    """Entry hashes are taken over the serialized form, so a float that does not round-trip would
    make the instructor's machine compute a different digest from the student's."""
    c = _chain()
    again = P.Chain.from_jsonl(c.to_jsonl())
    assert [e.hash() for e in again.entries] == [e.hash() for e in c.entries]


# ---- chain integrity -------------------------------------------------------- #
def test_a_clean_chain_verifies():
    assert P.verify_entries(_chain().entries).ok


def test_a_chain_must_start_with_genesis():
    c = _chain()
    v = P.verify_entries(c.entries[1:])
    assert not v.ok and "genesis" in v.reason


def test_editing_an_entry_names_that_entry():
    c = _chain()
    entries = list(c.entries)
    entries[2] = P.Entry(2, entries[2].t, "place",
                         {"id": "switch-2", "type": "router", "name": "S1"}, entries[2].prev)
    v = P.verify_entries(entries)
    assert not v.ok and v.broken_seq == 2 and "altered" in v.reason


def test_a_missing_entry_is_reported_as_missing():
    c = _chain()
    entries = c.entries[:2] + c.entries[3:]
    v = P.verify_entries(entries)
    assert not v.ok and "missing" in v.reason


def test_a_clock_that_goes_backwards_warns_but_does_not_fail():
    c = P.Chain.start(TICKET_A, t=1000.0)
    c.append("place", {"id": "a", "name": "M1"}, t=900.0)
    v = P.verify_entries(c.entries)
    assert v.ok and v.warnings and "clock" in v.warnings[0]


# ---- the proof envelope ----------------------------------------------------- #
def test_a_generated_proof_verifies():
    proof = P.build_proof(_submitted(_chain()), "1.2.3")
    v = P.verify_proof(proof)
    assert v.ok and v.label == "PASS"


def test_editing_one_byte_of_a_proof_fails_and_names_the_entry():
    proof = P.build_proof(_submitted(_chain()))
    proof["entries"][3]["data"]["why"] = "Join a LAN!"      # one character
    v = P.verify_proof(proof)
    assert not v.ok and v.broken_seq == 3


def test_editing_the_last_entry_is_caught_by_the_head():
    proof = P.build_proof(_submitted(_chain()))
    last = proof["entries"][-1]
    last["data"]["objectives"][0]["status"] = "met "        # a trailing space
    v = P.verify_proof(proof)
    assert not v.ok and v.broken_seq == last["seq"]


def test_adding_an_objective_to_a_finished_proof_is_caught():
    """The forgery this is really for: 'I'll just add the one I missed'."""
    proof = P.build_proof(_submitted(_chain()))
    proof["entries"][-1]["data"]["objectives"].append(
        {"id": "extra", "say": "invented", "kind": "structural", "status": "met"})
    assert not P.verify_proof(proof).ok


def test_recomputing_the_hashes_still_fails_on_the_mac():
    """A student who rebuilds the chain honestly-shaped but with different content has to forge
    the MAC too — which is what the app key is there to make inconvenient."""
    forged = _chain()
    forged.append("witness", {"probe": "reach(a -> b) == ok", "verdict": "ok"}, t=1150.0)
    proof = P.build_proof(_submitted(forged))
    proof["mac"] = "0" * 64
    v = P.verify_proof(proof)
    assert not v.ok and "integrity code" in v.reason


def test_a_proof_belongs_to_the_code_it_was_issued_to():
    proof = P.build_proof(_submitted(_chain(TICKET_A)))
    assert P.verify_proof(proof, expect_ticket=TICKET_A).ok
    v = P.verify_proof(proof, expect_ticket=TICKET_B)
    assert not v.ok and "different student" in v.reason


def test_the_expected_code_may_be_typed_with_hyphens():
    proof = P.build_proof(_submitted(_chain(TICKET_A)))
    pretty = "-".join(TICKET_A[i:i + 4] for i in range(0, 12, 4))
    assert P.verify_proof(proof, expect_ticket=pretty).ok


def test_relabelling_the_envelope_does_not_relabel_the_chain():
    proof = P.build_proof(_submitted(_chain(TICKET_A)))
    proof["ticket"] = TICKET_B
    v = P.verify_proof(proof)
    assert not v.ok and "not the code the chain was started with" in v.reason


def test_a_file_that_is_not_a_proof_is_refused_politely():
    assert not P.verify_proof({"hello": "world"}).ok
    assert not P.verify_proof("nonsense").ok
    with pytest.raises(P.ChainError):
        P.parse_proof("{not json")


def test_receipt_code_is_short_sayable_and_stable():
    proof = P.build_proof(_submitted(_chain()))
    r = P.receipt_code(proof)
    assert len(r) == 9 and r[4] == "-"
    assert r == P.receipt_code(proof)
    assert r != P.receipt_code(P.build_proof(_submitted(_chain(TICKET_B))))


# ---- provenance: where the submitted topology came from --------------------- #
def test_a_built_topology_is_fully_accounted_for():
    acc = P.account_for_artifact(_submitted(_chain()).entries)
    assert acc.ok and acc.total == 2 and set(acc.built) == {"M1", "S1"}


def test_an_imported_topology_is_not_counted_as_built():
    """Import a friend's file, generate your own proof: the chain shows one load, and every
    element in the submission traces back to it rather than to a placement."""
    c = P.Chain.start(TICKET_A, assignment="lan-basics", t=1000.0)
    c.append("load", {"source": "lab3.gini", "devices": 2, "links": 1,
                      "ids": ["host-9", "switch-9"], "names": ["M1", "S1"]}, t=1005.0)
    _submitted(c, {"host-9": "M1", "switch-9": "S1"})
    acc = P.account_for_artifact(c.entries)
    assert not acc.ok
    assert set(acc.imported) == {"M1", "S1"} and not acc.built


def test_work_done_before_arming_is_reported_as_such():
    c = P.Chain.start(TICKET_A, t=1000.0)
    c.append("preexisting", {"devices": 1, "links": 0, "ids": ["host-1"], "names": ["M1"]},
             t=1001.0)
    _submitted(c, {"host-1": "M1"})
    acc = P.account_for_artifact(c.entries)
    assert acc.preexisting == ("M1",) and not acc.ok


def test_an_element_the_chain_never_saw_is_unexplained():
    _c = _chain()
    _submitted(_c, {"host-1": "M1", "router-7": "R9"})
    acc = P.account_for_artifact(_c.entries)
    assert acc.unexplained == ("R9",) and acc.built == ("M1",)


def test_accounting_follows_a_rename():
    """Matching is by id, so tidying up names at the end of a lab is not an accusation."""
    c = _chain()
    c.append("configure", {"id": "host-1", "name": "M1", "changes": {"Name": "web"}}, t=1150.0)
    _submitted(c, {"host-1": "web", "switch-2": "S1"})
    assert P.account_for_artifact(c.entries).ok


def test_no_submission_means_nothing_to_account_for():
    assert P.account_for_artifact(_chain().entries).total == 0


def test_the_artifact_digest_moves_with_the_topology():
    a = P.artifact_summary({"name": "lab", "devices": [{"id": "h1", "name": "M1"}], "links": []})
    b = P.artifact_summary({"name": "lab", "devices": [{"id": "h1", "name": "M2"}], "links": []})
    assert a["sha256"] != b["sha256"]
    assert a["elements"] == {"h1": "M1"} and a["devices"] == 1


# ---- storage ---------------------------------------------------------------- #
def test_the_chain_survives_a_restart(tmp_path):
    store = P.ChainStore(tmp_path)
    c = P.Chain.start(TICKET_A, assignment="lan-basics", t=1000.0)
    store.write_chain(TICKET_A, c)
    store.append(TICKET_A, c.append("place", {"id": "host-1", "name": "M1"}, t=1010.0))

    reopened = store.load(TICKET_A)
    assert len(reopened) == 2 and reopened.ticket == TICKET_A
    # …and keeps going where it left off, which is what "spans multiple sessions" means.
    store.append(TICKET_A, reopened.append("place", {"id": "switch-2", "name": "S1"}, t=1020.0))
    assert P.verify_entries(store.load(TICKET_A).entries).ok


def test_an_unknown_ticket_has_no_chain(tmp_path):
    assert P.ChainStore(tmp_path).load(TICKET_A) is None


def test_a_truncated_chain_file_is_an_error_not_a_silent_restart(tmp_path):
    """Losing a student's recorded work by quietly starting over would be the worst possible
    failure mode for this feature, so a damaged file has to be loud."""
    store = P.ChainStore(tmp_path)
    store.write_chain(TICKET_A, _chain())
    p = store.chain_path(TICKET_A)
    p.write_text(p.read_text()[:-40])
    with pytest.raises(P.ChainError):
        store.load(TICKET_A)


def test_the_proof_file_is_readable_json(tmp_path):
    store = P.ChainStore(tmp_path)
    proof = P.build_proof(_submitted(_chain()))
    path = store.write_proof(TICKET_A, proof)
    assert P.verify_proof(json.loads(path.read_text())).ok
    assert P.verify_proof(P.load_proof(path)).ok


def test_the_envelope_is_anchor_ready():
    """Phase 2 fills `anchors` with GINI-server countersignatures; Phase 1 keeps the shape."""
    assert P.build_proof(_submitted(_chain()))["anchors"] == []


def test_proofs_live_under_the_gini_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    assert P.proofs_root() == tmp_path / "proofs"

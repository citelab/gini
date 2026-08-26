"""The transcript an instructor actually reads.

(Named `test_proof_narration` because `tests/test_narration.py` already belongs to the *mission*
narrator in `agent/narration.py` — a different thing entirely, and one that must keep its name.)

The chain is the evidence; this is its reading, and the bar is "skimmable on a Sunday with thirty
of them to get through". So the tests here are mostly about *what a marker must not be able to
miss*: an import that did not happen here, elements the chain cannot account for, and the absence
of any live check. Nothing in the narration may say more than the entries support — if a sentence
cannot be pointed at an entry, it does not belong.
"""
from gini.domain import narration as N
from gini.domain import proof as P
from gini.domain.ticket import mint

TICKET = mint(lambda n: bytes((i * 23 + 9) % 256 for i in range(n))).code
T0 = 1755990000.0


def _built() -> P.Chain:
    c = P.Chain.start(TICKET, assignment="lan-basics", gini_version="1.2.3", t=T0)
    c.append("preexisting", {"devices": 0, "links": 0, "ids": [], "names": []}, t=T0 + 1)
    c.append("place", {"id": "host-1", "type": "host", "name": "M1"}, t=T0 + 60)
    c.append("place", {"id": "switch-2", "type": "switch", "name": "S1"}, t=T0 + 90)
    c.append("connect", {"a": "M1", "b": "S1",
                         "why": "Join a LAN — a switch links machines on the same subnet."},
             t=T0 + 120)
    c.append("configure", {"id": "host-1", "name": "M1", "changes": {"OS": "alpine"}}, t=T0 + 150)
    c.append("run", {"ok": True, "msg": "up"}, t=T0 + 600)
    c.append("open_console", {"id": "host-1", "name": "M1"}, t=T0 + 640)
    c.append("witness", {"probe": "reach(host -> host) == ok", "verdict": "ok"}, t=T0 + 700)
    c.append("objective", {"id": "reach", "say": "Every machine reaches every other",
                           "from": "unmet", "to": "met"}, t=T0 + 700)
    return c


def _submit(c: P.Chain, elements) -> P.Chain:
    artifact = P.artifact_summary({
        "name": "lab",
        "devices": [{"id": i, "name": n, "type_key": "host"} for i, n in elements.items()],
        "links": []})
    c.append("submit", {"artifact": artifact,
                        "objectives": [{"id": "reach", "say": "Every machine reaches every other",
                                        "kind": "behavioral", "status": "met"}]}, t=T0 + 800)
    return c


def _imported() -> P.Chain:
    c = P.Chain.start(TICKET, assignment="lan-basics", t=T0)
    c.append("preexisting", {"devices": 0, "links": 0, "ids": [], "names": []}, t=T0 + 1)
    c.append("load", {"source": "lab3.gini", "devices": 2, "links": 1,
                      "ids": ["host-9", "switch-9"], "names": ["M1", "S1"]}, t=T0 + 30)
    c.append("run", {"ok": True, "msg": "up"}, t=T0 + 60)
    return _submit(c, {"host-9": "M1", "switch-9": "S1"})


# ---- one entry, one sentence ------------------------------------------------- #
def test_a_connection_reads_as_the_teaching_reason_not_a_link_id():
    line = N.describe(_built().entries[4])
    assert line.startswith("Connected M1 to S1")
    assert "Join a LAN" in line and "link-" not in line


def test_every_kind_renders_as_a_sentence():
    for e in _submit(_built(), {"host-1": "M1", "switch-2": "S1"}).entries:
        line = N.describe(e)
        assert line and line[0].isupper() and line.rstrip().endswith((".", "!"))


def test_an_unknown_kind_does_not_crash_the_transcript():
    """A proof written by a later gBuilder may carry entries this one has never heard of; the
    instructor must still get a readable transcript."""
    c = _built()
    c.append("teleported", {"whatever": 1}, t=T0 + 999)
    assert "teleported" in N.narrate(c.entries)


def test_switching_to_an_empty_experiment_is_not_called_an_import():
    """Opening a project and starting a new experiment go down the same code path; 'IMPORTED 0
    elements' would be alarming nonsense on a marker's screen."""
    c = P.Chain.start(TICKET, t=T0)
    c.append("load", {"source": "experiment-2", "devices": 0, "links": 0,
                      "ids": [], "names": []}, t=T0 + 5)
    line = N.describe(c.entries[1])
    assert "empty board" in line and "IMPORTED" not in line


def test_a_failed_run_reads_as_a_failed_run():
    c = P.Chain.start(TICKET, t=T0)
    c.append("run", {"ok": False, "msg": "Docker is not running"}, t=T0 + 5)
    assert "failed" in N.describe(c.entries[1]) and "Docker" in N.describe(c.entries[1])


# ---- the transcript ----------------------------------------------------------- #
def test_a_built_chain_reads_as_a_construction_sequence():
    text = N.narrate(_submit(_built(), {"host-1": "M1", "switch-2": "S1"}).entries)
    assert "Placed a host (M1)." in text
    assert "Connected M1 to S1" in text
    assert "Started the lab." in text
    assert "WHAT THEY DID" in text and "WHAT THE CHAIN SHOWS" in text


def test_the_header_names_the_code_the_assignment_and_the_span():
    text = N.narrate(_built().entries)
    pretty = "-".join(TICKET[i:i + 4] for i in range(0, 12, 4))
    assert pretty in text and "lan-basics" in text
    assert "11m" in text                              # T0 → T0+700


def test_the_integrity_verdict_sits_above_the_story():
    proof = P.build_proof(_submit(_built(), {"host-1": "M1", "switch-2": "S1"}))
    text = N.narrate(P.entries_of(proof), P.verify_proof(proof))
    assert text.index("Integrity: PASS") < text.index("WHAT THEY DID")


def test_a_broken_proof_is_still_readable():
    """Told FAIL and shown nothing, an instructor has no way to judge what happened."""
    proof = P.build_proof(_submit(_built(), {"host-1": "M1", "switch-2": "S1"}))
    proof["entries"][3]["data"]["name"] = "M9"
    verdict = P.verify_proof(proof)
    text = N.narrate(P.entries_of(proof), verdict)
    assert "Integrity: FAIL" in text and "Placed a host" in text


def test_an_empty_chain_says_so_rather_than_rendering_nothing():
    assert "empty" in N.narrate([])


# ---- the thing a marker must not miss ------------------------------------------ #
def test_an_import_is_visible_as_an_import():
    text = N.narrate(_imported().entries)
    assert "IMPORTED a topology" in text and "lab3.gini" in text
    assert "not built here" in text


def test_an_import_shows_no_construction_sequence():
    text = N.narrate(_imported().entries)
    assert "Placed a" not in text
    assert "Construction — 0 placed, 0 connected" in text


def test_the_transcript_says_which_elements_were_not_built_here():
    text = N.narrate(_imported().entries)
    assert "WHERE THE SUBMITTED TOPOLOGY CAME FROM" in text
    assert "Built under this code: 0 of 2" in text
    assert "Arrived in an import: 2 (M1, S1)" in text


def test_unaccounted_elements_are_named():
    text = N.narrate(_submit(_built(), {"host-1": "M1", "router-7": "R9"}).entries)
    assert "Unaccounted for: 1 (R9)" in text


def test_work_done_before_arming_is_not_claimed_as_recorded():
    c = P.Chain.start(TICKET, assignment="lan-basics", t=T0)
    c.append("preexisting", {"devices": 2, "links": 1, "ids": ["host-1", "switch-2"],
                             "names": ["M1", "S1"]}, t=T0 + 1)
    text = N.narrate(_submit(c, {"host-1": "M1", "switch-2": "S1"}).entries)
    assert "not in this chain" in text
    assert "Already on the canvas before recording started: 2" in text


def test_a_chain_with_no_live_check_says_so_plainly():
    c = P.Chain.start(TICKET, t=T0)
    c.append("place", {"id": "host-1", "type": "host", "name": "M1"}, t=T0 + 10)
    text = N.narrate(_submit(c, {"host-1": "M1"}).entries)
    assert "Nothing in this chain was measured on a live lab." in text


# ---- the one-line summary -------------------------------------------------------- #
def test_the_headline_states_the_good_case_without_hedging():
    line = N.headline(_submit(_built(), {"host-1": "M1", "switch-2": "S1"}).entries)
    assert line == "Built here, action by action, and proved live: 1/1 checks passed."


def test_the_headline_leads_with_the_import():
    assert "imported file" in N.headline(_imported().entries)


def test_the_headline_notices_an_unsubmitted_chain():
    assert "never submitted" in N.headline(_built().entries)


def test_the_headline_does_not_credit_a_build_that_was_never_checked():
    c = P.Chain.start(TICKET, t=T0)
    c.append("place", {"id": "host-1", "type": "host", "name": "M1"}, t=T0 + 10)
    assert "nothing was ever checked" in N.headline(_submit(c, {"host-1": "M1"}).entries)


# ---- the counts a UI can reuse ---------------------------------------------------- #
def test_summarize_counts_by_tier():
    s = N.summarize(_built().entries)
    assert s["construction"] == 4 and s["operation"] == 2 and s["witnessed"] == 2
    assert s["witness_passed"] == 1 and s["witness_total"] == 1


def test_span_reads_in_human_units():
    assert N.fmt_span(0) == "under a minute"
    assert N.fmt_span(90) == "1m"
    assert N.fmt_span(3600) == "1h"
    assert N.fmt_span(4140) == "1h 9m"

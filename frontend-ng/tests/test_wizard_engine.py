"""The LLM-driven Wizard's pure brain: building prompts and parsing model replies."""
from gini.agent import wizard as wz


def test_catalog_lists_elements_with_descriptions():
    cat = wz.element_catalog()
    assert "Router:" in cat and "K8s Cluster:" in cat
    assert "K8s Node" not in cat                       # hidden elements are excluded


def test_coach_prompt_lists_detected_issues_and_forbids_invention():
    issues = [{"level": "warn", "device": "R1", "message": "R1 isn't connected to anything."},
              {"level": "warn", "device": "S1", "message": "S1 has no controller."}]
    p = wz.coach_prompt(issues, "R1 (router), S1 (ovs)")
    assert "R1 isn't connected to anything." in p and "S1 has no controller." in p
    assert "not invent" in p.lower()                   # detection is deterministic, not the model's job
    assert "prioriti" in p.lower()


def test_starter_prompt_has_no_biasing_concrete_example():
    # a concrete example element makes weak models copy it for every goal — the prompt must
    # use a neutral placeholder instead
    p = wz.starter_prompt("a multi-LAN network", wz.element_catalog())
    assert "<element name>" in p.lower()
    assert "k8s cluster -" not in p.lower() and "router -" not in p.lower()
    assert "multi-LAN network" in p


def test_parse_starter_extracts_element_and_reason():
    key, reason = wz.parse_starter("K8s Cluster - it's the foundation everything runs in.")
    assert key == "k8s_cluster" and "foundation" in reason


def test_parse_starter_prose_without_a_clean_pick_is_none():
    # strict: don't guess from rambling prose — re-ask instead
    key, _ = wz.parse_starter("You should begin with a Router to separate the LANs.")
    assert key is None


def test_parse_starter_none_when_no_element_named():
    key, reason = wz.parse_starter("Hmm, not sure.")
    assert key is None and reason == ""


def test_parse_starter_ambiguous_pick_is_rejected():
    # the model listing several names is not a clean pick — must NOT guess
    assert wz.parse_starter("PICK: Router or Switch")[0] is None


def test_parse_starter_accepts_exact_name_with_article():
    key, _ = wz.parse_starter("PICK: a Router - it separates the LANs")
    assert key == "router"


def test_element_names_and_retry_prompt():
    names = wz.element_names()
    assert "Router" in names and "K8s Cluster" in names
    p = wz.starter_retry_prompt("a multi-LAN network", names)
    assert "PICK:" in p and "multi-LAN network" in p


def test_parse_starter_prefers_a_leading_element_line():
    key, _ = wz.parse_starter("K8s Cluster - the runtime.\n(Also consider a VPC later.)")
    assert key == "k8s_cluster"


def test_parse_starter_pick_line_beats_earlier_mentions():
    # the exact failure: the model mentions OpenVSwitch/Switch while reasoning but PICKs Router
    text = ("An OpenVSwitch or a plain Switch could work, but for a multi-LAN network you "
            "want a router first.\nPICK: Router - it routes between the LANs.")
    key, reason = wz.parse_starter(text)
    assert key == "router" and "routes" in reason


def test_filter_prompt_lists_candidate_labels():
    cands = [("switch", "Switch"), ("host", "Machine")]
    p = wz.filter_prompt("a multi-LAN network", "Router", cands, "R1 (Router)")
    assert "multi-LAN" in p and "Switch" in p and "Machine" in p and "R1 (Router)" in p


def test_parse_filter_keeps_only_listed_candidates_with_reasons():
    cands = [("switch", "Switch"), ("host", "Machine"), ("database", "Managed Database")]
    text = "Switch - a LAN segment\nMachine - a host on that LAN"
    out = wz.parse_filter(text, cands)
    keys = [k for k, _ in out]
    assert keys == ["switch", "host"]                 # database not listed -> excluded
    assert dict(out)["switch"] == "a LAN segment"


def test_parse_filter_falls_back_to_scanning_when_unformatted():
    cands = [("switch", "Switch"), ("host", "Machine")]
    out = wz.parse_filter("I'd add a Switch and maybe a Machine.", cands)
    assert {k for k, _ in out} == {"switch", "host"}


def test_parse_filter_ignores_non_candidates():
    cands = [("switch", "Switch")]
    out = wz.parse_filter("Database - for state", cands)
    assert out == []                                  # nothing valid named

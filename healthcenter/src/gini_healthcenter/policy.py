"""The policy the Health Center serves, in the two shapes the doctor's two stages can read.

Stage 0 has no JSON parser — it is ``sh`` and PowerShell — so one document goes out twice:
``GET /doctor/policy.json`` for Stage 1 and a plain ``key=value`` ``GET /doctor/policy.txt`` for
Stage 0 (design decision 19). Both are rendered from the same dict here, so a floor raised for a
course cannot reach one stage and miss the other.

**An unconfigured Health Center changes nothing.** ``DEFAULTS`` is the doctor's own built-in
policy, so pointing a doctor at a fresh server alters no value it was already using — only the
recorded source, from ``builtin`` to ``live``, which is a fact about the run and not a change to
it. Precedence stays live → cached → built-in on the doctor's side, and the doctor never needs
this server to run.

Two traps, both of which come from how the two stages read rather than from what is written:

* **Stage 1 silently keeps only the fields it understands.** ``policy.validate`` merges over its
  built-in and drops the rest, so a knob added here and not there would be served, accepted and
  ignored. ``document`` therefore validates what it builds through the doctor's own function and
  refuses anything that does not survive the round trip — the failure lands on whoever adds the
  field, not on thirty machines quietly not honouring it.
* **Stage 0 reads two version components, Stage 1 reads all three.** Its ``sed`` and its
  PowerShell regex both capture ``major.minor``, so a floor of ``3.8.1`` would be enforced as
  ``3.8`` by the stage that actually chooses an interpreter while being recorded as ``3.8.1`` by
  the stage that only reports it. A patch-level floor is refused rather than half-applied.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence

from gini_doctor.stage1 import policy as _doctor_policy

#: What the doctor already has. Anything not set on the server is left exactly here.
DEFAULTS: Dict[str, Any] = dict(_doctor_policy.BUILTIN)

#: The version a server serves before anyone has configured it: same values, named honestly.
UNCONFIGURED = "hc-0"

#: The one key Stage 0 ACTS on. Both shells look for this exact name, so renaming it here without
#: renaming it there would leave every machine quietly on the built-in floor.
STAGE0_KEY = "min_python"

# Only these are rendered as text: the floor Stage 0 applies, and the version so that a person who
# curls this endpoint can tell which policy they are looking at. Emitting the rest would suggest a
# shell script honours it, and someone reading policy.txt would be entitled to believe that.
TEXT_KEYS = ("version", STAGE0_KEY)


def document(version: str = UNCONFIGURED, *, min_python: Optional[str] = None,
             groups_default: Optional[Sequence[str]] = None,
             probe_timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Build one policy document, or raise ``ValueError`` naming the field that would not survive.

    Every value is checked against the doctor's own validator here, at the point someone sets it,
    because the alternative is a server that accepts a policy, serves it, and has it dropped on
    arrival with nothing anywhere saying so.
    """
    doc: Dict[str, Any] = dict(DEFAULTS)
    doc["version"] = str(version)
    if min_python is not None:
        if len(str(min_python).split(".")) > 2:
            raise ValueError(
                "min_python=%r has a patch level. Stage 0 reads major.minor, so it would choose an "
                "interpreter against %s while every report claimed %s — set the floor to two "
                "components." % (min_python, ".".join(str(min_python).split(".")[:2]), min_python))
        doc["min_python"] = str(min_python)
    if groups_default is not None:
        doc["groups_default"] = list(groups_default)
    if probe_timeout_s is not None:
        doc["probe_timeout_s"] = probe_timeout_s

    kept = _doctor_policy.validate(doc)
    if kept != doc:
        bad = sorted(k for k in set(doc) | set(kept) if doc.get(k) != kept.get(k))
        raise ValueError(
            "the doctor would not honour %s as set here: it keeps %r. A field it drops is served, "
            "accepted and ignored, so it is refused at the server instead."
            % (", ".join(bad), {k: kept.get(k) for k in bad}))
    return doc


def as_json(doc: Dict[str, Any]) -> str:
    """``/doctor/policy.json`` — what Stage 1 fetches, caches, and records the version of."""
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def as_text(doc: Dict[str, Any]) -> str:
    """``/doctor/policy.txt`` — what Stage 0 greps for a floor.

    One ``key=value`` per line and no leading whitespace. Both stages take the FIRST match, so
    exactly one line may carry the floor; the comments are written so that no line but that one
    can be read as it.
    """
    lines = [
        "# gini-doctor policy, for the shell bootstrap. Stage 1 fetches /doctor/policy.json,",
        "# which carries the rest of the policy; only the two lines below are read here.",
    ]
    for key in TEXT_KEYS:
        if key in doc:
            lines.append("%s=%s" % (key, doc[key]))
    return "\n".join(lines) + "\n"

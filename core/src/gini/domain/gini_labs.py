"""Which GINI lab a course's lab is — read from the lab's id, and nothing else.

A code is minted for one Teaching Center lab, and that lab's questions travel with it, so a student
always gets the right questions. The CHECKS do not live there: A-Lab 04's stages are gBuilder's,
in its level file. So hand-in must know which GINI lab the code is for, or it grades whatever
happens to be armed on the student's machines — which put a student's A-Lab 04, as failures, into
their T-Lab 02 hand-in.

A course names its labs after GINI's ("a-lab-03", "t-lab-02"), and a second run of one with a
suffix ("a-lab-01a"). That id IS the link: `resolve("a-lab-01a")` is A-Lab 01. A lab whose id names
no GINI lab ("c-lab-01", "lab1") is for none, and hand-in grades no A-Lab into it.

History, because the obvious alternative was built and taken out: 6.22.16 added a "GINI lab" menu
to the console's lab editor, a second place to say the same thing. Every lab in the course was
already named for its GINI lab, the menu sat at "not set" on all of them, and where the two could
disagree the menu won silently. Removed 2026-11-21 (6.22.20). The old column is left in the
database, unread.

A-Lab 01's own id predates the naming ("syscall-sysinfo"), and armed state on students' machines is
keyed by it, so the course's "a-lab-01" reaches it through `ALIASES` rather than a rename.

In gini.domain because both halves need it: the Teaching Center may import only gini-core.
"""
from __future__ import annotations

A_LAB, T_LAB = "a-lab", "t-lab"

#: A course's name for a GINI lab whose own id is older than the "a-lab-NN" naming.
ALIASES = {"a-lab-01": "syscall-sysinfo"}


def choices() -> list[dict]:
    """Every GINI lab a course can point at: `[{id, title, kind}]`, A-Labs then T-Labs.

    A level file that will not load costs its own entry, never the list: this fills a menu in the
    console, and a broken T-Lab file must not stop a teacher choosing an A-Lab.
    """
    out: list[dict] = []
    try:
        from . import lab_spec
        out += [{"id": s.id, "title": s.title, "kind": A_LAB} for s in lab_spec.catalog()]
    except Exception:                                   # noqa: BLE001 — see the docstring
        pass
    try:
        from . import tlab_catalog
        out += [{"id": lab.id, "title": lab.title, "kind": T_LAB}
                for lab in tlab_catalog.catalog() if lab.released]
    except Exception:                                   # noqa: BLE001
        pass
    return out


def find(lab_id: str) -> dict | None:
    lab_id = str(lab_id or "").strip()
    return next((c for c in choices() if c["id"] == lab_id), None) if lab_id else None


def kind(lab_id: str) -> str:
    c = find(lab_id)
    return c["kind"] if c else ""


def title(lab_id: str) -> str:
    c = find(lab_id)
    return c["title"] if c else str(lab_id or "")


def resolve(course_lab: str) -> str:
    """The GINI lab a course lab is for, from its id: the id itself ("t-lab-02"), a course's name
    for one ("a-lab-01"), or either followed by a suffix that is not a digit ("a-lab-01a",
    "t-lab-02-late"). "" when it names none — a lab for no GINI lab, not an unknown one."""
    lab = str(course_lab or "").strip().lower()
    if not lab:
        return ""
    names = {c["id"]: c["id"] for c in choices()}
    names.update({k: v for k, v in ALIASES.items() if v in names})
    for name in sorted(names, key=len, reverse=True):
        if lab == name or (lab.startswith(name) and not lab[len(name)].isdigit()):
            return names[name]
    return ""

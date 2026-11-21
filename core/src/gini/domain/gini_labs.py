"""Which GINI lab a course's lab is — the link between a Teaching Center lab and gBuilder's.

A code is minted for one Teaching Center lab, and that lab's questions travel with it, so a student
always gets the right questions. The CHECKS do not live there: A-Lab 04's stages are gBuilder's,
in its level file. And the Teaching Center lab never said which gBuilder lab it was — its id is
whatever the teacher typed ("lab4") — so at hand-in gBuilder graded whatever happened to be
armed on the student's machines. A student with A-Lab 04 armed from earlier had it graded, as
failures, into a T-Lab 02 hand-in.

So a Teaching Center lab names its GINI lab (`activity.gini_lab`), the arm reply carries it like the
questions, and hand-in checks that lab and no other. A lab whose teacher has not set it, but whose
own id IS a GINI lab id ("a-lab-04"), is taken to mean that one. Neither: "" — and gBuilder falls
back to judging from the chain (`xv6_lab.grade_at_hand_in`).

In gini.domain because both halves need it: the Teaching Center may import only gini-core.
"""
from __future__ import annotations

A_LAB, T_LAB = "a-lab", "t-lab"


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


def resolve(gini_lab: str = "", course_lab: str = "") -> str:
    """The GINI lab a course lab is: the one its teacher chose, else its own id if that is a GINI
    lab id, else "" (not known — the caller falls back)."""
    for candidate in (gini_lab, course_lab):
        if find(candidate):
            return str(candidate).strip()
    return ""

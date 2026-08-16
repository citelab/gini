"""The OS-Coach concern enumerator (REASONING_2.0_DESIGN.md §5, second surface).

The coach streams a single Socratic nudge straight to chat, so phase C gives it the Twin's
FEED-FORWARD half: a deterministic concern set (watcher events, scheduling flags, a faulted
shadow) whose TOP concern is injected into the prompt as the thing the hint should target —
"did the hint target the most salient event?" answered by construction. The same concern set
is the upgraded no-model fallback ("things worth looking at"). The audit/revision half needs
the coach to become a structured turn (not a fire-and-forget stream) — deferred, by design.

Socratic withholding is ALWAYS legitimate here: the coach's job is one nudge, so no coverage
obligation is imposed on it."""
from __future__ import annotations

from .contracts import Concern
from .salience import cap


def coach_concerns(events, machine_state) -> list[Concern]:
    """Enumerate what matters for one coach nudge — all deterministic, all evidence-backed.

    `events` are the drained StateWatcher OsEvents (the teachable moments); `machine_state`
    contributes the currently-active scheduling flags and the shadow manifest (a student shadow
    that crashed back to the primary outranks everything — they think their code is running)."""
    concerns: list[Concern] = []

    for e in events or []:
        concerns.append(Concern(
            id=f"watcher:{e.kind}" + (f":{e.pid}" if e.pid is not None else ""),
            kind="watcher-event", statement=e.detail, evidence=e.detail,
            salience=2, source="watcher"))

    try:
        for name, s in (machine_state.shadows() or {}).items():
            if getattr(s, "faults", 0) and getattr(s, "is_student", False):
                concerns.append(Concern(
                    id=f"shadow:{name}:faulted", kind="watcher-event",
                    statement=(f"your {name} shadow crashed {s.faults}x and the shipped "
                               "primary is running instead"),
                    evidence=f"manifest: faults={s.faults} active={int(bool(s.active))}",
                    salience=3, source="shadow-manifest"))
    except Exception:
        pass

    try:
        flags = machine_state.scheduling_flags() or {}
        seen = {c.id for c in concerns}
        for kind, pids in flags.items():
            for pid in pids:
                cid = f"watcher:{kind}:{pid}"
                if cid not in seen:            # active-but-already-drained conditions still show
                    concerns.append(Concern(
                        id=cid, kind="watcher-event",
                        statement=f"pid {pid} is currently in {kind.replace('_', ' ')}",
                        evidence=f"scheduling flag {kind} active for pid {pid}",
                        salience=2, source="watcher"))
    except Exception:
        pass

    return cap(concerns)


def focus_line(concerns: list[Concern]) -> str:
    """The feed-forward injection for the coach prompt: the single most salient concern the
    nudge should target (deterministically chosen — the model words it, never picks it)."""
    if not concerns:
        return ""
    top = concerns[0]
    return (f"The most salient issue RIGHT NOW (target your nudge at this): "
            f"{top.statement} [{top.evidence}]")


def fallback_text(concerns: list[Concern]) -> str:
    """The no-model coach reply — the concern set rendered directly (better than the old raw
    event list: salience-ordered, evidence-backed, still zero-LLM)."""
    if not concerns:
        return ("The run looks steady. Try slowing the time-slice or spawning another "
                "CPU-bound process, and watch the switches.")
    lines = [f"- {c.statement}" for c in concerns[:3]]
    return "Worth looking at right now:\n" + "\n".join(lines)

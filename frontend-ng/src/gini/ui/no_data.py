"""What a panel says when it has nothing to show — one contract, four states.

**The rule: a panel with no data says so. It never paints a zero.** A zero is an assertion, and
"the page allocator has 0 free of 0 pages" is a claim about the running kernel that happens to be
both alarming and false. The Memory face shipped exactly that for months: the physical bar drew
`0 used / 0 free of 0 pages` beside a fragmentation gauge showing real memory, because one panel
read numbers the other had been told did not exist.

Four states, because "no data" has four different causes and they need four different sentences —
one is a kernel too old, one is a machine that is not answering, and confusing them sends somebody
to rebuild an image when their container is simply down:

    live          the kernel reported it            → draw it
    derived       GINI worked it out from what was reported → draw it, title says "(derived)"
    absent        this kernel build does not dump it → "… not reported by this kernel build"
    offline       the read failed entirely           → "no live data — the container is not answering"

`derived` is kept distinct from `live` on purpose. The difference between what the kernel measured
and what GINI inferred from it is the distinction this course spends a term teaching; collapsing
the two to save a word in a title would be a poor trade.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

LIVE = "live"
DERIVED = "derived"
ABSENT = "absent"
OFFLINE = "offline"


def panel_state(snap, key: str) -> str:
    """Which of the four states one panel of `snap` is in. `key` is its name in `have`."""
    if snap is None or not getattr(snap, "ok", True):
        return OFFLINE
    if key in (getattr(snap, "derived", ()) or ()):
        return DERIVED
    if key in (getattr(snap, "have", ()) or ()):
        return LIVE
    return ABSENT


def has_data(snap, key: str) -> bool:
    """True when the panel should draw its data rather than a placeholder."""
    return panel_state(snap, key) in (LIVE, DERIVED)


def placeholder_for(state: str, what: str) -> str:
    """The sentence for a panel that cannot draw. `what` names the subject, e.g. "page allocator".

    Empty for a state that HAS data — callers can pass any state through without branching.
    """
    if state == ABSENT:
        return f"{what} not reported by this kernel build — rebuild the xv6 image"
    if state == OFFLINE:
        return "no live data — the container is not answering"
    return ""


def title_for(title: str, state: str) -> str:
    """A panel title, marked "(derived)" when GINI worked the contents out rather than reading it."""
    return f"{title}  ·  (derived)" if state == DERIVED else title


def paint_placeholder(painter, rect, theme, text: str) -> None:
    """Centre one line of faint text in `rect`. The four custom widgets each spelled this
    differently, which is how two of them came to spell it as nothing at all."""
    if not text:
        return
    painter.setPen(QColor(theme.faint))
    painter.drawText(rect, Qt.AlignCenter, text)

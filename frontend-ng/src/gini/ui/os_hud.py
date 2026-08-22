"""OS HUD — the kernel as a map, with the story of one process underneath it.

TWO READINGS OF THE SAME TEN SECONDS, in one panel:

  BOARD    a permanent map of the kernel — twelve subsystem blocks, the three doors in, the
           device boundary, and the wide lane that never enters the kernel at all. Answers
           "what is the kernel made of, where is the CPU, what is expensive".
  X-RAY    the swimlanes below — individual events in exact kernel order, optionally focused on
           one pid. Answers "what happened, and in what order".

Neither substitutes for the other: the board can never say `exec` preceded the page fault, and
the X-ray can never say the block cache absorbed 589 of 601 requests. They share one window, one
history and one scrub timeline, which is precisely why they belong in one panel rather than two.

THE GEOMETRY IS THE ARGUMENT
----------------------------
An earlier version stacked user / kernel / hardware, which draws the lie students already
believe: that reaching the hardware means going THROUGH the kernel. It does not. If a process has
the CPU and its memory is mapped, it simply runs — ~10^8 instructions per second with no kernel
instruction executing. So the board has two lanes, and the kernel is off to one side, entered
only through three doors.

The dashed arrow is the deepest thing on it: the kernel writes satp and stvec, then steps aside.
It is not bypassed despite itself — it arranged to be bypassed. Drawn dashed because it is
CONFIGURATION, NOT A CALL.

Colour language, shared with the rest of the OS views:
    green  = user / the direct path        blue = kernel        amber = boundary (doors, drivers)

Encoding rule, and the reason the board is worth building: arrow width is CALLS, block shade is
CPU TIME. They disagree — bcache is asked ~50x more often than the disk and holds a fraction of
the time — and that disagreement is the difference between frequency and cost.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..domain.kernel_board import (
    BLOCK_FILES, DEVICE_BLOCKS, DOOR_HELP, DOORS, Frame, Window, parse, signature,
)
from ..domain.os_events import (
    LANES, EventWindow, episodes, fault_events, merge, syscall_events, trap_events,
)
from .glass import apply_glass, paint_glass_panel
from .hud import HudController, HudHistory, live_rect, paint_timeline, timeline_rect

LANE_ACCENT = {"syscall": "blue", "proc": "green", "memory": "purple",
               "fs": "cyan", "trap": "amber"}

# -- board layout ------------------------------------------------------------------------------
# HAND-AUTHORED AND PERMANENT. No auto-layout, ever: spatial memory is how a structure is learned,
# and a block that moves between frames cannot be learned at all. After three weeks a student
# should be able to point at where the block cache lives with the HUD closed.
#
# Rows of block names; () is a spacer. Widths are shared equally across each row.
BOARD_ROWS = (
    ("syscall",),
    ("file", "memory", "proc"),
    ("pipe", "inode", "log"),
    ("bcache",),
    ("disk", "console", "plic"),
)
# Short on purpose. A 3-up block has to hold a label AND its numbers; the full-width rows
# (syscall dispatch, block cache) can afford more.
BLOCK_LABEL = {"syscall": "syscall dispatch", "bcache": "block cache"}

# What the hardware below the board actually is, split by REACHABILITY — which is the whole
# argument of the two lanes, carried through to the bottom of the panel:
#
#   kernel only   MMIO regions are mapped in the kernel page table and nowhere else, so a user
#                 process cannot address the UART or the virtio queues at all. (Real systems do
#                 hand devices to user space — mapped rings, io_uring. A book footnote, not a
#                 board element.)
#   direct        the kernel has NO privileged path to memory. It uses the CPU and RAM exactly
#                 the way your program does, just with different page tables. So this half sits
#                 under BOTH lanes, and is drawn wider than the kernel-only half to say so.
MACHINE_KERNEL = ("kernel only", "disk · console · timer")
MACHINE_DIRECT = ("direct", "CPU · MMU + TLB · RAM")

PANEL_W = 640
PANEL_H = 560                        # board + swimlanes + scrub
PANEL_H_BOARD = 400                  # board only (swimlanes collapsed)
PAD = 16
ROW_H = 22
ROW_GAP = 8
GUTTER = 20                          # between the kernel column and the direct lane
LANE_W = 150                         # only as wide as its text: the kernel column needs the room
TRAIL_DOTS = 12                      # recent CPU samples drawn; the newest is the marker


class OsHud(QWidget):
    """Pure rendering over a board Frame + an event list. No I/O here."""

    def __init__(self, parent, theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self._frame: Frame = Frame()
        self._events: list = []
        self._hart_sub: str = "user"      # where the CPU marker sits
        self._lanes = {"board": True, "xray": True}
        self._history: HudHistory | None = None
        self._scrub_t: float | None = None
        self._scrub_drag = False
        self._focus_pid: int | None = None
        self.window_s: float = 10.0
        self.stale: bool = False          # kernel has no board support (old image)
        self.paint_error: str = ""        # last paint failure, shown in the panel (see paintEvent)
        self._focus: str = ""             # selected block/door name, "" = none
        self._hit: dict = {}              # name -> clickable rect, rebuilt every paint
        self._xray_head = QRectF()        # the X-RAY caption, clicked to collapse the lanes
        self.resize(PANEL_W, PANEL_H)
        self.setMouseTracking(True)
        apply_glass(self)

    # -- data -------------------------------------------------------------- #
    def set_frame(self, frame, events, hart_sub="user") -> None:
        self._frame = frame or Frame()
        self._events = list(events or [])
        self._hart_sub = hart_sub or "user"
        self.update()

    def set_history(self, hist: HudHistory) -> None:
        self._history = hist
        self.update()

    def set_lane(self, name: str, on: bool) -> None:
        self._lanes[name] = bool(on)
        self._resize_to_lanes()
        self.update()

    def set_focus_pid(self, pid) -> None:
        self._focus_pid = pid
        self.update()

    # Which swimlanes a board selection reveals. A block or a door is not a pid, so selecting one
    # filters by SUBSYSTEM rather than by process — "show me what the block cache produced" is the
    # question the board actually poses.
    FOCUS_LANES = {
        "asked": ("syscall", "proc", "fs"), "couldn't": ("memory",), "seized": ("trap",),
        "syscall": ("syscall",), "proc": ("proc",), "memory": ("memory",),
        "file": ("fs",), "inode": ("fs",), "log": ("fs",), "bcache": ("fs",),
        "pipe": ("fs",), "disk": ("fs", "trap"), "console": ("trap",), "plic": ("trap",),
    }

    def set_focus_lanes(self, name) -> None:
        """Select a block or door on the board; the swimlanes below narrow to its lanes."""
        self._focus = name or ""
        self.update()

    def _block_help(self, name: str) -> str:
        files = ", ".join(BLOCK_FILES.get(name, ()))
        n = self._frame.blocks.get(name, 0)
        ours = self._frame.ours(name)
        bits = [f"{n} calls in this window"]
        if ours:
            bits.append(f"{ours} of them provoked by GINI reading the machine")
        if files:
            bits.append(files)
        return f"{name} — " + "; ".join(bits)

    def _resize_to_lanes(self) -> None:
        self.resize(PANEL_W, PANEL_H if self._lanes.get("xray") else PANEL_H_BOARD)

    @property
    def scrubbing(self) -> bool:
        return self._scrub_t is not None

    def go_live(self) -> None:
        self._scrub_t = None
        if self._history is not None:
            latest = self._history.latest()
            if latest:
                self.set_frame(*latest)
        self.update()

    # -- paint ------------------------------------------------------------- #
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        f = self._frame
        ipe = f.instr_per_entry
        head = f"OS  ·  KERNEL BOARD  ·  last {int(self.window_s)} s"
        paint_glass_panel(p, self.rect(), self.theme, head)
        if ipe:
            p.setFont(QFont(self.font().family(), 8))
            p.setPen(QColor(t.accent_for("orange")))
            p.drawText(self.width() - 320 - PAD, 8, 320, 18, Qt.AlignRight | Qt.AlignVCenter,
                       f"{ipe:,.0f} instructions per kernel entry")

        if self.stale:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "this kernel has no board support.\n"
                       "Rebuild the xv6 image (Load) to enable it.")
            self._paint_scrub(p)
            return

        # The scrub timeline is drawn in a `finally` and the board/lane painting is guarded.
        #
        # Qt abandons the rest of paintEvent when it raises, so a single bad frame used to take
        # the timeline down with it — and a missing timeline looks like "the recorder is broken"
        # rather than "one block failed to draw". The controls must outlive the content. The
        # error is shown once, in the panel, instead of silently every 900 ms.
        y = 34
        try:
            if self._lanes.get("board"):
                y = self._paint_board(p, y)
            if self._lanes.get("xray"):
                self._paint_xray(p, y + 10)
        except Exception as e:                      # noqa: BLE001 - a HUD must never take the app down
            self.paint_error = f"{type(e).__name__}: {e}"
            p.setPen(QColor(t.danger))
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(PAD, self.height() - 74, self.width() - 2 * PAD, 28,
                       Qt.AlignLeft | Qt.TextWordWrap, f"board paint failed — {self.paint_error}")
        finally:
            self._paint_scrub(p)

    # -- the board --------------------------------------------------------- #
    def _kernel_col(self) -> tuple:
        left = PAD
        width = self.width() - 2 * PAD - GUTTER - LANE_W
        return left, width

    def _paint_board(self, p: QPainter, y: int) -> int:
        t = self.theme.theme
        f = self._frame
        blue = QColor(t.accent_for("blue"))
        green = QColor(t.accent_for("green"))
        amber = QColor(t.accent_for("amber"))
        kleft, kwidth = self._kernel_col()
        full = self.width() - 2 * PAD

        small = QFont(self.font().family(), 8)
        p.setFont(small)

        # -- your program: the top strip, and the widest number on the board --------------
        strip = QRectF(PAD, y, full, ROW_H)
        self._chip(p, strip, green, 0.14, "your program",
                   f"{f.user_kinstr * 1000 / max(f.span_s, 0.001):,.0f} instr/s"
                   if f.span_s else "")
        y += ROW_H + 10

        # -- the three doors, by AGENCY --------------------------------------------------
        # Not one gate: a fault is not an interrupt, and this is the same three-way split
        # usertrap() itself makes.
        self._hit = {}
        dw = (kwidth - 2 * ROW_GAP) / 3.0
        for i, name in enumerate(DOORS):
            r = QRectF(kleft + i * (dw + ROW_GAP), y, dw, ROW_H)
            self._hit[name] = r
            self._chip(p, r, amber, 0.18, name, str(f.doors[i] if i < len(f.doors) else 0),
                       centre=True, selected=self._focus == name)
        doors_y = y
        # The doors band IS the trap code, so a CPU marker reading "trap" belongs here rather than
        # nowhere. Registered as a target below alongside the blocks.
        doors_rect = QRectF(kleft, y, kwidth, ROW_H)
        y += ROW_H + 10

        # -- the kernel container --------------------------------------------------------
        ktop = y
        rows_h = len(BOARD_ROWS) * ROW_H + (len(BOARD_ROWS) - 1) * ROW_GAP + 12 + 14
        kbox = QRectF(kleft, ktop, kwidth, rows_h)
        p.setPen(QPen(blue, 1))
        p.setBrush(QColor(blue.red(), blue.green(), blue.blue(), 14))
        p.drawRoundedRect(kbox, 10, 10)

        # LAYOUT FIRST, PAINT SECOND. The edges have to be drawn UNDER the blocks: a call from
        # syscall dispatch down to the block cache passes over two rows on the way, and drawn on
        # top it scribbles through their labels (which is what turned the first build into
        # spaghetti). Blocks paint opaque afterwards, so lines vanish behind them and re-emerge.
        by = ktop + 6
        centres, boundary_y = {}, 0.0
        for row in BOARD_ROWS:
            n = len(row)
            avail = kwidth - 16
            bw = (avail - (n - 1) * ROW_GAP) / n
            x0 = kleft + 8
            # The block cache is inset so it reads as a shared floor under the layers above it
            # rather than as another full-width band. `syscall dispatch` stays full width: the
            # three door arrows land on it, and they span the whole column.
            if row == ("bcache",):
                x0 += avail * 0.08
                bw = avail * 0.84
            for i, name in enumerate(row):
                centres[name] = QRectF(x0 + i * (bw + ROW_GAP), by, bw, ROW_H)
            by += ROW_H + ROW_GAP
            if row == ("bcache",):
                boundary_y = by
                by += 14

        self._paint_edges(p, centres, blue)

        for name, r in centres.items():
            self._hit[name] = r
            # SHADE IS TIME, not calls — and only when enough samples back it (see
            # Frame.resid_trustworthy). Scaled against the busiest kernel block so a quiet machine
            # still shows contrast, capped so nothing saturates to unreadable.
            share = f.share(name) if f.resid_trustworthy else 0.0
            self._chip(p, r, amber if name in DEVICE_BLOCKS else blue,
                       min(0.42, 0.04 + share * 2.4),
                       BLOCK_LABEL.get(name, name), self._block_note(name),
                       outline=amber if name in DEVICE_BLOCKS else None, opaque=True,
                       selected=self._focus == name)
        p.setPen(QColor(t.accent_for("amber")))
        p.setFont(QFont(self.font().family(), 7))
        p.drawText(int(kleft + 8), int(boundary_y), 200, 12, Qt.AlignLeft, "device boundary")
        p.setFont(small)

        # -- doors feed the kernel ------------------------------------------------------
        p.setPen(QPen(amber, 1))
        for i in range(3):
            x = int(kleft + i * (dw + ROW_GAP) + dw / 2)
            p.drawLine(x, int(doors_y + ROW_H), x, int(ktop))

        # -- the direct lane: permanent, and the widest path on the board ---------------
        lane_x = self.width() - PAD - LANE_W
        lane = QRectF(lane_x, doors_y, LANE_W, kbox.bottom() - doors_y)
        p.setPen(QPen(green, 1))
        p.setBrush(QColor(green.red(), green.green(), green.blue(), 12))
        p.drawRoundedRect(lane, 10, 10)

        ax = lane_x + 15
        tip = lane.bottom() - 6
        arrow = QPolygonF([
            QPointF(ax - 6, lane.top() + 4), QPointF(ax + 6, lane.top() + 4),
            QPointF(ax + 6, tip - 16), QPointF(ax + 12, tip - 16),
            QPointF(ax, tip), QPointF(ax - 12, tip - 16), QPointF(ax - 6, tip - 16)])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(green.red(), green.green(), green.blue(), 120))
        p.drawPolygon(arrow)

        tx = lane_x + 32
        tw = LANE_W - 40
        ty = lane.top() + 10
        p.setPen(QColor(t.text))
        p.drawText(int(tx), int(ty), int(tw), 14, Qt.AlignLeft, "direct path")
        p.setPen(QColor(t.muted))
        p.setFont(QFont(self.font().family(), 7))
        # Kept to ~15 characters a line: the lane is now only as wide as it needs to be, and the
        # space it gave back went to the kernel blocks, which were clipping their numbers.
        for line in ("no kernel runs", "the MMU checks", "every address",
                     "in hardware", "", "rules installed", "by the kernel"):
            ty += 13
            if line:
                p.drawText(int(tx), int(ty), int(tw), 12, Qt.AlignLeft, line)
        p.setFont(small)

        # -- configuration, NOT a call ---------------------------------------------------
        gx = int(kleft + kwidth + GUTTER / 2)
        pen = QPen(blue, 1)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawLine(gx, int(ktop), gx, int(kbox.bottom() + 22))

        # -- the machine, split by REACHABILITY -------------------------------------------
        # One box would repeat the stacked-geometry lie at the bottom of the panel: it would say
        # the hardware is one undifferentiated thing sitting under the kernel. It is not.
        #
        # The two halves are deliberately UNEQUAL and deliberately NOT aligned with the columns
        # above. `kernel only` is narrow and sits under the driver row, because the drivers are
        # the only way to reach it. `direct` is wide and starts back under the kernel column,
        # because the kernel reaches memory the same way a user process does — it has no
        # privileged path, only different page tables. That asymmetry is the point.
        y = kbox.bottom() + 30
        kw = full * 0.44
        dwid = full - kw - 12
        self._chip(p, QRectF(PAD, y, kw, ROW_H), amber, 0.10,
                   MACHINE_KERNEL[0], MACHINE_KERNEL[1], outline=amber)
        self._chip(p, QRectF(PAD + kw + 12, y, dwid, ROW_H), green, 0.10,
                   MACHINE_DIRECT[0], MACHINE_DIRECT[1], outline=green)
        y += ROW_H + 6

        # -- the CPU marker: a TRAIL of real samples, newest solid ------------------------
        # Every dot is somewhere the hart actually was, recorded by the kernel on a timer tick.
        # The alternative — animating a position from the residency distribution — would look
        # livelier and be a fabrication. Most of the trail sits in the direct lane, which is the
        # lesson: the CPU is usually not in the kernel at all.
        centres["trap"] = doors_rect          # "in trap code" == in the doorway
        orange = QColor(t.accent_for("orange"))

        def spot_for(sub, k):
            if sub == "user":
                return QPointF(ax, lane.top() + lane.height() * (0.30 + 0.06 * (k % 6)))
            r = centres.get(sub)
            return QPointF(r.right() - 12, r.center().y()) if r else None

        recent = list(f.trail)[-TRAIL_DOTS:]
        for k, sub in enumerate(recent):
            pt = spot_for(sub, k)
            if pt is None:
                continue
            newest = k == len(recent) - 1
            a = 210 if newest else int(30 + 90 * (k / max(1, len(recent) - 1)))
            p.setPen(QPen(orange, 2) if newest else Qt.NoPen)
            p.setBrush(QColor(orange.red(), orange.green(), orange.blue(), a))
            p.drawEllipse(pt, 6 if newest else 3, 6 if newest else 3)
            if newest:
                p.setPen(QColor(orange))
                p.setFont(QFont(self.font().family(), 7))
                p.drawText(int(pt.x()) + 12, int(pt.y()) - 6, 140, 12, Qt.AlignLeft,
                           "hart 0, last sample")
                p.setFont(small)

        p.setPen(QColor(t.faint))
        p.setFont(QFont(self.font().family(), 7))
        legend = ("arrow = calls   ·   shade = CPU time   ·   grey dashed = GINI observing   ·   "
                  "blue dashed = configuration")
        if not f.resid_trustworthy:
            legend = (f"sampling — only {f.total_resid} residency samples this window; "
                      "widen it for meaningful shading")
        p.drawText(PAD, int(y), full, 12, Qt.AlignLeft, legend)
        p.setFont(small)
        return int(y) + 14

    @staticmethod
    def _num(n: int) -> str:
        """Compact counts. A 3-up block has ~121px for a label and its numbers, so "1.3k" earns
        its place over "1,284" — the exact figure is a click away, the magnitude is the point."""
        if n >= 10_000:
            return f"{n / 1000:.0f}k"
        if n >= 1_000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _block_note(self, name: str) -> str:
        """Calls and time, side by side and never merged.

        Both are printed because shade alone is colour-as-only-cue, and because the two numbers
        DISAGREEING is the whole point of the board: bcache reads "601 · 2%" right above disk's
        "12 · 12%".

        A trailing "+N" is what GINI's own polling contributed — the console and plic always carry
        one. The percentage is dropped entirely when too few residency samples back it, rather
        than printing a confident number derived from two observations.
        """
        f = self._frame
        n = f.blocks.get(name, 0)
        ours = f.ours(name)
        pct = f.share(name) * 100
        if not n and not ours and pct < 0.5:
            return "—"
        s = self._num(n)
        if ours:
            s += f"+{self._num(ours)}"
        if pct >= 0.5 and f.resid_trustworthy:
            s += f"  {pct:.0f}%"
        return s

    def _chip(self, p: QPainter, r: QRectF, col: QColor, alpha: float,
              label: str, note: str = "", centre: bool = False, outline=None,
              opaque: bool = False, selected: bool = False) -> None:
        t = self.theme.theme
        if selected:
            sel = QColor(t.accent_for("orange"))
            p.setPen(QPen(sel, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 7, 7)
        if opaque:
            # Blocks are painted over the edge lines, so they need a solid ground first. Without
            # it the tint is translucent and every line that passes behind a block shows through
            # its label.
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.panel))
            p.drawRoundedRect(r, 5, 5)
        p.setPen(QPen(outline if outline is not None else col,
                      1.6 if outline is not None else 0.8))
        p.setBrush(QColor(col.red(), col.green(), col.blue(), int(255 * alpha)))
        p.drawRoundedRect(r, 5, 5)
        p.setPen(QColor(t.text))
        if centre:
            p.drawText(r, Qt.AlignCenter, f"{label}  ·  {note}" if note else label)
            return
        p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
        if note:
            p.setPen(QColor(t.muted))
            p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignRight, note)

    @staticmethod
    def _exit_point(r: QRectF, toward) -> QPointF:
        """Where the line from `r`'s centre toward `toward` crosses `r`'s edge.

        Every edge runs centre-to-centre and is clipped here at both ends. The first version
        always drew bottom-to-top, which meant an upward call (console → proc) left the bottom of
        its source and entered the top of a box ABOVE it — producing crossings that looked like
        they encoded something and encoded nothing at all.
        """
        c = r.center()
        dx, dy = toward.x() - c.x(), toward.y() - c.y()
        if not dx and not dy:
            return c
        sx = (r.width() / 2) / abs(dx) if dx else float("inf")
        sy = (r.height() / 2) / abs(dy) if dy else float("inf")
        s = min(sx, sy)
        return QPointF(c.x() + dx * s, c.y() + dy * s)

    def _paint_edges(self, p: QPainter, centres: dict, blue: QColor) -> None:
        """Arrow width encodes CALLS. Never time — that is the block shade's job, and collapsing
        the two would erase the frequency/cost distinction the board exists to show.

        Two sets of edges, drawn differently on purpose:

          solid blue     what the workload did
          dashed grey    what GINI's own polling provoked — real kernel work, caused by the act of
                         measuring. On an idle machine most of the console and plic traffic is
                         this. Hiding it would flatter the board; showing it teaches that
                         measurement perturbs the thing measured.
        """
        f = self._frame
        grey = QColor(self.theme.theme.muted)
        top = max(list(f.edges.values()) + list(f.edges_obs.values()) or [1]) or 1

        def draw(edges, col, dashed):
            for (src, dst), n in edges.items():
                a, b = centres.get(src), centres.get(dst)
                if a is None or b is None or a is b:
                    continue
                p0 = self._exit_point(a, b.center())
                p1 = self._exit_point(b, a.center())
                w = 0.8 + 3.2 * (n / top) ** 0.5
                pen = QPen(col, w)
                if dashed:
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawLine(p0, p1)
                self._arrowhead(p, p0, p1, col, w)

        draw(f.edges_obs, grey, True)      # ours first, so the workload's edges sit on top
        draw(f.edges, blue, False)

    @staticmethod
    def _arrowhead(p: QPainter, p0: QPointF, p1: QPointF, col: QColor, w: float) -> None:
        """A head at the CALLEE end. Centre-to-centre lines carry no inherent direction, and the
        legend promises arrows — so draw them."""
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        d = (dx * dx + dy * dy) ** 0.5
        if d < 12:
            return
        ux, uy = dx / d, dy / d
        size = 4 + w
        bx, by = p1.x() - ux * size, p1.y() - uy * size
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([
            QPointF(p1.x(), p1.y()),
            QPointF(bx - uy * size * 0.45, by + ux * size * 0.45),
            QPointF(bx + uy * size * 0.45, by - ux * size * 0.45)]))

    # -- the swimlanes ----------------------------------------------------- #
    def _paint_xray(self, p: QPainter, y: int) -> None:
        t = self.theme.theme
        evs_all = self._events
        if self._focus_pid is not None:
            evs_all = [e for e in evs_all if e.pid == self._focus_pid]
        # A board selection narrows the lanes to the ones it can produce. Lanes outside the
        # selection stay drawn but empty, so you can see WHAT WAS EXCLUDED rather than having the
        # rails silently disappear.
        show = self.FOCUS_LANES.get(self._focus) if self._focus else None
        head = "X-RAY" + (f"  ·  pid {self._focus_pid}" if self._focus_pid is not None
                          else "  ·  all processes")
        if self._focus:
            head += f"  ·  from {self._focus}"
        p.setFont(QFont(self.font().family(), 7))
        p.setPen(QColor(t.accent if self._focus else t.faint))
        self._xray_head = QRectF(PAD, y, 400, 12)
        p.drawText(self._xray_head, Qt.AlignLeft | Qt.AlignVCenter,
                   f"{head}  ·  {len(evs_all)} events  ·  click to collapse")
        y += 14

        left, width = PAD + 56, self.width() - PAD - 70
        # The rails are drawn even with nothing on them. An empty window and a missing feature
        # look identical if the lanes disappear, and the student needs to know the lanes are
        # there and that scrubbing back may fill them.
        lo = evs_all[0].seq if evs_all else 0
        span = ((evs_all[-1].seq - lo) or 1) if evs_all else 1

        for lane in LANES:
            evs = [e for e in evs_all if e.lane == lane]
            if show is not None and lane not in show:
                evs = []                                  # excluded by the board selection
            p.setFont(QFont(self.font().family(), 8))
            p.setPen(QColor(t.text if evs else t.faint))
            p.drawText(PAD - 10, y, 60, ROW_H, Qt.AlignVCenter | Qt.AlignRight, lane)
            p.setPen(QPen(QColor(t.line), 1))
            p.drawLine(int(left), int(y + ROW_H / 2), int(left + width), int(y + ROW_H / 2))
            col = QColor(t.accent_for(LANE_ACCENT.get(lane, "slate")))
            # Dots read as a story only while they are countable; past that they merge into a
            # solid bar that says "lots" and nothing else.
            dense = len(evs) > 24
            for e in evs:
                x = left + (e.seq - lo) / span * width
                if dense:
                    p.setPen(QPen(col, 1))
                    p.drawLine(int(x), int(y + ROW_H / 2 - 5), int(x), int(y + ROW_H / 2 + 5))
                else:
                    p.setBrush(col)
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(QRectF(x - 4, y + ROW_H / 2 - 4, 8, 8))
            p.setFont(QFont(self.font().family(), 7))
            p.setPen(QColor(t.muted))
            if dense:
                p.drawText(int(left + width) - 54, int(y), 54, ROW_H,
                           Qt.AlignVCenter | Qt.AlignRight, f"{len(evs)}")
            elif evs and len(evs) <= 6:
                for e in evs:
                    x = left + (e.seq - lo) / span * width
                    p.drawText(int(x) - 26, int(y), 52, 11, Qt.AlignCenter, e.kind[:10])
            y += ROW_H

        if not evs_all:
            # The rails above are drawn empty on purpose; this says why, and points at the one
            # control that can fill them.
            p.setFont(QFont(self.font().family(), 7))
            p.setPen(QColor(t.faint))
            p.drawText(PAD + 56, y + 4, self.width() - 2 * PAD - 56, 12, Qt.AlignLeft,
                       f"no events in the last {int(self.window_s)} s — "
                       "launch a program, or scrub back.")

    # -- scrub ------------------------------------------------------------- #
    def _paint_scrub(self, p: QPainter) -> None:
        if self._history is not None and len(self._history):
            paint_timeline(p, self.theme, self._history, self.width(), self.height(),
                           self._scrub_t)

    # -- interaction --------------------------------------------------------- #
    def mousePressEvent(self, e) -> None:  # noqa: N802
        pos = e.position() if hasattr(e, "position") else e.pos()

        # Click the X-RAY header to collapse the swimlanes; the panel shrinks to the board.
        if self._xray_head.contains(pos):
            self.set_lane("xray", not self._lanes.get("xray"))
            return

        # Click a block or a door to focus the swimlanes on what it produces; click it again, or
        # anywhere else on the board, to clear. This is what makes the two halves one view rather
        # than two stacked ones — the map selects, the story below answers.
        for name, r in self._hit.items():
            if r.contains(pos):
                self.set_focus_lanes(None if self._focus == name else name)
                return

        h = self._history
        if h is None or not len(h):
            return
        if live_rect(self.width(), self.height()).contains(pos):
            self.go_live()
            return
        if timeline_rect(self.width(), self.height()).contains(pos):
            self._scrub_drag = True
            self._scrub_to(pos.x())
        elif self._focus:
            self.set_focus_lanes(None)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._scrub_drag:
            pos = e.position() if hasattr(e, "position") else e.pos()
            self._scrub_to(pos.x())
            return
        # Hover help. The doors are the part students most often misread — a fault is not an
        # interrupt — so each one says what it means rather than relying on its one-word label.
        pos = e.position() if hasattr(e, "position") else e.pos()
        tip = ""
        for name, r in self._hit.items():
            if r.contains(pos):
                tip = DOOR_HELP.get(name) or self._block_help(name)
                break
        if tip != self.toolTip():
            self.setToolTip(tip)

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._scrub_drag = False

    def _scrub_to(self, x: float) -> None:
        h = self._history
        tl = timeline_rect(self.width(), self.height())
        span = (h.t_end - h.t_start) or 1.0
        frac = min(1.0, max(0.0, (x - tl.left()) / (tl.width() or 1.0)))
        t = h.t_start + frac * span
        if t >= h.t_end - 0.5:
            self.go_live()
            return
        self._scrub_t = t
        frame = h.at(t)
        if frame:
            self.set_frame(*frame)


class OsHudController(HudController):
    """Polls the xv6 agent for the board and the event rings, records each frame for replay."""

    frame_ready = Signal(object, object, object)      # (Frame, events, hart_sub)

    # Deliberately much shorter than the HUD default: a snapshot lands almost every poll on a busy
    # kernel, and ten minutes of them is an unreadable wall of ticks you cannot aim at.
    SCRUBBACK_S = 120.0

    def __init__(self, parent, theme, agent_of, window_getter=None,
                 interval_ms: int = 900) -> None:
        super().__init__(parent, interval_ms=interval_ms, retain_s=self.SCRUBBACK_S)
        self.hud = OsHud(parent, theme)
        self.hud.set_history(self.history)
        self._agent_of = agent_of
        self._window_getter = window_getter or (lambda: 10)
        self.board = Window()
        self.window = EventWindow(window_s=float(self._window_getter() or 10))
        self.frame_ready.connect(self._on_frame)

    def read(self):
        """Blocking reads on a worker thread: the board plus the three event rings."""
        agent = self._agent_of()
        if agent is None:
            return None
        board = parse(agent.get_text("/board"))
        sc = agent.get_text("/sc")
        flt = agent.get_text("/faults")
        tr = agent.get_text("/traps")
        events = merge(syscall_events(sc), fault_events(flt), trap_events(tr))
        return board, events

    def deliver(self, payload) -> None:
        sample, events = payload
        frame = self.board.add(sample, time.monotonic())
        self.frame_ready.emit(frame, events, self._hart_sub(frame))

    def _hart_sub(self, frame) -> str:
        """Kept for the history payload's shape; the marker now draws from frame.trail, which is
        a ring of REAL samples rather than a window aggregate."""
        return frame.here or "user"

    def _on_frame(self, frame, events, hart_sub) -> None:
        now = time.monotonic()
        try:
            self.window.set_window(self._window_getter() or 10)
        except Exception:
            pass
        recent = self.window.add(events, now)
        self.hud.window_s = self.window.window_s
        self.hud.stale = not self.board.board_supported
        self.history.push((frame, recent, hart_sub), signature(frame), now)
        if not self.hud.scrubbing:
            self.hud.set_frame(frame, recent, hart_sub)
        self.hud.set_history(self.history)

    def latest_episodes(self) -> list:
        frame = self.history.latest()
        return episodes(frame[1]) if frame else []

    def show_topright(self) -> None:
        par = self.hud.parentWidget()
        if par is not None:
            self.hud.move(max(0, par.width() - self.hud.width() - 16), 16)
        self.hud.show()
        self.hud.raise_()
        self.start()

    def close(self) -> None:
        self.stop()
        self.hud.hide()

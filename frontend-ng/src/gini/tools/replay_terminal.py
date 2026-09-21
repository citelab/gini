"""Replay a GINI_TTYD_CAPTURE file through the terminal emulator, offline.

    GINI_TTYD_CAPTURE=/tmp/term.log gbuilder      # reproduce, then quit
    python -m gini.tools.replay_terminal /tmp/term.log

It answers one question and is built for nothing else: **a line that is missing from the pane —
did it never arrive, or did it arrive and get overwritten?**

Those have different causes. Never arrived points at ttyd, the PTY or the container; arrived and
lost points at this emulator. No amount of reading either half distinguishes them, and guessing
wrong costs a day.

So this feeds the captured bytes through the SAME `TerminalView` the app uses, in the same
order, and prints three things:

* what the wire contained — every line the container actually sent;
* what the emulator ends up holding — screen plus scrollback;
* the difference, which is the answer.

A line in the first list and not the second was received and lost HERE. A line the student saw
missing that is in neither was never sent. `--chunk` replays the stream in fixed-size pieces
instead of the captured frame boundaries, which is how to check whether a split at an awkward
place (a UTF-8 sequence, an escape sequence) is what breaks it.
"""
from __future__ import annotations

import argparse
import os
import re
import sys


def parse(path: str) -> list[tuple[str, bytes]]:
    """The capture file -> [(direction, payload)], in the order it happened."""
    raw = open(path, "rb").read()
    out: list[tuple[str, bytes]] = []
    i = 0
    head = re.compile(rb"([<>]) (\d+)\n")
    while i < len(raw):
        m = head.match(raw, i)
        if not m:
            break
        n = int(m.group(2))
        start = m.end()
        out.append((m.group(1).decode(), raw[start:start + n]))
        i = start + n + 1                     # +1 for the trailing newline
    return out


def wire_lines(frames) -> list[str]:
    """Every line the container sent, escapes stripped — what SHOULD be visible."""
    from ..services.console_tap import clean
    body = clean(b"".join(p for d, p in frames if d == "<"))
    return [ln.rstrip() for ln in body.split("\n") if ln.strip()]


def replayed_lines(frames, chunk: int = 0) -> list[str]:
    """Everything the emulator can still show afterwards: scrollback, then the live screen."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ..ui.terminal_view import TerminalView
    QApplication.instance() or QApplication([])

    class _T:
        class theme:
            panel2 = bg = "#1e222a"
            text = "#dcdfe4"
            line = "#232b36"
            muted = "#8b93a1"
            accent = "#4c8dff"

    v = TerminalView(_T())
    if chunk:
        blob = b"".join(p for d, p in frames if d == "<")
        for i in range(0, len(blob), chunk):
            v.feed(blob[i:i + chunk])
    else:
        for d, payload in frames:
            if d == "<":
                v.feed(payload)
    scr = v._screen
    rows = ["".join(c.data for c in line.values()) for line in scr.history.top]
    rows += [v._row_text(scr.buffer[y]) for y in range(scr.lines)]
    return [r.rstrip() for r in rows if r.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture")
    ap.add_argument("--chunk", type=int, default=0,
                    help="ignore frame boundaries and feed fixed-size pieces instead")
    ap.add_argument("--show", action="store_true", help="print both lists in full")
    a = ap.parse_args(argv)

    frames = parse(a.capture)
    sent = sum(1 for d, _ in frames if d == "<")
    wire = wire_lines(frames)
    got = replayed_lines(frames, a.chunk)

    print(f"{len(frames)} frames ({sent} from the container), "
          f"{sum(len(p) for d, p in frames if d == '<')} bytes")
    print(f"lines on the wire: {len(wire)}    lines the emulator kept: {len(got)}")

    held = list(got)
    lost = []
    for ln in wire:
        if ln in held:
            held.remove(ln)
        else:
            lost.append(ln)
    print()
    if lost:
        print(f"ARRIVED AND LOST — {len(lost)} line(s) the container sent that the emulator "
              f"no longer holds:")
        for ln in lost[:40]:
            print("   ", ln)
        if len(lost) > 40:
            print(f"    … {len(lost) - 40} more")
        print("\nThat is a bug on THIS side: the bytes were received.")
    else:
        print("Nothing the container sent was lost by the emulator.")
        print("A line the student saw missing is therefore one ttyd never sent — look at the")
        print("container, the PTY, or ttyd itself, not at the terminal widget.")
    if a.show:
        print("\n--- wire ---")
        for ln in wire:
            print("   ", ln)
        print("\n--- emulator ---")
        for ln in got:
            print("   ", ln)
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())

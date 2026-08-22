"""Reading the kernel's own source — what the GINI Source tab browses.

Nobody reads ten thousand lines of kernel cold. But a student will read the thirty lines behind a
block they just watched go dark on the board, which turns source reading from an assignment into
a consequence. That is the entire argument for this module.

The source comes from INSIDE the container, so it is the PATCHED tree the running kernel was
compiled from. A student who opens `bio.c` sees

    bread(uint dev, uint blockno)
    {
      GINI_SUB(GSUB_BCACHE);  // GINI-xv6: board probe bread

— the instrumentation is visible rather than hidden, and the board's numbers stop being magic.

Everything here is pure: text in, dataclasses out. No Qt, no HTTP.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from .kernel_board import BLOCK_FILES, SUBSYSTEMS

# The probes the patcher inserts, which are also the block's public API. Scanning for these is how
# the jump list is built: derived from the file actually being shown, never from a table that can
# drift away from it.
_PROBE = re.compile(r"^\s*GINI_SUB\(GSUB_(\w+)\);\s*//\s*GINI-xv6: board probe (\w+)", re.M)

# xv6 puts the return type on its own line, so a function definition is a line that STARTS with
# the name. Used for the fallback outline when a file has no probes at all (headers, string.c).
_DEFN = re.compile(r"^([a-z_][a-z0-9_]*)\([^;{]*\)\s*$", re.M)

SOURCE_SUFFIXES = (".c", ".h", ".S")


@dataclass(frozen=True)
class Entry:
    """One jump target in the open file."""
    name: str
    line: int                      # 1-based, so it matches what an editor would say
    block: str = ""                # the board block it belongs to, when the probe says so

    @property
    def label(self) -> str:
        return f"{self.name}  ·  line {self.line}"


@dataclass
class SourceFile:
    """A served file plus the jump list scanned out of it."""
    path: str = ""
    text: str = ""
    entries: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """False for the agent's refusal/not-found replies, which are valid text but not source.

        They are deliberately shaped as C comments so they render harmlessly if this check is ever
        missed, but the browser should show them as an error rather than as a one-line file.
        """
        return bool(self.text) and not self.text.startswith("// refused") \
            and not self.text.startswith("// not found") \
            and not self.text.startswith("// no file") \
            and not self.text.startswith("// unreadable")

    @property
    def lines(self) -> int:
        return self.text.count("\n") + 1 if self.text else 0

    @property
    def error(self) -> str:
        return "" if self.ok else self.text.strip().lstrip("/ ").strip()


def files_for(block: str) -> list:
    """The source files behind a board block. `memory` is two files, most are one."""
    return list(BLOCK_FILES.get(block, ()))


def safe_rel(path: str) -> str:
    """Normalise a request to a tree-relative path, or "" if it tries to escape.

    The agent refuses traversal too, and that check is the one that actually matters — this is the
    near side of the same door, so a malformed request never leaves the app in the first place and
    the UI can say why without a round trip.
    """
    if not path:
        return ""
    p = path.replace("\\", "/").strip()
    if p.startswith("/"):
        return ""
    norm = posixpath.normpath(p)
    if norm.startswith("../") or norm == ".." or norm.startswith("/"):
        return ""
    if not norm.endswith(SOURCE_SUFFIXES):
        return ""
    return norm


def parse_source(path: str, text: str) -> SourceFile:
    """Wrap served text with a jump list scanned out of it."""
    sf = SourceFile(path=path, text=text or "")
    if sf.ok:
        sf.entries = entries_in(sf.text)
    return sf


def entries_in(text: str) -> list:
    """The jump targets in a file, best first.

    Probed entry points lead, because those are the functions the board actually counts — "the
    doors into this block". A file with no probes (a header, or an internal-only file) falls back
    to its function definitions so the browser is never just a wall of text.
    """
    out, seen = [], set()
    for m in _PROBE.finditer(text):
        block, fn = m.group(1).lower(), m.group(2)
        if fn in seen:
            continue
        seen.add(fn)
        # the probe sits on the line AFTER the opening brace, which is two below the name
        out.append(Entry(name=fn, line=_line_of(text, m.start()) - 2,
                         block=block if block in SUBSYSTEMS else ""))
    if out:
        return out
    for m in _DEFN.finditer(text):
        fn = m.group(1)
        if fn in seen or fn in ("if", "for", "while", "switch", "return", "sizeof"):
            continue
        seen.add(fn)
        out.append(Entry(name=fn, line=_line_of(text, m.start())))
    return out


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def find_line(text: str, needle: str) -> int:
    """1-based line of the first occurrence, or 0. Used to jump to a name from the entry list."""
    i = text.find(needle)
    return _line_of(text, i) if i >= 0 else 0

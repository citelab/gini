"""T-Lab 04 — You are the file system. The face over `gini.domain.fslab`.

The student is the kernel's block layer: they click the blocks `open()` reads, follow the pointers
a 1-byte `read()` follows, write into a new file and watch which blocks it takes, and build the
trace of an append one R or W at a time, with GINI checking every step against the tutorial's
seven rules. Then a **Try every …** sweeps every case of the level and says the pattern in a green
box. The design is `docs/design/t-lab-04.md`; the interaction is the approved mockup's
(`prototype.html` beside the reference engine), whose layout, wording and behaviour this file
follows. Where the mockup and the reference engine disagreed, the engine won, because the golden
tests check it.

Two halves, and the seam is the point, as in `sched_lab`:

* **The top half is a view model with no Qt in it.** `kind_of`, `block_info`, `walk_judge`,
  `walk_table`, `file_strip`, `every_byte` and friends are pure functions from the engine's
  results to what is drawn, and `Session` holds what the student has chosen — level, start, seed,
  the clicks so far — and judges each click. Everything a behaviour test wants to assert is
  reachable without a widget (`tests/test_fs_lab_model.py`).
* **The bottom half draws it.** One window, 940 × 700. The disk map, the i-node with its index
  grids, the file strip, the Every-block grids and the Every-length chart are painted; the tables
  are rich-text labels, because the tutorial prints them as tables.

**One entry per goal, never per click.** A goal is reached once per window; the chain gets one
`fs_walk` / `fs_read` / `fs_make` / `fs_trace` entry the first time, carrying exactly what the
engine's replays (`walk_clicks`, `pointer_clicks`, `trace_steps`) need to judge it again: every
click the face judged, in order, wrong ones included, so a replay counts the same wrong clicks the
student saw. A walk, read or trace that used Show me or Watch reaches no goal, except Past the
end, Big and thin and All three ranges, which are about the answer rather than the route to it.

**No worker thread, by design.** Every sweep here runs on the GUI thread in well under a frame:
Try every block costs one `read_call` (no copy) per file block, Try every length is memoised in
the engine to a handful of `write_call`s, and the Every-block sweep of level 3 uses
`blocks_to_take`, which never copies the disk. Watch is a `QTimer`, not a thread. Keep it that way;
if a sweep ever needs a thread, read known issue 17 first.

Colour is never the only signal. A block read carries the badge `R3`, a block written `W5`, a
block both `3·4`; a buffer hit is a grey row that *says* "no read"; every pointer cell's name and
value are its tooltip; the Every-block grids have a legend with counts; goal chips carry ✓ or ○.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from ..domain import fslab as F

BS = F.BS

#: The side tabs of each level, in order: (key, label).
TABS = {
    "walk": (("table", "Table"), ("every", "Every path")),
    "byte": (("file", "The file"), ("every", "Every block")),
    "make": (("writes", "Writes"), ("every", "Every block")),
    "append": (("plan", "Plan"), ("table", "Table"), ("every", "Every length")),
}

#: The seed a Random start opens on before Random is first pressed (the mockup's).
DEFAULT_SEEDS = {"walk": 7, "byte": 3, "append": 5, "make": 11}

#: A read that has been followed to its end.
DONE = 99

#: Level 3 refuses one write longer than this: the file strip and the sweep stay readable, and
#: the lesson is in where a write lands, not in how much it carries.
MAX_MAKE_WRITE = 300_000

#: Level 4's bounds on a typed write (the mockup's): enough to cross several blocks and into the
#: single range, small enough that the trace table stays a table.
MAX_APPEND_N = 20_000
MAX_APPEND_OFF = 300_000

WALK_INSIDE = ("Point at a block to see what it holds. The i-node table is blocks 2–33: "
               "i-node i is in block 2 + ⌊i/8⌋.")
GUESS_PATHS = "Guess first: how many reads does each open cost? Then press Try every path."
GUESS_BYTES = ("Guess first: where in this file does a 1-byte read cost 0, 1, 2 or 3 reads? "
               "Then press Try every block.")
GUESS_MAKE = ("Guess first: how many new blocks would a 1-byte write take, in each part of this "
              "file? Then press Try every block.")
GUESS_LENGTH = ("Guess first: as the number of bytes grows, when does the count jump? Then "
                "press Try every length.")
PLAN_FIRST = ("Work it out first: which file blocks does this write touch, which of them exist "
              "already, and which blocks must the file system take?")

EVERY_PATH_BOX = (
    "Each directory on the path costs 2 reads (its i-node block, then its data), and the last "
    "name costs 1 more (its i-node). A block already read in the same call is free (rule 3), so "
    "i-nodes that share a block, and a link’s second walk, cost less. A name in a later "
    "directory block costs one read per block searched.")
EVERY_BYTE_BOX = (
    "A block that exists costs 1 read, plus 1 for each index block on the way: 1 in the direct "
    "range, 2 in the single range, 3 in the double range. A hole costs the index blocks read "
    "before the kernel meets a 0: 0 if the 0 is in the i-node.")
EVERY_MAKE_BOX = (
    "A write into a hole takes a data block, plus every index block that is still missing on "
    "the way: up to 1 in the single range, 2 in the double range and 3 in the triple range. A "
    "block that exists takes nothing.")
EVERY_APPEND_BOX = (
    "The count changes only where the write crosses into another block: one more W for each new "
    "block. The first new block also costs the superblock (R + W), and a new index block, when "
    "the file needs one, costs one more W.")
EVERY_AT_BOX = (
    "A block that the write changes in part costs R + W; a block it replaces whole costs W. Past "
    "the end of the file, new blocks cost W each, plus the superblock (R + W) once, and the "
    "i-node block (R + W) because the size grows.")
PLAN_RULES = ("Rule 4: a block changed in part costs R + W; a new block, or one replaced whole, "
              "costs W. Rule 5: taking blocks costs R + W of the superblock, once. Rule 6: a new "
              "size or a new pointer costs R + W of the i-node block.")


def num(n: int) -> str:
    return F.num(n)


def plural(n: int, word: str, many: str = "") -> str:
    return f"{num(n)} {word if n == 1 else (many or word + 's')}"


def parse_num(s) -> int | None:
    """"300,000", "300 000" and "300_000" are all 300000; anything else is None."""
    t = re.sub(r"[,\s_]", "", str(s or ""))
    if not re.fullmatch(r"-?\d+", t):
        return None
    return int(t)


# --------------------------------------------------------------------------- #
# what a block holds
# --------------------------------------------------------------------------- #
#: The disk map's kinds in legend order, with the legend's words.
LEGEND = (("boot", "boot"), ("super", "superblock"), ("inode2", "i-node table"),
          ("dir", "directory"), ("data", "file data"), ("index", "index block"),
          ("link", "link path"), ("other", "other files"), ("free", "free"))


def kind_of(fs: F.FS, b: int) -> str:
    """What the disk map colours block b as. An i-node block is `inode2` (darker) when it holds
    an i-node in use, `inode` otherwise."""
    if b == 0:
        return "boot"
    if b == F.SUPER:
        return "super"
    if b < F.FIRST_DATA:
        lo = (b - 2) * F.IPB
        return "inode2" if any(i in fs.inodes for i in range(lo, lo + F.IPB)) else "inode"
    k = fs.blocks.get(b)
    if k is not None:
        return k.kind
    return "other" if b < fs.first_free else "free"


def block_info(fs: F.FS, b: int) -> tuple:
    """(head, rest): what the strip under the disk map says about block b, in words. The head is
    drawn bold."""
    k = kind_of(fs, b)
    if k == "boot":
        return "Block 0 · boot block.", "Bootstrap code. File operations never touch it."
    if k == "super":
        f = fs.next_free()
        return ("Block 1 · superblock.",
                (f"The free-block list ({f}, {f + 1}, {f + 2}, …, handed out in increasing "
                "order) and the free-i-node list."))
    if k in ("inode", "inode2"):
        lo = (b - 2) * F.IPB
        used = [fs.inodes[i] for i in range(lo, lo + F.IPB) if i in fs.inodes]
        head = f"i-node block {b}"
        rest = f"holds i-nodes {lo}–{lo + F.IPB - 1} (2 + ⌊i/8⌋ = {b}). "
        if used:
            rest += " · ".join(
                f"{i.num} {i.path} ("
                + ("symbolic link" if i.kind == "link" else f"{i.kind}, {num(i.size)} bytes")
                + ")" for i in used)
        else:
            rest += "None of them is in use."
        return head, rest
    bl = fs.blocks.get(b)
    o = fs.inodes.get(bl.owner) if bl is not None else None
    if k == "dir" and o is not None:
        lo = bl.fb * F.EPB
        hi = min(o.count, lo + F.EPB) - 1
        es = [e for e in o.entries if lo <= e[0] <= hi]
        others = hi - lo + 1 - len(es)
        return (f"Block {b} · {o.path},",
                f"directory block {bl.fb}, entries {lo}–{hi}: "
                + " · ".join(f"{e[1]} {e[2]}" for e in es)
                + (f" and {others} other entries" if others > 0 else "") + ".")
    if k == "data" and o is not None:
        lo = bl.fb * BS
        hi = min(o.size, lo + BS) - 1
        held = max(0, hi - lo + 1)
        return (f"Block {b} · {o.path},",
                f"file block {bl.fb}: bytes {num(lo)}–{num(max(lo, hi))}"
                + (f" ({num(held)} bytes; the rest of the block is unused)" if held < BS else "")
                + ".")
    if k == "index" and o is not None:
        es = sorted(bl.ptrs)
        shown = ", ".join(f"[{e}] → {bl.ptrs[e]}" for e in es[:6]) + (", …" if len(es) > 6
                                                                       else "")
        return (f"Block {b} · {bl.what or 'index block'}",
                f"of {o.path}: {len(es)} of 256 entries in use"
                + (f": {shown}" if es else "") + ". Every other entry is 0.")
    if k == "link" and o is not None:
        return f"Block {b} · {o.path},", f"a symbolic link: it holds the path {o.target}."
    if k == "other":
        return f"Block {b}", "is in use by files that are not in the table."
    return f"Block {b}", "is free."


def disk_blocks(*filesystems, marks=()) -> int:
    """How many blocks the disk map draws: at least 80, past the first free block and every block
    in use or marked, rounded up to a whole row of 20."""
    top = 79
    for fs in filesystems:
        top = max(top, fs.first_free + 3, *(fs.blocks or [0]))
    top = max([top, *marks])
    return -(-(top + 1) // 20) * 20


@dataclass(frozen=True)
class Mark:
    """A ring (read, written or both) and the badge under the number: `R3`, `W5`, `3·4`."""
    r: bool
    w: bool
    txt: str


def walk_marks(rows, done: int) -> dict:
    marks, k = {}, 0
    for r in rows[:done]:
        if r.op == "R":
            k += 1
            marks[r.block] = Mark(True, False, f"R{k}")
    return marks


def trace_marks(done) -> dict:
    acc: dict = {}
    for i, (b, op) in enumerate(done):
        m = acc.setdefault(b, {"r": False, "w": False, "n": []})
        m["r" if op == "R" else "w"] = True
        m["n"].append(i + 1)
    out = {}
    for b, m in acc.items():
        txt = ("·".join(str(n) for n in m["n"]) if (m["r"] and m["w"])
               else ("R" if m["r"] else "W") + str(m["n"][0]))
        out[b] = Mark(m["r"], m["w"], txt)
    return out


# --------------------------------------------------------------------------- #
# level 1: walk the path
# --------------------------------------------------------------------------- #
def need_hint(row: F.Row) -> str:
    """The formula, never the answer: where an i-node lives, or that the i-node lists the
    block."""
    m = re.match(r"^i-node (\d+)", row.need)
    if m:
        return f" i-node {m.group(1)} is in block 2 + ⌊{m.group(1)}/8⌋."
    if row.need.startswith("the data"):
        return " Its i-node, already read, lists the block."
    return ""


def walk_judge(rows, done: int, b: int) -> tuple:
    """One click on block b, judged as `fslab.walk_clicks` judges it. Returns (new done, the
    green line, the red line); the red line is set only for a wrong click, and then `done` does
    not move."""
    if done >= len(rows):
        return done, "", ""
    nxt = rows[done]
    j = done
    while j < len(rows) and rows[j].op == "hit":
        j += 1
    if nxt.op == "hit" and nxt.block == b:
        return done + 1, f"Block {b} is already in this call’s buffer: no read (rule 3).", ""
    if j < len(rows) and rows[j].block == b:
        ok = ""
        if j > done:
            passed = ", ".join(str(r.block) for r in rows[done:j])
            ok = (f"Blocks {passed} were already in the buffer (rule 3), so the next read is "
                  f"block {b}.") if j - done > 1 else (
                  f"Block {passed} was already in the buffer (rule 3), so the next read is "
                  f"block {b}.")
        return j + 1, ok, ""
    return done, "", f"Not block {b}. The kernel needs {nxt.need} next." + need_hint(nxt)


@dataclass(frozen=True)
class TableRow:
    """One row of a trace table: `n` is blank for a buffer hit, which the tutorial does not
    number; `kind` is R, W, hit or pend (the row still to fill)."""
    n: str
    block: str
    why: str
    kind: str
    op: str = ""


def walk_table(rows, done: int) -> tuple:
    out, k = [], 0
    for r in rows[:done]:
        if r.op == "R":
            k += 1
            out.append(TableRow(str(k), F.label(r.block), r.why, "R"))
        else:
            out.append(TableRow("", F.label(r.block),
                                "no read, already in this call’s buffer (rule 3). " + r.why,
                                "hit"))
    if done < len(rows):
        out.append(TableRow(str(k + 1), "?", "click a block on the disk", "pend"))
    return tuple(out)


def walk_totals(rows, done: int, wrong: int, hints: int) -> tuple:
    """(the count, the rest): "9 R" and "1 found in the buffer · 0 wrong clicks · Show me used 0
    times"."""
    r = sum(1 for x in rows[:done] if x.op == "R")
    hits = sum(1 for x in rows[:done] if x.op == "hit")
    return (f"{r} R", (f"{num(hits)} found in the buffer · {plural(wrong, 'wrong click')} · "
                      f"Show me used {plural(hints, 'time')}"))


# --------------------------------------------------------------------------- #
# levels 2 and 3: pointers
# --------------------------------------------------------------------------- #
def slot_text(slot: str) -> str:
    """What a pointer in the i-node serves, in file blocks and bytes."""
    if slot.startswith("direct"):
        k = int(slot[7:-1])
        return f"{slot} serves file block {k}: bytes {num(k * BS)}–{num(k * BS + BS - 1)}."
    return {"single": "single serves file blocks 12–267: bytes 12,288–274,431.",
            "double": "double serves file blocks 268–65,803: bytes 274,432–67,383,295.",
            "triple": "triple serves file blocks 65,804 onward: bytes 67,383,296 onward."}[slot]


def serves_text(key, e: int) -> str:
    """What entry e of an index block serves: "single[46] serves file block 58: bytes
    59,392–60,415."."""
    top, pre = key
    nm = top + "".join(f"[{z}]" for z in pre) + f"[{e}]"
    if top == "single":
        lo = hi = F.S0 + e
    elif top == "double":
        if not pre:
            lo = F.D0 + e * F.NPTR
            hi = lo + F.NPTR - 1
        else:
            lo = hi = F.D0 + pre[0] * F.NPTR + e
    else:
        base = F.T0 + sum(z * F.NPTR ** (2 - i) for i, z in enumerate(pre))
        sp = F.NPTR ** (2 - len(pre))
        lo = base + e * sp
        hi = lo + sp - 1
    blocks = f" {num(lo)}" if lo == hi else f"s {num(lo)}–{num(hi)}"
    return f"{nm} serves file block{blocks}: bytes {num(lo * BS)}–{num(hi * BS + BS - 1)}."


@dataclass(frozen=True)
class ReadLog:
    """A finished read, as a row of the tutorial's table: Offset | File block | Pointer |
    Reads."""
    offset: int
    fb: int
    pointer: str
    reads: int
    result: str
    zero: str = ""
    shown: bool = False


def read_log_cells(x: ReadLog) -> tuple:
    if x.result == "eof":
        return num(x.offset), "–", "past the end: 0 bytes", str(x.reads)
    if x.result == "hole":
        return num(x.offset), num(x.fb), f"{x.pointer}: a hole; {x.zero}", str(x.reads)
    return num(x.offset), num(x.fb), x.pointer, str(x.reads)


def finish_text(res: dict, offset: int, size: int, kind: str) -> str:
    reads = 0 if kind == "eof" else len(res["rows"])
    if kind == "eof":
        return (f"Offset {num(offset)} is at or past the end (size {num(size)}): read returns 0 "
                "bytes. The size is in the i-node, so no block is read.")
    if res["result"] == "hole":
        return (f"A hole: {res['zero']}. The read returns a zero after "
                f"{plural(reads, 'read')}.")
    idx = reads - 1
    via = f"{plural(idx, 'index block')} and " if idx else ""
    return (f"{res['pointer']} → block {res['block']}: {plural(reads, 'read')}, "
            f"{via}the data block.")


@dataclass(frozen=True)
class Band:
    title: str
    first: int
    n: int


def bands(last_fb: int) -> tuple:
    """The Every-block grids: the direct range, the single range if the file reaches it, and up
    to six double[a] grids. A file that reaches further is drawn to double[5]; the count says
    so."""
    out = [Band("direct · file blocks 0–11", 0, 12)]
    if last_fb >= F.S0:
        out.append(Band("single · 12–267", F.S0, F.NPTR))
    a = 0
    while F.D0 + a * F.NPTR <= last_fb and a < 6:
        lo = F.D0 + a * F.NPTR
        out.append(Band(f"double[{a}] · {num(lo)}–{num(lo + F.NPTR - 1)}", lo, F.NPTR))
        a += 1
    return tuple(out)


def every_byte(fs: F.FS, inum: int) -> dict:
    """file block -> (result, reads) for a 1-byte read at the start of every file block."""
    ino = fs.inodes[inum]
    out = {}
    for fb in range((ino.size - 1) // BS + 1 if ino.size else 0):
        r = F.read_call(fs, inum, fb * BS)
        out[fb] = (r["result"], len(r["rows"]))
    return out


#: The kinds Every block can show on level 2, in legend order.
BYTE_KINDS = (("data", 1), ("data", 2), ("data", 3), ("hole", 0), ("hole", 1), ("hole", 2))


def byte_legend(ev: dict) -> tuple:
    """((kind, reads, count), ...) for the kinds this file has, in legend order."""
    tally: dict = {}
    for kind, n in ev.values():
        tally[(kind, n)] = tally.get((kind, n), 0) + 1
    return tuple((k, n, tally[(k, n)]) for k, n in BYTE_KINDS if (k, n) in tally)


def byte_legend_text(kind: str, n: int) -> str:
    return f"{'data' if kind == 'data' else 'hole'}, {n} R"


def every_make(fs: F.FS, inum: int) -> dict:
    """file block -> blocks a 1-byte write there would take now, over the bands drawn, plus
    'triple'."""
    ino = fs.inodes[inum]
    last = max(F.D0 + 2 * F.NPTR + F.NPTR - 1, (ino.size - 1) // BS if ino.size else 0)
    ev: dict = {}
    for bd in bands(min(last, F.D0 + 6 * F.NPTR - 1)):
        for e in range(bd.n):
            ev[bd.first + e] = F.blocks_to_take(fs, inum, bd.first + e)
    ev["triple"] = F.blocks_to_take(fs, inum, F.T0)
    return ev


def write_summary(res: F.WriteResult, off: int, n: int) -> str:
    """"Bytes 0–99: file block 0 (direct[0]). Took 1 block: 61."."""
    last = off + n - 1
    p0, p1 = res.plan[0]["fb"], res.plan[-1]["fb"]
    where = (f"file blocks {p0}–{p1} ({F.pointer_name(p0)} to {F.pointer_name(p1)})"
             if len(res.plan) > 1 else f"file block {p0} ({F.pointer_name(p0)})")
    took = (f"Took {plural(len(res.alloc), 'block')}: " + taken_text(res.alloc) + "."
            if res.alloc else "Took no new block.")
    return f"Bytes {num(off)}–{num(last)}: {where}. {took}"


def taken_text(alloc) -> str:
    return ", ".join(f"{b} (index)" if w == "index" else str(b) for b, w in alloc)


def file_blocks_text(res: F.WriteResult) -> str:
    fbs = [p["fb"] for p in res.plan]
    if len(fbs) > 3:
        return f"file blocks {fbs[0]}–{fbs[-1]}"
    return ("file blocks " if len(fbs) > 1 else "file block ") + ", ".join(str(x) for x in fbs)


# --------------------------------------------------------------------------- #
# level 4: append and overwrite
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StripCell:
    """One file block in the strip above the disk map. `fb` is None for the "…" gap; `used` is
    how many bytes it holds (0 when it does not exist); `cover` is the (from, to) fraction this
    write covers, or None."""
    fb: int | None
    exists: bool = False
    used: int = 0
    cover: tuple | None = None
    pointer: str = ""


def strip_pointer(fb: int) -> str:
    """The mockup's short names under the strip: d[11], s[0], [0][24]."""
    return (F.pointer_name(fb).replace("direct", "d").replace("single", "s")
            .replace("double", "").replace("triple", "t"))


def file_strip(fs: F.FS, inum: int, res: F.WriteResult) -> tuple:
    ino = fs.inodes[inum]
    last0 = (ino.size - 1) // BS if ino.size else -1
    p0, p1 = res.plan[0]["fb"], res.plan[-1]["fb"]
    fbs: list = list(range(max(0, p0 - 2), p1 + 2))
    if len(fbs) > 15:
        fbs = fbs[:7] + [None] + fbs[-7:]
    plan = {p["fb"]: p for p in res.plan}
    out = []
    for f in fbs:
        if f is None:
            out.append(StripCell(None))
            continue
        exists = f <= last0 and bool(F.lookup(fs, ino, f)[-1][2])
        used = max(0, min(BS, ino.size - f * BS)) if exists else 0
        pl = plan.get(f)
        cover = ((pl["lo"] - f * BS) / BS, (pl["hi"] + 1 - f * BS) / BS) if pl else None
        out.append(StripCell(f, exists, used, cover, strip_pointer(f)))
    return tuple(out)


def plan_rows(res: F.WriteResult) -> tuple:
    """(file block, bytes written, status) per block the write touches."""
    out = []
    for p in res.plan:
        status = ("new → " + str(p["block"]) if p["status"] == "new" else
                  f"exists ({p['block']}), changed in part" if p["status"] == "partial" else
                  f"exists ({p['block']}), replaced whole")
        out.append((str(p["fb"]), f"{num(p['lo'])}–{num(p['hi'])}", status))
    return tuple(out)


def trace_table(res: F.WriteResult, done) -> tuple:
    have = {(r.block, r.op): r for r in res.rows}
    return tuple(TableRow(str(i + 1), F.label(b), have[(b, op)].why, op, op)
                 for i, (b, op) in enumerate(done))


# --------------------------------------------------------------------------- #
# goals
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Chip:
    id: str
    text: str
    done: bool
    tooltip: str


def goal_chips(level: F.Level, achieved) -> tuple:
    return tuple(Chip(id=g.id, text=g.chip, done=f"{level.id}:{g.id}" in achieved,
                      tooltip=g.text) for g in level.goals)


# --------------------------------------------------------------------------- #
# the session
# --------------------------------------------------------------------------- #
@dataclass
class WalkState:
    path: str = ""
    clicks: list = field(default_factory=list)
    hints: int = 0
    msg: str = ""
    ok: str = ""
    every: list | None = None
    hide_ib: bool = False


@dataclass
class ByteState:
    offset: int = 0
    stage: int = 0
    clicks: list = field(default_factory=list)      # [(value, correct)]
    hints: int = 0
    log: list = field(default_factory=list)
    msg: str = ""
    ok: str = ""
    every: dict | None = None


@dataclass
class MakeState:
    writes: list = field(default_factory=list)
    off: int = 0
    n: int = 100
    msg: str = ""
    every: dict | None = None


@dataclass
class AppendState:
    op: str = "append"
    n: int = 0
    off: int = 0
    done: list = field(default_factory=list)
    steps: list = field(default_factory=list)       # [((block, op), accepted)]
    sel: int | None = None
    hints: int = 0
    refused: int = 0
    msg: str = ""
    ok: str = ""
    plan: bool = False
    every: dict | None = None


class Session:
    """What the student has chosen, and nothing Qt. One per window.

    Each level keeps its own state (`walk`, `byte`, `make`, `append`), so switching levels and
    back does not lose a half-built trace; picking a start, Random or Reset starts that level
    over. Every click goes through a method here, which judges it with the engine and keeps the
    clicks in the order they came, because those clicks are what the chain records.

    `on_event(kind, payload)` is told once per goal, the first time it is reached: `fs_walk`,
    `fs_read`, `fs_make` or `fs_trace`, with the fields `proof_events` builds those entries from.
    It is a callback and not a signal so this class stays importable without Qt.
    """

    def __init__(self, lab: F.LevelFile, on_event=None, rng=None) -> None:
        self.lab = lab
        self.on_event = on_event
        self.rng = rng or random.Random()
        self.level_index = 0
        self.achieved: set = set()
        self.inspect: int | None = None
        self._start = {lv.id: 0 for lv in lab.levels}
        self._seed = {lv.id: DEFAULT_SEEDS.get(lv.id, 1) for lv in lab.levels}
        self._tab = {lv.id: TABS.get(lv.id, (("", ""),))[0][0] for lv in lab.levels}
        self._worlds: dict = {}
        self._rows_cache: dict = {}
        self._make_cache: tuple = ((), None)
        self.walk = WalkState()
        self.byte = ByteState()
        self.make = MakeState()
        self.append = AppendState()
        for lv in lab.levels:
            self._init(lv.id)

    # -- what is loaded ------------------------------------------------------ #
    @property
    def level(self) -> F.Level:
        return self.lab.levels[self.level_index]

    @property
    def start_index(self) -> int:
        return self._start[self.level.id]

    @property
    def start(self) -> F.Start:
        return self.level.starts[self.start_index]

    @property
    def tab(self) -> str:
        return self._tab[self.level.id]

    @tab.setter
    def tab(self, name: str) -> None:
        if name in {k for k, _ in TABS.get(self.level.id, ())}:
            self._tab[self.level.id] = name

    def seed(self, level_id: str | None = None) -> int:
        return self._seed[level_id or self.level.id]

    def world(self, level_id: str | None = None) -> F.World:
        lid = level_id or self.level.id
        lv = self.lab.level(lid)
        s = lv.starts[self._start[lid]]
        key = (lid, s.label, self._seed[lid] if s.random else None)
        if key not in self._worlds and len(self._worlds) > 32:
            # Each Random press makes a world; keep the cache from growing for a whole term.
            self._worlds.clear()
            self._rows_cache.clear()
        if key not in self._worlds:
            self._worlds[key] = F.start_world(self.lab, lid, s.label,
                                              self._seed[lid] if s.random else None)
        return self._worlds[key]

    def _init(self, lid: str) -> None:
        w = self.world(lid)
        if lid == "walk":
            self.walk = WalkState(path=w.path, hide_ib=self.walk.hide_ib)
        elif lid == "byte":
            self.byte = ByteState(offset=w.offset)
        elif lid == "make":
            self.make = MakeState()
        elif lid == "append":
            op, n, off = w.presets[0] if w.presets else ("append", 1, 0)
            self.append = AppendState(op=op, n=n, off=off)
        self.inspect = None

    def can_undo(self) -> bool:
        lid = self.level.id
        if lid == "walk":
            return self.walk_done() > 0
        if lid == "byte":
            return bool(self.byte.log) or 0 < self.byte.stage < DONE
        if lid == "make":
            return bool(self.make.writes)
        return bool(self.append.done)

    # -- choosing ------------------------------------------------------------ #
    def pick_level(self, index: int) -> None:
        if 0 <= index < len(self.lab.levels):
            self.level_index = index
            self.inspect = None

    def pick_start(self, index: int) -> None:
        if 0 <= index < len(self.level.starts):
            self._start[self.level.id] = index
            self._init(self.level.id)

    def random(self) -> None:
        """Random: a fresh seed and the Random start, or on level 3 a random write."""
        lid = self.level.id
        if lid == "make":
            r = self.rng
            o, n = r.choice([
                (r.randrange(12) * BS + 7, 1 + r.randrange(3000)),
                ((F.S0 + r.randrange(256)) * BS, 1 + r.randrange(2000)),
                ((F.D0 + r.randrange(768)) * BS + 100, 1 + r.randrange(1500))])
            self.make.off, self.make.n = o, n
            self.make_write(o, n)
            return
        idx = next((i for i, s in enumerate(self.level.starts) if s.random), None)
        if idx is None:
            return
        self._seed[lid] = self.rng.randint(1, 100_000)
        self._start[lid] = idx
        self._init(lid)

    def reset(self) -> None:
        lid = self.level.id
        if lid == "walk":
            # Reset starts the walk over on the path chosen, not the start's.
            path, hide = self.walk.path, self.walk.hide_ib
            self.walk = WalkState(path=path, hide_ib=hide)
            self.inspect = None
        else:
            self._init(lid)

    def undo(self) -> None:
        lid = self.level.id
        if lid == "walk":
            self._walk_undo()
        elif lid == "byte":
            self._byte_undo()
        elif lid == "make":
            if self.make.writes:
                self.make.writes.pop()
            self.make.msg = ""
            self.make.every = None
        else:
            self._append_undo()

    def try_every(self) -> None:
        lid = self.level.id
        w = self.world()
        self.tab = "every"
        if lid == "walk":
            self.walk.every = F.every_path(w.fs)
        elif lid == "byte":
            self.byte.every = every_byte(w.fs, w.inum)
        elif lid == "make":
            fs, _ = self.make_state()
            self.make.every = every_make(fs, w.inum)
        else:
            st = self.append
            upto = min(8192, max(4096, st.n * 2))
            seq = F.every_length(w.fs, w.inum, upto, st.off, st.op == "append")
            self.append.every = {"seq": seq, "iv": F.intervals(seq), "upto": upto, "op": st.op,
                                 "off": st.off}

    # -- events -------------------------------------------------------------- #
    def _reach(self, goal_ids, hints: int, kind: str, payload: dict) -> None:
        lv = self.level
        for gid in goal_ids:
            g = lv.goal(gid)
            k = f"{lv.id}:{gid}"
            if g is None or k in self.achieved or (hints and g.needs_no_hints):
                continue
            self.achieved.add(k)
            if self.on_event is not None:
                d = {"lab": self.lab.id, "level": lv.id}
                d.update(payload)
                d["goal"] = gid
                self.on_event(kind, d)

    def _start_fields(self) -> dict:
        w = self.world()
        return {"start": w.label, "seed": w.seed}

    # -- level 1 ------------------------------------------------------------- #
    def walk_rows(self) -> list:
        w = self.world("walk")
        key = (id(w), self.walk.path)
        if key not in self._rows_cache:
            self._rows_cache[key] = F.open_call(w.fs, self.walk.path)
        return self._rows_cache[key][0]

    def walk_end(self) -> int:
        self.walk_rows()
        return self._rows_cache[(id(self.world("walk")), self.walk.path)][1]

    def walk_verdict(self) -> dict:
        return F.walk_clicks(self.world("walk").fs, self.walk.path, self.walk.clicks)

    def walk_done(self) -> int:
        return self.walk_verdict()["done"]

    def walk_complete(self) -> bool:
        return self.walk_done() >= len(self.walk_rows())

    def pick_path(self, path: str) -> None:
        w = self.world("walk")
        if path not in {i.path for i in w.fs.inodes.values()}:
            return
        self.walk = WalkState(path=path, hide_ib=self.walk.hide_ib, every=self.walk.every)
        self.inspect = None

    def toggle_hide_ib(self) -> None:
        self.walk.hide_ib = not self.walk.hide_ib

    def walk_click(self, b: int) -> None:
        st = self.walk
        rows = self.walk_rows()
        done = self.walk_done()
        self.inspect = b
        if done >= len(rows):
            return
        new, ok, bad = walk_judge(rows, done, b)
        st.clicks.append(int(b))
        st.ok, st.msg = ok, bad
        if new != done:
            self._walk_check()

    def walk_show(self) -> bool:
        """Show me: the next block, as if clicked, counted as a hint. False when there is none."""
        st = self.walk
        rows = self.walk_rows()
        done = self.walk_done()
        if done >= len(rows):
            return False
        st.hints += 1
        st.clicks.append(rows[done].block)
        self.inspect = rows[done].block
        st.msg = st.ok = ""
        return True

    def _walk_check(self) -> None:
        st = self.walk
        v = self.walk_verdict()
        if not v["complete"]:
            return
        met = F.walk_goals(self.level, self.world("walk").label, st.path, v)
        self._reach(met, st.hints, "fs_walk",
                    dict(self._start_fields(), path=st.path, clicks=list(st.clicks),
                         hints=st.hints))

    def _walk_undo(self) -> None:
        st = self.walk
        w = self.world("walk")
        # Take back the last click that moved the walk, and any wrong ones after it.
        dones = [F.walk_clicks(w.fs, st.path, st.clicks[:i])["done"]
                 for i in range(len(st.clicks) + 1)]
        for i in range(len(st.clicks) - 1, -1, -1):
            if dones[i + 1] > dones[i]:
                del st.clicks[i:]
                break
        st.msg = st.ok = ""

    # -- level 2 ------------------------------------------------------------- #
    def read_res(self) -> dict:
        w = self.world("byte")
        return F.read_call(w.fs, w.inum, self.byte.offset)

    def read_hops(self) -> list:
        w = self.world("byte")
        r = self.read_res()
        if r["result"] == "eof":
            return []
        return F.lookup(w.fs, w.fs.inodes[w.inum], r["fb"])

    def file_size(self, level_id: str = "byte") -> int:
        w = self.world(level_id)
        return w.fs.inodes[w.inum].size

    def set_offset(self, value) -> bool:
        v = parse_num(value) if not isinstance(value, int) else value
        if v is None or v < 0:
            self.byte.msg, self.byte.ok = "Type an offset of 0 or more.", ""
            return False
        st = self.byte
        st.offset, st.stage, st.clicks, st.hints = v, 0, [], 0
        st.msg = st.ok = ""
        self.inspect = None
        return True

    def _size_first(self) -> None:
        self.byte.msg = (f"Look at the size first: the i-node says "
                         f"{num(self.file_size())} bytes.")
        self.byte.ok = ""

    def click_slot(self, slot: str) -> None:
        st = self.byte
        if st.stage == DONE:
            return
        r = self.read_res()
        if r["result"] == "eof":
            st.clicks.append((slot, False))
            self._size_first()
            return
        hops = self.read_hops()
        if st.stage > 0:
            st.msg = (f"The kernel has left the i-node: it is reading block {hops[st.stage][0]} "
                      "now. Click an entry in that block.")
            st.ok = ""
            return
        if slot != hops[0][1]:
            st.clicks.append((slot, False))
            st.msg, st.ok = "Not that pointer. " + slot_text(slot), ""
            return
        st.clicks.append((slot, True))
        self._advance()

    def click_entry(self, block: int, e: int) -> None:
        st = self.byte
        if st.stage == DONE:
            return
        r = self.read_res()
        if r["result"] == "eof":
            st.clicks.append((int(e), False))
            self._size_first()
            return
        if st.stage == 0:
            st.msg, st.ok = "Start in the i-node: which of its pointers serves this offset?", ""
            return
        hop = self.read_hops()[st.stage]
        if block != hop[0]:
            st.msg = f"The kernel is reading block {hop[0]} now. Click an entry in that block."
            st.ok = ""
            return
        if e != hop[1]:
            top, idx = F.route(r["fb"])
            st.clicks.append((int(e), False))
            st.msg = "Not that entry. " + serves_text((top, idx[:st.stage - 1]), e)
            st.ok = ""
            return
        st.clicks.append((int(e), True))
        self._advance()

    def _advance(self) -> None:
        st = self.byte
        hops = self.read_hops()
        v = hops[st.stage][2]
        if not v or st.stage == len(hops) - 1:
            self._finish(self.read_res()["result"])
            return
        st.stage += 1
        st.msg = ""
        st.ok = f"The kernel reads block {v}: {plural(st.stage, 'read')} so far. Which entry?"

    def _finish(self, kind: str, pressed: bool = False) -> None:
        st = self.byte
        r = self.read_res()
        reads = 0 if kind == "eof" else len(r["rows"])
        st.log.append(ReadLog(st.offset, r["fb"], r["pointer"], reads, r["result"],
                              r.get("zero", ""), st.hints > 0))
        st.ok = finish_text(r, st.offset, self.file_size(), kind)
        st.msg = ""
        st.stage = DONE
        if kind == "eof" and not pressed:
            return          # Show me said it; Past the end is for pressing the button
        clicks = [c for c, _ in st.clicks] + (["eof"] if kind == "eof" else [])
        self._reach(F.read_goals(self.level, r["result"], reads), st.hints, "fs_read",
                    dict(self._start_fields(), offset=st.offset, clicks=clicks, hints=st.hints))

    def press_eof(self) -> None:
        st = self.byte
        if st.stage == DONE:
            return
        r = self.read_res()
        if r["result"] == "eof":
            self._finish("eof", pressed=True)
            return
        st.clicks.append(("eof", False))
        st.msg = (f"Not past the end: the size is {num(self.file_size())}, so offset "
                  f"{num(st.offset)} is inside the file.")
        st.ok = ""

    def byte_show(self) -> None:
        st = self.byte
        if st.stage == DONE:
            return
        st.hints += 1
        if self.read_res()["result"] == "eof":
            self._finish("eof")
            return
        hops = self.read_hops()
        while st.stage != DONE:
            hop = hops[st.stage]
            st.clicks.append((hop[1], True))
            self._advance()

    def _byte_undo(self) -> None:
        st = self.byte
        if 0 < st.stage < DONE:
            st.stage -= 1
            for i in range(len(st.clicks) - 1, -1, -1):
                if st.clicks[i][1]:
                    del st.clicks[i:]
                    break
            st.msg = st.ok = ""
        elif st.log:
            st.log.pop()
            st.stage, st.clicks = 0, []
            st.msg = st.ok = ""

    # -- level 3 ------------------------------------------------------------- #
    def make_state(self) -> tuple:
        """(the file system after the writes, their results), cached on the writes."""
        key = tuple(self.make.writes)
        if self._make_cache[0] != key or self._make_cache[1] is None:
            w = self.world("make")
            fs, out = w.fs, []
            for o, n in key:
                r = F.write_call(fs, w.inum, o, n)
                out.append(r)
                fs = r.fs
            self._make_cache = (key, (fs, out))
        return self._make_cache[1]

    def make_write(self, o, n) -> bool:
        st = self.make
        o = parse_num(o) if not isinstance(o, int) else o
        n = parse_num(n) if not isinstance(n, int) else n
        if o is None or n is None or n < 1 or o < 0:
            st.msg = "Give an offset of 0 or more and a length of at least 1 byte."
            return False
        st.off, st.n = o, n
        if n > MAX_MAKE_WRITE:
            st.msg = f"Keep a single write to {num(MAX_MAKE_WRITE)} bytes or less here."
            return False
        if (o + n - 1) // BS >= F.TEND:
            st.msg = "That is past the largest file this i-node can describe."
            return False
        st.writes.append((o, n))
        st.msg = ""
        st.every = None
        fs, _ = self.make_state()
        self._reach(F.shape_goals(self.level, fs, self.world("make").inum), 0, "fs_make",
                    {"writes": [list(x) for x in st.writes]})
        return True

    # -- level 4 ------------------------------------------------------------- #
    def write_res(self) -> F.WriteResult:
        w = self.world("append")
        st = self.append
        key = ("w", id(w), st.op, st.n, st.off)
        if key not in self._rows_cache:
            lv = self.lab.level("append")
            self._rows_cache[key] = F.write_call(w.fs, w.inum, st.off, st.n,
                                                 append=st.op == "append",
                                                 index_first=lv.index_first if lv else True)
        return self._rows_cache[key]

    def append_complete(self) -> bool:
        return len(self.append.done) >= len(self.write_res().rows)

    def set_write(self, op: str, n, off=0) -> bool:
        st = self.append
        n = parse_num(n) if not isinstance(n, int) else n
        off = parse_num(off) if not isinstance(off, int) else off
        if op not in ("append", "at"):
            return False
        if (n is None or n < 1 or n > MAX_APPEND_N or off is None or off < 0
                or off > MAX_APPEND_OFF):
            st.msg, st.ok = (f"Use 1 to {num(MAX_APPEND_N)} bytes and an offset from 0 to "
                             f"{num(MAX_APPEND_OFF)} here."), ""
            return False
        # A new write is worked out afresh: the plan hides again until it is asked for.
        self.append = AppendState(op=op, n=n, off=off if op == "at" else 0)
        self.inspect = None
        return True

    def select_block(self, b: int) -> None:
        st = self.append
        st.sel = int(b)
        self.inspect = int(b)
        st.msg = st.ok = ""

    def commit(self, op: str) -> None:
        """R or W on the selected block, judged by `check_step`: a row with the tutorial's why,
        or the refusal's sentence and the count of refusals."""
        st = self.append
        res = self.write_res()
        if st.sel is None or self.append_complete():
            return
        cand = (st.sel, op)
        ok, msg = F.check_step(res, st.done, cand)
        st.steps.append((cand, ok))
        if ok:
            st.done.append(cand)
            st.msg = st.ok = ""
            st.sel = None
            self._append_check()
        else:
            st.msg, st.ok = msg, ""
            st.refused += 1

    def append_show(self) -> bool:
        st = self.append
        res = self.write_res()
        nxt = F.next_step(res, st.done)
        if nxt is None:
            return False
        st.hints += 1
        st.done.append((nxt.block, nxt.op))
        st.steps.append(((nxt.block, nxt.op), True))
        st.sel = None
        st.msg = st.ok = ""
        self.inspect = nxt.block
        self._append_check()
        return True

    def thats_all(self) -> None:
        st = self.append
        left = len(self.write_res().rows) - len(st.done)
        if left <= 0:
            return
        st.msg = (f"Not yet: {left} more block access{'es' if left > 1 else ''} to go. Check the "
                  "superblock (rule 5) and the i-node block (rule 6).")
        st.ok = ""

    def show_plan(self) -> None:
        self.append.plan = True

    def _append_check(self) -> None:
        st = self.append
        if not self.append_complete():
            return
        res = self.write_res()
        self._reach(F.trace_goals(self.level, res), st.hints, "fs_trace",
                    dict(self._start_fields(), op=st.op, offset=res.offset, n=st.n,
                         steps=[[b, o] for (b, o), _ok in st.steps], hints=st.hints))

    def _append_undo(self) -> None:
        st = self.append
        if not st.done:
            return
        st.done.pop()
        for i in range(len(st.steps) - 1, -1, -1):
            if st.steps[i][1]:
                del st.steps[i:]
                break
        st.msg = st.ok = ""

    def watch_step(self) -> bool:
        """One step of Watch: a Show me. False when there is nothing left to show."""
        if self.level.id == "walk":
            return self.walk_show()
        if self.level.id == "append":
            if self.append_complete():
                return False
            return self.append_show()
        return False


# --------------------------------------------------------------------------- #
# the face
# --------------------------------------------------------------------------- #
try:
    from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QToolTip,
        QVBoxLayout,
        QWidget,
    )
    _QT = True
except Exception:                                   # noqa: BLE001 — the view model needs no Qt
    _QT = False

if _QT:
    from html import escape as _esc

    from .flow_layout import FlowLayout
    from .theme import ThemeManager
    from .theme.manager import scale_css as _scss
    from .windowing import standalone

    SIDE_W = 290

    @dataclass
    class _Ink:
        """The colours, resolved once per theme. On a light theme the block kinds, rings and
        cost scales are the approved mockup's light palette; on a dark theme its dark palette,
        with the surfaces and text taken from the theme's own tokens so the window still looks
        like GINI."""
        kinds: dict
        super_line: str
        rd: str
        wr: str
        ok: str
        ok_bg: str
        ok_fg: str
        bad: str
        bad_bg: str
        bad_fg: str
        accent: str
        accent_soft: str
        accent_ink: str
        ink: str
        ink_fg: str
        text: str
        text2: str
        muted: str
        faint: str
        line: str
        line2: str
        panel: str
        panel2: str
        bg: str
        p_data: str
        p_index: str
        cost: dict

    def _flat(spec: str, base: str) -> str:
        """An `rgba(r,g,b,a)` token laid over `base`, as #rrggbb. The dark themes' soft tokens
        are translucent, and Qt's rich text (the trace tables' shaded row) reads only opaque
        names."""
        if not spec.startswith("rgba("):
            return spec
        r, g, b, a = (int(float(x)) for x in spec[5:-1].split(","))
        under = QColor(base)
        f = a / 255
        return QColor(round(r * f + under.red() * (1 - f)), round(g * f + under.green() * (1 - f)),
                      round(b * f + under.blue() * (1 - f))).name()

    def _ink(t) -> _Ink:
        if not t.dark:
            kinds = {"boot": "#d3d8df", "super": "#e4d9f7", "inode": "#e8eef8",
                     "inode2": "#c7d8f3", "dir": "#d6eedb", "data": "#f4e6c8",
                     "index": "#f6cfb2", "link": "#f4d5e4", "other": "#e6e9ee",
                     "free": "#ffffff"}
            cost = {"c1": "#9cc0f5", "c2": "#5d93e6", "c3": "#2a5fbf", "h0": "#eef1f5",
                    "h1": "#f3c79a", "h2": "#e08a45"}
            return _Ink(kinds=kinds, super_line="#7d5bc4", rd="#2560c8", wr="#c4501a",
                        ok="#23863a", ok_bg="#e1f2e4", ok_fg="#14532d", bad="#c62f2f",
                        bad_bg="#fbe4e4", bad_fg="#8f1d1d", accent=t.accent,
                        accent_soft="#dfe8fb", accent_ink="#1f4fb0", ink="#131a23",
                        ink_fg="#ffffff", text=t.text, text2=t.text, muted=t.muted,
                        faint=t.faint, line=t.line, line2=t.line2, panel=t.panel,
                        panel2=t.panel2, bg=t.bg, p_data="#d39b2e", p_index="#d9692c",
                        cost=cost)
        kinds = {"boot": "#2b323c", "super": "#33284a", "inode": "#1a2333", "inode2": "#253756",
                 "dir": "#1b3424", "data": "#3a301b", "index": "#4a2b1b", "link": "#422538",
                 "other": "#212831", "free": t.panel}
        cost = {"c1": "#2c4878", "c2": "#3f6cc0", "c3": "#7aa5ff", "h0": t.panel2,
                "h1": "#6b4220", "h2": "#c4702e"}
        return _Ink(kinds=kinds, super_line="#b39af0", rd="#7aa5ff", wr="#f0905a",
                    ok=t.success, ok_bg=_flat(t.success_soft, t.panel), ok_fg=t.text,
                    bad=t.danger, bad_bg=_flat(t.danger_soft, t.panel), bad_fg=t.text,
                    accent=t.accent, accent_soft=_flat(t.accent_soft, t.panel),
                    accent_ink=t.text, ink=t.text, ink_fg=t.bg,
                    text=t.text, text2=t.text, muted=t.muted, faint=t.faint, line=t.line,
                    line2=t.line2, panel=t.panel, panel2=t.panel2, bg=t.bg, p_data="#e0ad45",
                    p_index="#ef8a4e", cost=cost)

    def _mono(px: float, bold: bool = False) -> QFont:
        f = QFont()
        f.setStyleHint(QFont.Monospace)
        f.setFamilies(["Menlo", "Consolas", "DejaVu Sans Mono", "monospace"])
        f.setPixelSize(max(7, round(px)))
        f.setBold(bold)
        return f

    def _sans(px: float, bold: bool = False) -> QFont:
        f = QFont()
        f.setPixelSize(max(7, round(px)))
        f.setBold(bold)
        return f

    MONO_CSS = "font-family:Menlo,Consolas,'DejaVu Sans Mono',monospace;"

    def _clear_layout(lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                _clear_layout(it.layout())

    class _ElideLabel(QLabel):
        """A single-line label that gives way instead of setting the window's width."""

        def __init__(self, text: str = "", parent=None) -> None:
            super().__init__(text, parent)
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        def minimumSizeHint(self) -> QSize:
            return QSize(24, super().minimumSizeHint().height())

        def paintEvent(self, e) -> None:
            p = QPainter(self)
            p.setFont(self.font())
            p.setPen(self.palette().color(self.foregroundRole()))
            fm = QFontMetrics(self.font())
            p.drawText(self.rect(), int(self.alignment()) | Qt.AlignVCenter,
                       fm.elidedText(self.text(), Qt.ElideRight, self.width()))

    class _Chip(QLabel):
        """A goal chip that gives way when the goals row runs out of room.

        Level 2 has four goals beside Undo · Reset · Random · Try every block, and on Linux's
        fonts that row asked for 949 px of a 940 px window. A chip keeps its full text whenever
        there is room (every platform at 940 px so far); when there is not, it elides the end
        and the tooltip, which already carries the whole goal, says the rest.
        """

        def __init__(self, text: str) -> None:
            super().__init__(text)
            self._full = text
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        def minimumSizeHint(self) -> QSize:
            full = super().minimumSizeHint()
            return QSize(min(full.width(), 72), full.height())

        def sizeHint(self) -> QSize:
            return QLabel(self._full, styleSheet=self.styleSheet()).sizeHint()

        def resizeEvent(self, e) -> None:
            super().resizeEvent(e)
            room = self.contentsRect().width()
            fm = QFontMetrics(self.font())
            text = (self._full if fm.horizontalAdvance(self._full) <= room
                    else fm.elidedText(self._full, Qt.ElideRight, room))
            if text != self.text():
                self.setText(text)

    def _wrap(text: str = "", css: str = "") -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if css:
            lbl.setStyleSheet(_scss(css))
        return lbl

    # ----------------------------------------------------------------------- the disk map
    class _DiskMap(QWidget):
        """Every block of the disk as a numbered square, 20 per row, coloured by what it holds.
        A read rings it blue with `R3`, a write orange with `W5`, both with `3·4`; the selected
        block has a dark outline. Pointing says what the block holds (`on_hover`); clicking
        reports the block (`on_click`)."""

        GAP, CH, LEG = 2, 27, 30

        def __init__(self, ink: _Ink, on_click, on_hover) -> None:
            super().__init__()
            self._ink = ink
            self._on_click = on_click
            self._on_hover = on_hover
            self._fs = None
            self._n = 80
            self._marks: dict = {}
            self._sel = None
            self._kind = None
            self._hover = None
            self.setMouseTracking(True)
            self.setCursor(Qt.PointingHandCursor)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMinimumWidth(360)

        def set_disk(self, fs, n: int, marks: dict, sel=None, kind=None) -> None:
            self._fs, self._n, self._marks, self._sel, self._kind = fs, n, marks, sel, kind
            rows = -(-n // 20)
            self.setFixedHeight(rows * (self.CH + self.GAP) + self.LEG)
            # And the card around it. A level's page is laid out while it is hidden in the
            # stack, and coming back to level 4 from levels 1–3 left its map card squeezed to
            # 14 px — below its own minimum — with the trace card holding the spare height. The
            # map is what a student clicks on that level, so its card's height is pinned.
            card = self.parentWidget()
            lay = card.layout() if card is not None else None
            if lay is not None and lay.count() == 1:
                m = lay.contentsMargins()
                card.setFixedHeight(self.height() + m.top() + m.bottom())
            self.update()

        def marks(self) -> dict:
            return dict(self._marks)

        def blocks(self) -> int:
            return self._n

        def _cw(self) -> float:
            return max(14.0, (self.width() - 19 * self.GAP) / 20)

        def cell_rect(self, b: int) -> QRectF:
            cw = self._cw()
            return QRectF((b % 20) * (cw + self.GAP), (b // 20) * (self.CH + self.GAP), cw,
                          self.CH)

        def block_at(self, x: float, y: float):
            cw = self._cw()
            col, row = int(x // (cw + self.GAP)), int(y // (self.CH + self.GAP))
            if col < 0 or col >= 20 or row < 0:
                return None
            b = row * 20 + col
            if b >= self._n or not self.cell_rect(b).contains(QPointF(x, y)):
                return None
            return b

        def kind(self, b: int) -> str:
            k = self._kind(b) if self._kind else None
            return k or kind_of(self._fs, b)

        def paintEvent(self, _e) -> None:
            if self._fs is None:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            num_f, badge_f = _mono(10), _mono(8.5, bold=True)
            for b in range(self._n):
                r = self.cell_rect(b)
                kind = self.kind(b)
                p.setBrush(QColor(k.kinds.get(kind, k.panel)))
                pen = QPen(QColor(k.super_line if kind == "super" else k.line2), 1)
                if kind == "free":
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                m = self._marks.get(b)
                if m is not None:
                    p.setBrush(Qt.NoBrush)
                    if m.r:
                        p.setPen(QPen(QColor(k.rd), 2))
                        p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 3, 3)
                    if m.w:
                        p.setPen(QPen(QColor(k.wr), 2))
                        inset = 3.5 if m.r else 1.5
                        p.drawRoundedRect(r.adjusted(inset, inset, -inset, -inset), 3, 3)
                if self._sel == b or self._hover == b:
                    pen = QPen(QColor(k.text), 2 if self._sel == b else 1)
                    if self._sel != b:
                        pen.setStyle(Qt.DotLine)
                    p.setPen(pen)
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 4, 4)
                p.setFont(num_f)
                p.setPen(QColor(k.muted if kind in ("other", "free") else k.text))
                top = QRectF(r.x(), r.y() + 1, r.width(), 13 if m else r.height() - 2)
                p.drawText(top, Qt.AlignCenter, str(b))
                if m is not None:
                    p.setFont(badge_f)
                    p.setPen(QColor(k.text if (m.r and m.w) else k.rd if m.r else k.wr))
                    p.drawText(QRectF(r.x() - 2, r.y() + 13, r.width() + 4, 13), Qt.AlignCenter,
                               m.txt)
            # the legend, wrapping
            y0 = -(-self._n // 20) * (self.CH + self.GAP) + 4
            x, y = 2.0, float(y0)
            f = _sans(10.5)
            fm = QFontMetrics(f)
            p.setFont(f)
            items = [(kk, t) for kk, t in LEGEND] + [("@r", "read"), ("@w", "written")]
            for kk, t in items:
                wd = 15 + fm.horizontalAdvance(t) + 10
                if x + wd > self.width() and x > 2:
                    x, y = 2.0, y + 13
                sw = QRectF(x, y + 1, 10, 10)
                if kk.startswith("@"):
                    p.setBrush(QColor(k.panel))
                    p.setPen(QPen(QColor(k.rd if kk == "@r" else k.wr), 2))
                else:
                    p.setBrush(QColor(k.kinds[kk]))
                    pen = QPen(QColor(k.line2), 1)
                    if kk == "free":
                        pen.setStyle(Qt.DashLine)
                    p.setPen(pen)
                p.drawRoundedRect(sw, 2, 2)
                p.setPen(QColor(k.muted))
                p.drawText(QPointF(x + 14, y + 10), t)
                x += wd

        def mouseMoveEvent(self, e) -> None:
            b = self.block_at(e.position().x(), e.position().y())
            if b != self._hover:
                self._hover = b
                if b is not None:
                    m = self._marks.get(b)
                    self.setToolTip(f"Block {b}: {F.label(b)}" + (f", {m.txt}" if m else ""))
                self._on_hover(b)
                self.update()

        def leaveEvent(self, _e) -> None:
            self._hover = None
            self._on_hover(None)
            self.update()

        def mousePressEvent(self, e) -> None:
            b = self.block_at(e.position().x(), e.position().y())
            if b is not None and e.button() == Qt.LeftButton:
                self._on_click(b)

    # ----------------------------------------------------------------------- pointers
    class _PointerView(QWidget):
        """The open file's i-node, drawn in memory with its 12 direct pointers and the single,
        double and triple pointers, and each index block that exists as a 16 × 16 grid of its
        256 entries. Laid out on a 590 × 300 plan (the mockup's) and scaled down to fit. In level
        2 the student clicks the i-node's pointer, then an entry in each index block the kernel
        reads; level 3 draws the same thing read-only."""

        W, H = 590.0, 300.0
        PITCH, CELL, GW = 8, 7, 128
        C2, C3, GY = 214, 400, (24, 168)

        def __init__(self, ink: _Ink, on_slot=None, on_entry=None) -> None:
            super().__init__()
            self._ink = ink
            self._on_slot = on_slot
            self._on_entry = on_entry
            self._fs = None
            self._inum = 0
            self._hops: list = []
            self._stage = 0
            self._picks: tuple = ()
            self._click = False
            self._regions: list = []
            self.setMouseTracking(True)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        def sizeHint(self) -> QSize:
            return QSize(int(self.W), int(self.H))

        def minimumSizeHint(self) -> QSize:
            return QSize(300, 200)

        def set_view(self, fs, inum: int, hops=(), stage: int = 0, picks=(),
                     click: bool = False) -> None:
            self._fs, self._inum = fs, inum
            self._hops, self._stage, self._picks, self._click = list(hops or []), stage, \
                tuple(picks), click
            self.update()

        def _scale(self) -> float:
            return min(1.0, self.width() / self.W, self.height() / self.H)

        def regions(self) -> list:
            """(rect on the plan, kind, value) for everything that answers a click or a hover:
            ("slot", name), ("entry", (block, entry)) and ("cell", (block, name, value))."""
            return list(self._regions)

        # -- the plan --------------------------------------------------------- #
        def _on_path(self, b) -> bool:
            return any(h[0] == b for h in self._hops[1:self._stage + 1])

        def _cur(self):
            if self._hops and 0 < self._stage < len(self._hops):
                return self._hops[self._stage][0]
            return None

        def _pick_in(self, b):
            for k in range(1, len(self._hops)):
                if self._hops[k][0] == b and len(self._picks) > k:
                    return self._picks[k]
            return None

        def paintEvent(self, _e) -> None:
            if self._fs is None:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            s = self._scale()
            p.scale(s, s)
            self._regions = []
            k = self._ink
            fs, ino = self._fs, self._fs.inodes[self._inum]
            ix, iy, iw = 6, 6, 160
            on0 = self._stage == 0 and self._click
            p.setBrush(QColor(k.panel2))
            p.setPen(QPen(QColor(k.accent if on0 else k.line2), 2 if on0 else 1))
            p.drawRoundedRect(QRectF(ix, iy, iw, self.H - 14), 8, 8)
            p.setPen(QColor(k.text))
            p.setFont(_sans(11.5, True))
            p.drawText(QPointF(ix + 10, iy + 18), f"i-node {self._inum} · in memory")
            p.setFont(_sans(11))
            p.drawText(QPointF(ix + 10, iy + 34), f"size {num(ino.size)} bytes")
            p.setPen(QColor(k.muted))
            p.setFont(_sans(9.5, True))
            p.drawText(QPointF(ix + 10, iy + 52), "DIRECT · FILE BLOCKS 0–11")
            pick_top = self._picks[0] if self._picks else None

            def slot(x, y, wd, name, val, lab):
                on = pick_top == name
                r = QRectF(x, y, wd, 19)
                fill = (k.kinds["data"] if name.startswith("direct") else k.kinds["index"]) \
                    if val else k.panel
                p.setBrush(QColor(fill))
                p.setPen(QPen(QColor(k.accent if on else k.line2), 2 if on else 1))
                p.drawRoundedRect(r, 4, 4)
                p.setFont(_mono(10))
                p.setPen(QColor(k.text2))
                p.drawText(QPointF(x + 5, y + 13), lab)
                p.setFont(_mono(10.5, True))
                p.setPen(QColor(k.text if val else k.muted))
                p.drawText(QRectF(x, y, wd - 5, 19), Qt.AlignRight | Qt.AlignVCenter,
                           str(val or 0))
                self._regions.append((r, "slot", name))
                self._regions.append((r, "tip", f"{name} = {val or 0}"))

            for i in range(F.NDIRECT):
                slot(ix + 8 + (i % 2) * 74, iy + 58 + (i // 2) * 22, 70, f"direct[{i}]",
                     ino.direct[i], f"d[{i}]")
            ys = {"single": iy + 200, "double": iy + 228, "triple": iy + 256}
            p.setPen(QColor(k.muted))
            p.setFont(_sans(9.5, True))
            p.drawText(QPointF(ix + 10, iy + 194), "INDIRECT")
            for t in ("single", "double", "triple"):
                slot(ix + 8, ys[t], 144, t, ino.top(t), t)
            cur = self._cur()
            c2, c3, gy = self.C2, self.C3, self.GY

            def grid(x, y, b, key, leaf, title):
                self._grid(p, x, y, b, key, leaf, title, on=(b == cur or self._on_path(b)),
                           pick=self._pick_in(b), clickable=self._click and b == cur)

            def empty(x, y, txt):
                pen = QPen(QColor(k.line2), 1)
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(x, y, self.GW, self.GW), 5, 5)
                p.setPen(QColor(k.muted))
                p.setFont(_sans(11))
                p.drawText(QRectF(x, y, self.GW, self.GW), Qt.AlignCenter | Qt.TextWordWrap,
                           txt)

            def label_(x, y, txt):
                p.setPen(QColor(k.muted))
                p.setFont(_sans(10.5, True))
                p.drawText(QPointF(x, y), txt)

            if ino.single:
                grid(c2, gy[0], ino.single, ("single", ()), True,
                     f"single-indirect · block {ino.single}")
                self._arrow(p, ix + 152, ys["single"] + 9, c2 - 4, gy[0] + 40,
                            self._on_path(ino.single))
            else:
                label_(c2, gy[0] - 6, "single = 0")
                empty(c2, gy[0], "no single-indirect block")
            extras = []
            if ino.double:
                dptr = fs.blocks[ino.double].ptrs
                for a in sorted(dptr):
                    extras.append({"b": dptr[a], "key": ("double", (a,)), "leaf": True,
                                       "title": f"under double[{a}] · block {dptr[a]}"})
            if ino.triple:
                extras.append({"b": ino.triple, "key": ("triple", ()), "leaf": False,
                                   "title": f"triple-indirect · block {ino.triple}"})
                tptr = fs.blocks[ino.triple].ptrs
                for a in sorted(tptr):
                    d = tptr[a]
                    extras.append({"b": d, "key": ("triple", (a,)), "leaf": False,
                                       "title": f"under triple[{a}] · block {d}"})
                    for bb in sorted(fs.blocks[d].ptrs):
                        c = fs.blocks[d].ptrs[bb]
                        extras.append({"b": c, "key": ("triple", (a, bb)), "leaf": True,
                                           "title": f"under triple[{a}][{bb}] · block {c}"})
            if ino.double:
                grid(c2, gy[1], ino.double, ("double", ()), False,
                     f"double-indirect · block {ino.double}")
                self._arrow(p, ix + 152, ys["double"] + 9, c2 - 4, gy[1] + 40,
                            self._on_path(ino.double))
            elif not ino.triple:
                label_(c2, gy[1] - 6, "double = 0")
                empty(c2, gy[1], "no double-indirect block")
            shown = [e for e in extras if self._on_path(e["b"]) or e["b"] == cur]
            for e in extras:
                if len(shown) < 2 and e not in shown:
                    shown.append(e)
            shown = sorted(shown[:2], key=extras.index)
            triple_only = not ino.double and ino.triple and shown
            if triple_only:
                t0 = extras[0]
                grid(c2, gy[1], t0["b"], t0["key"], False, t0["title"])
                self._arrow(p, ix + 152, ys["triple"] + 9, c2 - 4, gy[1] + 40,
                            self._on_path(t0["b"]))
                rest = extras[1:]
                cand = [e for e in rest if self._on_path(e["b"]) or e["b"] == cur] + rest
                seen: list = []
                for e in cand:
                    if e not in seen:
                        seen.append(e)
                shown = seen[:2]
            for i, e in enumerate(shown):
                grid(c3, gy[i], e["b"], e["key"], e["leaf"], e["title"])
                pre = e["key"][1]
                a = pre[-1]
                frm = (ino.double if e["key"][0] == "double" else ino.triple) \
                    if len(pre) == 1 else None
                if frm and (e["key"][0] == "double" or not ino.double):
                    fy = gy[1] + (a // 16) * self.PITCH + 3
                    fx = c2 + (a % 16) * self.PITCH + 7
                    self._arrow(p, fx, fy, c3 - 4, gy[i] + 40, self._on_path(e["b"]))
            more = len(extras) - len(shown) - (1 if (not ino.double and ino.triple) else 0)
            if more > 0:
                p.setPen(QColor(k.muted))
                p.setFont(_sans(10))
                p.drawText(QPointF(c3, self.H - 4),
                           f"+ {more} more index block{'s' if more > 1 else ''} not drawn")

        def _arrow(self, p, x1, y1, x2, y2, on: bool) -> None:
            k = self._ink
            col = QColor(k.accent if on else k.line2)
            path = QPainterPath(QPointF(x1, y1))
            path.cubicTo(QPointF(x1 + 26, y1), QPointF(x2 - 26, y2), QPointF(x2, y2))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 2 if on else 1.2))
            p.drawPath(path)
            head = QPainterPath(QPointF(x2, y2))
            head.lineTo(x2 - 6, y2 - 3.5)
            head.lineTo(x2 - 6, y2 + 3.5)
            head.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPath(head)

        def _grid(self, p, x, y, b, key, leaf, title, on, pick, clickable) -> None:
            k = self._ink
            bl = self._fs.blocks.get(b)
            p.setPen(QColor(k.text2))
            p.setFont(_sans(10.5, True))
            p.drawText(QPointF(x, y - 6), title)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(k.line2), 0.8))
            p.drawRoundedRect(QRectF(x - 1.5, y - 1.5, self.GW + 2, self.GW + 2), 3, 3)
            if on:
                p.setPen(QPen(QColor(k.accent), 2))
                p.drawRoundedRect(QRectF(x - 3, y - 3, self.GW + 5, self.GW + 5), 4, 4)
            p.setPen(QColor(k.muted))
            p.setFont(_sans(7.5))
            for r in (0, 4, 8, 12):
                p.drawText(QRectF(x - 24, y + r * self.PITCH - 1, 21, 9),
                           Qt.AlignRight | Qt.AlignVCenter, str(r * 16))
            top, pre = key
            for e in range(F.NPTR):
                cx = x + (e % 16) * self.PITCH
                cy = y + (e // 16) * self.PITCH
                v = bl.ptrs.get(e, 0) if bl is not None else 0
                fill = (k.p_data if leaf else k.p_index) if v else k.panel2
                p.setBrush(QColor(fill))
                if pick == e:
                    p.setPen(QPen(QColor(k.accent), 2))
                elif v:
                    p.setPen(QPen(QColor(k.line2), 0.6))
                else:
                    p.setPen(QPen(QColor(k.line), 0.5))
                r = QRectF(cx, cy, self.CELL, self.CELL)
                p.drawRoundedRect(r, 1, 1)
                nm = top + "".join(f"[{z}]" for z in pre) + f"[{e}]"
                self._regions.append((r, "tip", f"{nm} → {v}"))
                if clickable:
                    self._regions.append((r.adjusted(-0.5, -0.5, 0.5, 0.5), "entry", (b, e)))

        def _hit(self, pos, kinds):
            s = self._scale() or 1.0
            pt = QPointF(pos.x() / s, pos.y() / s)
            for r, kind, val in reversed(self._regions):
                if kind in kinds and r.contains(pt):
                    return kind, val
            return None

        def mouseMoveEvent(self, e) -> None:
            h = self._hit(e.position(), ("tip",))
            clickable = self._hit(e.position(), ("slot", "entry")) if self._click else None
            self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
            if h:
                QToolTip.showText(e.globalPosition().toPoint(), h[1], self)
            else:
                QToolTip.hideText()

        def mousePressEvent(self, e) -> None:
            if not self._click or e.button() != Qt.LeftButton:
                return
            h = self._hit(e.position(), ("slot", "entry"))
            if h is None:
                return
            if h[0] == "slot" and self._on_slot:
                self._on_slot(h[1])
            elif h[0] == "entry" and self._on_entry:
                self._on_entry(*h[1])

    # ----------------------------------------------------------------------- level 4's strip
    class _FileStrip(QWidget):
        """The file blocks around the write: blocks that exist filled to how much of them holds
        data, missing ones dashed, the bytes this write covers shaded, the pointer under each."""

        CW, GAP, H = 36, 3, 50

        def __init__(self, ink: _Ink) -> None:
            super().__init__()
            self._ink = ink
            self._cells: tuple = ()
            self.setFixedHeight(self.H)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        def set_cells(self, cells, aria: str = "") -> None:
            self._cells = tuple(cells)
            self.setToolTip(aria)
            self.update()

        def cells(self) -> tuple:
            return self._cells

        def paintEvent(self, _e) -> None:
            if not self._cells:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            wd = len(self._cells) * (self.CW + self.GAP)
            s = min(1.0, self.width() / wd) if wd else 1.0
            p.scale(s, s)
            cw = self.CW
            for i, c in enumerate(self._cells):
                x = i * (cw + self.GAP)
                if c.fb is None:
                    p.setPen(QColor(k.muted))
                    p.setFont(_sans(12))
                    p.drawText(QRectF(x, 10, cw, 26), Qt.AlignCenter, "…")
                    continue
                pen = QPen(QColor(k.line2 if c.exists else k.muted), 1)
                if not c.exists:
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.setBrush(QColor(k.panel))
                p.drawRoundedRect(QRectF(x, 10, cw, 26), 4, 4)
                if c.used:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(k.kinds["data"]))
                    p.drawRoundedRect(QRectF(x + 1, 11, (cw - 2) * c.used / BS, 24), 3, 3)
                if c.cover:
                    a, b = c.cover
                    col = QColor(k.accent)
                    col.setAlphaF(0.28)
                    p.setPen(Qt.NoPen)
                    p.setBrush(col)
                    p.drawRect(QRectF(x + 1 + (cw - 2) * a, 11, (cw - 2) * (b - a), 24))
                p.setPen(QColor(k.text))
                p.setFont(_mono(10.5))
                p.drawText(QRectF(x, 10, cw, 26), Qt.AlignCenter, str(c.fb))
                p.setPen(QColor(k.muted))
                p.setFont(_sans(8.5))
                p.drawText(QRectF(x - 4, 37, cw + 8, 13), Qt.AlignHCenter | Qt.AlignTop,
                           c.pointer)

    # ----------------------------------------------------------------------- Every block grids
    class _BandsView(QWidget):
        """Every file block as a small square, one grid per range (the direct range, the single
        range, each double[a]); each square's words are its tooltip."""

        WD = 276

        def __init__(self, ink: _Ink) -> None:
            super().__init__()
            self._ink = ink
            self._bands: list = []
            self._regions: list = []
            self.setMouseTracking(True)
            self.setFixedWidth(self.WD)

        def set_bands(self, bands_) -> None:
            """bands_: [(title, [(fill, tip), ...]), ...]."""
            self._bands = list(bands_)
            y, col = 14, 0
            for i, _b in enumerate(self._bands):
                if i == 0:
                    y += 3 * 18
                    continue
                if col == 1:
                    y += 16 * 7 + 22
                    col = 0
                else:
                    col = 1
            self.setFixedHeight(y + (16 * 7 + 22 if col else 0) + 6)
            self.update()

        def cell_count(self) -> int:
            return sum(len(c) for _t, c in self._bands)

        def paintEvent(self, _e) -> None:
            p = QPainter(self)
            k = self._ink
            self._regions = []
            y, col = 14, 0
            for i, (title, cells) in enumerate(self._bands):
                if i == 0:
                    self._mini(p, 4, y, cells, 18, title)
                    y += 3 * 18
                    continue
                x = 4 + col * 136
                self._mini(p, x, y + 10, cells, 7, title)
                if col == 1:
                    y += 16 * 7 + 22
                    col = 0
                else:
                    col = 1
            del k

        def _mini(self, p, x, y, cells, pitch, title) -> None:
            k = self._ink
            p.setPen(QColor(k.text2))
            p.setFont(_sans(10, True))
            p.drawText(QPointF(x, y - 4), title)
            p.setPen(QPen(QColor(k.line), 0.4))
            for e, (fill, tip) in enumerate(cells):
                r = QRectF(x + (e % 16) * pitch, y + (e // 16) * pitch, pitch - 1, pitch - 1)
                p.setBrush(QColor(fill))
                p.drawRect(r)
                self._regions.append((r, tip))

        def mouseMoveEvent(self, e) -> None:
            pt = e.position()
            for r, tip in self._regions:
                if r.contains(pt):
                    QToolTip.showText(e.globalPosition().toPoint(), tip, self)
                    return
            QToolTip.hideText()

    # ----------------------------------------------------------------------- Every length chart
    class _StepChart(QWidget):
        """R and W against the number of bytes written, as two step lines."""

        WD, HT, PL, PB, PT = 276, 150, 26, 22, 8

        def __init__(self, ink: _Ink) -> None:
            super().__init__()
            self._ink = ink
            self._ev = None
            self.setFixedSize(self.WD, self.HT)

        def set_every(self, ev: dict) -> None:
            self._ev = ev
            self.setToolTip("Reads (R) and writes (W) against the number of bytes")
            self.update()

        def paintEvent(self, _e) -> None:
            ev = self._ev
            if not ev:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            wd, ht, pl, pb, pt = self.WD, self.HT, self.PL, self.PB, self.PT
            max_y = max(max(r, w) for _n, r, w in ev["seq"]) + 1
            upto = ev["upto"]

            def X(n):
                return pl + (n - 1) / max(1, upto - 1) * (wd - pl - 8)

            def Y(v):
                return pt + (1 - v / max_y) * (ht - pt - pb)
            p.setFont(_sans(9))
            step = 2 if max_y > 8 else 1
            for v in range(0, max_y + 1, step):
                p.setPen(QPen(QColor(k.line), 0.6))
                p.drawLine(QPointF(pl, Y(v)), QPointF(wd - 8, Y(v)))
                p.setPen(QColor(k.muted))
                p.drawText(QRectF(0, Y(v) - 6, pl - 4, 12), Qt.AlignRight | Qt.AlignVCenter,
                           str(v))
            for n, al in ((1, Qt.AlignLeft), (round(upto / 2), Qt.AlignHCenter),
                          (upto, Qt.AlignRight)):
                x = X(n)
                box = (QRectF(x - 4, ht - 20, 60, 12) if al == Qt.AlignLeft else
                       QRectF(x - 60, ht - 20, 64, 12) if al == Qt.AlignRight else
                       QRectF(x - 30, ht - 20, 60, 12))
                p.drawText(box, al | Qt.AlignVCenter, num(n))
            p.drawText(QRectF(wd - 60, ht - 9, 56, 9), Qt.AlignRight | Qt.AlignVCenter, "bytes")
            for idx, col in ((2, k.rd), (3, k.wr)):
                path = QPainterPath()
                for j, iv in enumerate(ev["iv"]):
                    a, b, v = iv[0], iv[1], iv[idx]
                    if j == 0:
                        path.moveTo(X(a), Y(v))
                    else:
                        path.lineTo(X(a), Y(v))
                    path.lineTo(X(b), Y(v))
                p.setPen(QPen(QColor(col), 2))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)

    # ----------------------------------------------------------------------- the table rows
    class _PathRow(QFrame):
        """A row of the tutorial's table: the name, then i-node · i-node block · data, then the
        contents. Clicking it opens the name (level 1); on level 4 it is read-only."""

        def __init__(self, ink: _Ink, path: str, meta: str, contents: str, pressed: bool,
                     on_click=None) -> None:
            super().__init__()
            self.path = path
            self._on_click = on_click
            k = ink
            self.setObjectName("prow")
            self.setStyleSheet(
                f"QFrame#prow{{background:{k.accent_soft if pressed else k.panel};"
                f"border:1px solid {k.accent if pressed else k.line};border-radius:7px;}}")
            v = QVBoxLayout(self)
            v.setContentsMargins(8, 5, 8, 5)
            v.setSpacing(1)
            self.p = _wrap(_esc(path), f"{MONO_CSS}font-weight:600;font-size:12px;"
                                       f"color:{k.text};background:transparent;border:none;")
            self.m = _wrap(_esc(meta), f"font-size:11px;color:{k.muted};background:transparent;"
                                       "border:none;")
            self.c = _wrap(_esc(contents), f"{MONO_CSS}font-size:11px;color:{k.text2};"
                                           "background:transparent;border:none;")
            for w in (self.p, self.m, self.c):
                v.addWidget(w)
            if on_click is not None:
                self.setCursor(Qt.PointingHandCursor)
                self.setToolTip(f"Open {path}")

        def click(self) -> None:
            if self._on_click is not None:
                self._on_click(self.path)

        def mouseReleaseEvent(self, e) -> None:
            if e.button() == Qt.LeftButton:
                self.click()

    # ----------------------------------------------------------------------- the window
    class FsLab(QDialog):
        """One window: the header with the level buttons, the goals and Undo · Reset · Random ·
        the level's Try every; a starts row and the level's page on the left; the side panel
        with the level's tabs on the right.

        It polls nothing. Every control goes through `Session` and then `_refresh`, which
        redraws the face from the session. Watch is a `QTimer` that calls Show me every 550 ms;
        any other control stops it.
        """

        def __init__(self, parent, theme: ThemeManager, lab: F.LevelFile | None = None,
                     device=None, on_event=None) -> None:
            super().__init__(parent)
            self.theme = theme
            self.device = device
            lab = lab or F.get("t-lab-04")
            if lab is None:
                raise F.LevelError("the level file t-lab-04 is not shipped with this gini-core")
            if not lab.levels:
                raise F.LevelError(f"{lab.title} has no levels yet")
            self.session = Session(lab, on_event=on_event)
            t = theme.theme
            self._k = _ink(t)
            title = lab.title + (f" — {device.name}" if getattr(device, "name", "") else "")
            standalone(self, title)
            self.resize(940, 700)
            self.setMinimumSize(760, 560)
            self.setStyleSheet(_scss(
                f"QDialog{{background:{t.bg};}} QLabel{{color:{t.text};}}"
                f"QToolTip{{color:{t.text};background:{t.panel};border:1px solid {t.line};}}"))
            self._watch = QTimer(self)
            self._watch.setInterval(550)
            self._watch.timeout.connect(self._watch_tick)
            self._hovering = None
            root = QVBoxLayout(self)
            root.setContentsMargins(14, 12, 14, 12)
            root.setSpacing(8)
            self._build_header(root)
            self._build_goals(root)
            body = QHBoxLayout()
            body.setSpacing(8)
            root.addLayout(body, 1)
            left = QVBoxLayout()
            left.setSpacing(8)
            body.addLayout(left, 1)
            self._build_starts(left)
            self._stack = QStackedWidget()
            left.addWidget(self._stack, 1)
            self._build_walk()
            self._build_byte()
            self._build_make()
            self._build_append()
            self._build_aside(body)
            self._refresh()

        # -- styles ---------------------------------------------------------- #
        def _css(self, kind: str = "plain") -> str:
            k = self._k
            if kind == "prime":
                return _scss(f"QPushButton{{background:{k.accent};color:#ffffff;border:1px solid "
                             f"{k.accent};border-radius:7px;padding:0 12px;font-size:13px;"
                             f"font-weight:600;min-height:30px;}}"
                             f"QPushButton:disabled{{background:{k.panel2};color:{k.faint};"
                             f"border-color:{k.line};}}")
            if kind == "small":
                return _scss(f"QPushButton{{background:{k.panel};color:{k.accent_ink};border:1px "
                             f"solid {k.accent};border-radius:6px;padding:0 8px;font-size:12px;"
                             f"min-height:24px;}}"
                             f"QPushButton:disabled{{color:{k.faint};border-color:{k.line};}}")
            if kind == "pre":
                return _scss(f"QPushButton{{background:{k.panel2};color:{k.text};border:1px "
                             f"solid {k.line};border-radius:5px;padding:0 7px;font-size:11px;"
                             f"min-height:22px;{MONO_CSS}}}"
                             f"QPushButton:checked{{background:{k.accent_soft};color:"
                             f"{k.accent_ink};border-color:{k.accent};font-weight:600;}}")
            if kind == "st":
                return _scss(f"QPushButton{{background:{k.panel};color:{k.text};border:1px "
                             f"solid {k.line2};border-radius:6px;padding:0 9px;font-size:12px;"
                             f"min-height:26px;}}"
                             f"QPushButton:checked{{background:{k.accent_soft};color:"
                             f"{k.accent_ink};border-color:{k.accent};font-weight:700;}}")
            return _scss(f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid "
                         f"{k.line2};border-radius:7px;padding:0 10px;font-size:13px;"
                         f"min-height:30px;}}"
                         f"QPushButton:hover{{border-color:{k.accent};}}"
                         f"QPushButton:disabled{{color:{k.faint};}}")

        def _card(self, name: str = "card") -> QFrame:
            k = self._k
            f = QFrame()
            f.setObjectName(name)
            f.setStyleSheet(f"QFrame#{name}{{background:{k.panel};border:1px solid {k.line};"
                            f"border-radius:10px;}}")
            return f

        def _cap(self, text: str) -> QLabel:
            lbl = QLabel(text.upper())
            lbl.setStyleSheet(_scss(f"font-size:11px;font-weight:700;letter-spacing:1px;"
                                    f"color:{self._k.muted};"))
            return lbl

        def _mono_lbl(self, text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(_scss(f"{MONO_CSS}font-size:12px;color:{self._k.text};"))
            return lbl

        def _edit(self, width: int = 92) -> QLineEdit:
            k = self._k
            e = QLineEdit()
            e.setFixedWidth(width)
            e.setAlignment(Qt.AlignRight)
            e.setStyleSheet(_scss(f"QLineEdit{{{MONO_CSS}font-size:12px;background:{k.panel2};"
                                  f"color:{k.text};border:1px solid {k.line2};border-radius:6px;"
                                  f"padding:2px 6px;}}"))
            return e

        def _combo(self) -> QComboBox:
            k = self._k
            c = QComboBox()
            c.setStyleSheet(_scss(f"QComboBox{{{MONO_CSS}font-size:12px;background:{k.panel2};"
                                  f"color:{k.text};border:1px solid {k.line2};border-radius:6px;"
                                  f"padding:2px 6px;}}"))
            return c

        def _set_banner(self, lbl: QLabel, kind: str, text: str, head: str = "") -> None:
            """A line under a call: red for a refusal, green for a step done, grey for news,
            and the accent "NEXT" / "GINI ASKS" callout otherwise."""
            k = self._k
            bg, fg, line = {"bad": (k.bad_bg, k.bad_fg, k.bad),
                            "ok": (k.ok_bg, k.ok_fg, k.ok),
                            "info": (k.panel2, k.text2, k.line)}.get(kind, (None, None, None))
            lbl.setProperty("kind", kind)
            if bg is None:
                lbl.setText(f"<span style='color:{k.accent_ink};font-size:11px;"
                            f"font-weight:700'>{_esc(head.upper())}</span>&nbsp;&nbsp;"
                            f"{_esc(text)}")
                lbl.setStyleSheet(_scss(f"font-size:12px;color:{k.text};padding:4px 2px;"))
            else:
                lbl.setText(_esc(text))
                lbl.setStyleSheet(_scss(f"font-size:12px;color:{fg};background:{bg};"
                                        f"border:1px solid {line};border-radius:7px;"
                                        f"padding:4px 8px;"))
            lbl.setVisible(bool(text))

        def _table_html(self, heads, rows, widths=()) -> str:
            """A trace table in the tutorial's format, as rich text. Each row is (cells,
            kind): a hit row is grey italic, the pending row is shaded, R and W cells are
            blue and orange."""
            k = self._k
            th = "".join(f"<td style='color:{k.muted};font-size:10px;font-weight:700'"
                         + (f" width='{widths[i]}'" if i < len(widths) and widths[i] else "")
                         + f">{_esc(h.upper())}</td>" for i, h in enumerate(heads))
            out = [f"<table width='100%' cellspacing='0' cellpadding='3'><tr>{th}</tr>"]
            for cells, kind in rows:
                bg = f" bgcolor='{k.accent_soft}'" if kind == "pend" else ""
                tds = []
                for i, c in enumerate(cells):
                    style = f"color:{k.text};font-size:12px;"
                    if kind == "hit":
                        style = f"color:{k.muted};font-style:italic;font-size:12px;"
                    if i in (1,) or (isinstance(c, tuple)):
                        style += MONO_CSS
                    if isinstance(c, tuple):        # (text, "R"|"W")
                        c, op = c
                        style += f"font-weight:700;color:{k.rd if op == 'R' else k.wr};"
                    tds.append(f"<td style=\"{style}\">{_esc(str(c))}</td>")
                out.append(f"<tr{bg}>{''.join(tds)}</tr>")
            out.append("</table>")
            return "".join(out)

        def _scroll(self, inner: QWidget) -> QScrollArea:
            k = self._k
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sc.setStyleSheet(f"QScrollArea{{border:none;background:{k.panel};}}"
                             f"QScrollArea > QWidget > QWidget{{background:{k.panel};}}")
            sc.setWidget(inner)
            return sc

        @staticmethod
        def _stick_to_bottom(sc: QScrollArea) -> None:
            """A trace grows downward and the row being built is the last one: when the table
            grows, show its end. Scrolling up by hand still works, because only a change in
            the table's height moves the view."""
            bar = sc.verticalScrollBar()
            bar.rangeChanged.connect(lambda _lo, hi, bar=bar: bar.setValue(hi))

        # -- construction: chrome --------------------------------------------- #
        def _build_header(self, root) -> None:
            k = self._k
            row = QHBoxLayout()
            row.setSpacing(10)
            col = QVBoxLayout()
            col.setSpacing(0)
            cap = QLabel(self.session.lab.title.replace(" — ", " · ").upper())
            cap.setStyleSheet(_scss(f"font-size:11px;font-weight:700;letter-spacing:1px;"
                                    f"color:{k.accent_ink};"))
            trow = QHBoxLayout()
            trow.setSpacing(8)
            self._level_title = QLabel("")
            self._level_title.setStyleSheet(_scss(f"font-size:17px;font-weight:700;"
                                                  f"color:{k.text};"))
            self._level_source = _ElideLabel("")
            self._level_source.setStyleSheet(_scss(f"font-size:12px;color:{k.muted};"))
            trow.addWidget(self._level_title)
            trow.addWidget(self._level_source, 1)
            col.addWidget(cap)
            col.addLayout(trow)
            row.addLayout(col, 1)
            self._level_btns = []
            for i, lv in enumerate(self.session.lab.levels):
                b = QPushButton(str(lv.n))
                b.setCheckable(True)
                b.setFixedSize(34, 34)
                b.setToolTip(f"Level {lv.n}: {lv.title}")
                b.setStyleSheet(_scss(
                    f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid "
                    f"{k.line2};border-radius:7px;font-size:14px;font-weight:700;}}"
                    f"QPushButton:checked{{background:{k.ink};color:{k.ink_fg};"
                    f"border-color:{k.ink};}}"))
                b.clicked.connect(lambda _c=False, i=i: self._pick_level(i))
                row.addWidget(b)
                self._level_btns.append(b)
            root.addLayout(row)

        def _build_goals(self, root) -> None:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(self._cap("Goals"))
            self._chips_row = QHBoxLayout()
            self._chips_row.setSpacing(5)
            row.addLayout(self._chips_row)
            row.addStretch(1)
            self._undo = QPushButton("Undo")
            self._reset = QPushButton("Reset")
            self._random = QPushButton("Random")
            self._every = QPushButton("Try every")
            for b in (self._undo, self._reset, self._random):
                b.setStyleSheet(self._css())
                row.addWidget(b)
            self._every.setStyleSheet(self._css("prime"))
            row.addWidget(self._every)
            self._undo.clicked.connect(self._on_undo)
            self._reset.clicked.connect(self._on_reset)
            self._random.clicked.connect(self._on_random)
            self._every.clicked.connect(self._on_every)
            self._chips: dict = {}
            root.addLayout(row)

        def _build_starts(self, left) -> None:
            self._starts_box = QWidget()
            row = QHBoxLayout(self._starts_box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(self._cap("Start"))
            self._starts_row = row
            self._start_btns: list = []
            row.addStretch(1)
            left.addWidget(self._starts_box)

        def _build_aside(self, body) -> None:
            k = self._k
            aside = QFrame()
            aside.setObjectName("aside")
            aside.setStyleSheet(f"QFrame#aside{{background:{k.panel};border:1px solid {k.line};"
                                f"border-radius:10px;}}")
            aside.setFixedWidth(SIDE_W)
            al = QVBoxLayout(aside)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(0)
            self._tabs_row = QHBoxLayout()
            self._tabs_row.setSpacing(0)
            al.addLayout(self._tabs_row)
            self._tab_btns: dict = {}
            inner = QWidget()
            self._side_col = QVBoxLayout(inner)
            self._side_col.setContentsMargins(12, 10, 12, 10)
            self._side_col.setSpacing(8)
            self._side_scroll = self._scroll(inner)
            al.addWidget(self._side_scroll, 1)
            body.addWidget(aside)

        # -- construction: level 1 -------------------------------------------- #
        def _inside_label(self) -> QLabel:
            k = self._k
            lbl = _wrap("", f"font-size:12px;color:{k.text2};padding:6px 10px;"
                            f"background:{k.panel};border:1px solid {k.line};"
                            "border-radius:10px;")
            lbl.setMinimumHeight(36)
            return lbl

        def _build_walk(self) -> None:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            op = self._card()
            ov = QVBoxLayout(op)
            ov.setContentsMargins(10, 8, 10, 8)
            ov.setSpacing(6)
            r1 = QHBoxLayout()
            r1.setSpacing(3)
            r1.addWidget(self._mono_lbl('open("'))
            self._path_box = self._combo()
            self._path_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self._path_box.setMinimumContentsLength(24)
            self._path_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self._path_box.activated.connect(self._on_path_box)
            r1.addWidget(self._path_box)
            r1.addWidget(self._mono_lbl('", O_RDONLY)'))
            r1.addStretch(1)
            self._walk_show = QPushButton("Show me")
            self._walk_watch = QPushButton("Watch")
            for b in (self._walk_show, self._walk_watch):
                b.setStyleSheet(self._css("small"))
                r1.addWidget(b)
            self._walk_show.clicked.connect(self._on_walk_show)
            self._walk_watch.clicked.connect(self._on_watch)
            ov.addLayout(r1)
            self._walk_banner = _wrap()
            ov.addWidget(self._walk_banner)
            v.addWidget(op)
            mc = self._card()
            ml = QVBoxLayout(mc)
            ml.setContentsMargins(8, 7, 8, 5)
            self._walk_map = _DiskMap(self._k, self._on_walk_block, self._on_hover)
            ml.addWidget(self._walk_map)
            v.addWidget(mc)
            self._walk_inside = self._inside_label()
            v.addWidget(self._walk_inside)
            tc = self._card()
            tl = QVBoxLayout(tc)
            tl.setContentsMargins(10, 6, 10, 6)
            tl.setSpacing(4)
            self._walk_table = _wrap()
            self._walk_table.setAlignment(Qt.AlignTop)
            host = QWidget()
            hl = QVBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(self._walk_table)
            hl.addStretch(1)
            self._walk_scroll = self._scroll(host)
            self._stick_to_bottom(self._walk_scroll)
            tl.addWidget(self._walk_scroll, 1)
            tot = QHBoxLayout()
            self._walk_count = QLabel("")
            self._walk_count.setStyleSheet(_scss(f"font-size:13px;font-weight:700;"
                                                 f"color:{self._k.text};"))
            self._walk_total = _ElideLabel("")
            self._walk_total.setStyleSheet(_scss(f"font-size:11px;color:{self._k.muted};"))
            tot.addWidget(self._walk_count)
            tot.addWidget(self._walk_total, 1)
            tl.addLayout(tot)
            tc.setMinimumHeight(110)
            v.addWidget(tc, 1)
            self._stack.addWidget(page)

        # -- construction: level 2 -------------------------------------------- #
        def _build_byte(self) -> None:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            op = self._card()
            ov = QVBoxLayout(op)
            ov.setContentsMargins(10, 8, 10, 8)
            ov.setSpacing(6)
            r1 = QHBoxLayout()
            r1.setSpacing(4)
            r1.addWidget(self._mono_lbl("read(fd, buf, 1) at offset"))
            self._off_edit = self._edit()
            self._off_edit.setToolTip("Offset")
            self._off_edit.editingFinished.connect(self._on_off_edit)
            r1.addWidget(self._off_edit)
            r1.addStretch(1)
            self._eof_btn = QPushButton("At or past the end")
            self._byte_show = QPushButton("Show me")
            for b in (self._eof_btn, self._byte_show):
                b.setStyleSheet(self._css("small"))
                r1.addWidget(b)
            self._eof_btn.clicked.connect(self._on_eof)
            self._byte_show.clicked.connect(self._on_byte_show)
            ov.addLayout(r1)
            self._off_host = QWidget()
            self._off_flow = FlowLayout(self._off_host, spacing=4)
            self._off_btns: list = []
            ov.addWidget(self._off_host)
            self._byte_banner = _wrap()
            ov.addWidget(self._byte_banner)
            v.addWidget(op)
            pc = self._card()
            pl = QVBoxLayout(pc)
            pl.setContentsMargins(4, 4, 4, 2)
            self._ptr_view = _PointerView(self._k, self._on_slot, self._on_entry)
            pl.addWidget(self._ptr_view)
            v.addWidget(pc)
            tc = self._card()
            tl = QVBoxLayout(tc)
            tl.setContentsMargins(10, 6, 10, 6)
            self._byte_table = _wrap()
            self._byte_table.setAlignment(Qt.AlignTop)
            host = QWidget()
            hl = QVBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(self._byte_table)
            hl.addStretch(1)
            tl.addWidget(self._scroll(host))
            tc.setMinimumHeight(80)
            v.addWidget(tc, 1)
            self._stack.addWidget(page)

        # -- construction: level 3 -------------------------------------------- #
        def _build_make(self) -> None:
            k = self._k
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            op = self._card()
            ov = QVBoxLayout(op)
            ov.setContentsMargins(10, 8, 10, 8)
            ov.setSpacing(6)
            r1 = QHBoxLayout()
            r1.setSpacing(4)
            r1.addWidget(self._mono_lbl("write(fd, buf,"))
            self._mk_n = self._edit(80)
            self._mk_n.setToolTip("Bytes to write")
            r1.addWidget(self._mk_n)
            r1.addWidget(self._mono_lbl(") at offset"))
            self._mk_off = self._edit()
            self._mk_off.setToolTip("Offset")
            r1.addWidget(self._mk_off)
            r1.addStretch(1)
            self._mk_write = QPushButton("Write")
            self._mk_write.setStyleSheet(self._css("prime"))
            self._mk_write.clicked.connect(self._on_make_write)
            self._mk_n.returnPressed.connect(self._on_make_write)
            self._mk_off.returnPressed.connect(self._on_make_write)
            r1.addWidget(self._mk_write)
            ov.addLayout(r1)
            self._mk_host = QWidget()
            self._mk_flow = FlowLayout(self._mk_host, spacing=4)
            self._mk_presets: list = []
            ov.addWidget(self._mk_host)
            self._make_banner = _wrap()
            ov.addWidget(self._make_banner)
            v.addWidget(op)
            pc = self._card()
            pl = QVBoxLayout(pc)
            pl.setContentsMargins(4, 4, 4, 2)
            self._make_ptr = _PointerView(self._k)
            pl.addWidget(self._make_ptr)
            v.addWidget(pc, 1)
            sc = self._card()
            sl = QHBoxLayout(sc)
            sl.setContentsMargins(10, 6, 10, 6)
            self._make_stats = _wrap("", f"font-size:12px;color:{k.text};")
            sl.addWidget(self._make_stats)
            v.addWidget(sc)
            self._stack.addWidget(page)

        # -- construction: level 4 -------------------------------------------- #
        def _build_append(self) -> None:
            k = self._k
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            op = self._card()
            ov = QVBoxLayout(op)
            ov.setContentsMargins(10, 8, 10, 8)
            ov.setSpacing(5)
            r1 = QHBoxLayout()
            r1.setSpacing(4)
            self._op_box = self._combo()
            self._op_box.addItem("append", "append")
            self._op_box.addItem("write at", "at")
            self._op_box.setToolTip("Kind of write")
            self._op_box.activated.connect(self._on_op_box)
            r1.addWidget(self._op_box)
            self._lseek_a = self._mono_lbl("lseek(fd,")
            self._ap_off = self._edit(80)
            self._ap_off.setToolTip("Offset")
            self._lseek_b = self._mono_lbl(");")
            r1.addWidget(self._lseek_a)
            r1.addWidget(self._ap_off)
            r1.addWidget(self._lseek_b)
            r1.addWidget(self._mono_lbl("write(fd, buf,"))
            self._ap_n = self._edit(72)
            self._ap_n.setToolTip("Bytes to write")
            r1.addWidget(self._ap_n)
            r1.addWidget(self._mono_lbl(")"))
            self._ap_n.editingFinished.connect(self._on_ap_edit)
            self._ap_off.editingFinished.connect(self._on_ap_edit)
            self._ap_note = _ElideLabel("")
            self._ap_note.setStyleSheet(_scss(f"font-size:11px;color:{k.muted};"))
            r1.addWidget(self._ap_note, 1)
            ov.addLayout(r1)
            self._ap_host = QWidget()
            self._ap_flow = FlowLayout(self._ap_host, spacing=4)
            self._ap_presets: list = []
            ov.addWidget(self._ap_host)
            self._strip = _FileStrip(k)
            ov.addWidget(self._strip)
            v.addWidget(op)
            mc = self._card()
            ml = QVBoxLayout(mc)
            ml.setContentsMargins(8, 7, 8, 5)
            self._ap_map = _DiskMap(k, self._on_ap_block, self._on_hover)
            ml.addWidget(self._ap_map)
            v.addWidget(mc)
            self._ap_inside = self._inside_label()
            v.addWidget(self._ap_inside)
            tc = self._card()
            tl = QVBoxLayout(tc)
            tl.setContentsMargins(10, 6, 10, 6)
            tl.setSpacing(4)
            self._ap_banner = _wrap()
            tl.addWidget(self._ap_banner)
            host = QWidget()
            hl = QVBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(2)
            self._ap_table = _wrap()
            self._ap_table.setAlignment(Qt.AlignTop)
            hl.addWidget(self._ap_table)
            self._pending = QFrame()
            self._pending.setObjectName("pend")
            self._pending.setStyleSheet(f"QFrame#pend{{background:{k.accent_soft};"
                                        f"border-radius:4px;}}")
            pr = QHBoxLayout(self._pending)
            pr.setContentsMargins(4, 2, 4, 2)
            pr.setSpacing(6)
            self._pend_n = QLabel("")
            self._pend_block = QLabel("")
            self._pend_block.setStyleSheet(_scss(f"{MONO_CSS}font-size:12px;color:{k.text};"))
            self._pend_n.setStyleSheet(_scss(f"font-size:12px;color:{k.muted};"))
            self._r_btn = QPushButton("R")
            self._w_btn = QPushButton("W")
            for b, col in ((self._r_btn, k.rd), (self._w_btn, k.wr)):
                b.setFixedSize(36, 24)
                b.setStyleSheet(_scss(
                    f"QPushButton{{{MONO_CSS}font-weight:700;font-size:12px;color:{col};"
                    f"background:{k.panel};border:1.5px solid {col};border-radius:6px;}}"))
            self._r_btn.clicked.connect(lambda: self._on_commit("R"))
            self._w_btn.clicked.connect(lambda: self._on_commit("W"))
            self._pend_hint = QLabel("")
            self._pend_hint.setStyleSheet(_scss(f"font-size:12px;color:{k.accent_ink};"))
            for w in (self._pend_n, self._pend_block, self._r_btn, self._w_btn, self._pend_hint):
                pr.addWidget(w)
            pr.addStretch(1)
            hl.addWidget(self._pending)
            hl.addStretch(1)
            self._ap_scroll = self._scroll(host)
            self._stick_to_bottom(self._ap_scroll)
            tl.addWidget(self._ap_scroll, 1)
            tot = QHBoxLayout()
            tot.setSpacing(6)
            self._ap_count = QLabel("")
            self._ap_count.setStyleSheet(_scss(f"font-size:13px;font-weight:700;color:{k.text};"))
            self._ap_total = _ElideLabel("")
            self._ap_total.setStyleSheet(_scss(f"font-size:11px;color:{k.muted};"))
            tot.addWidget(self._ap_count)
            tot.addWidget(self._ap_total, 1)
            self._allin = QPushButton("That’s all")
            self._ap_show = QPushButton("Show me")
            self._ap_watch = QPushButton("Watch")
            for b in (self._allin, self._ap_show, self._ap_watch):
                b.setStyleSheet(self._css("small"))
                tot.addWidget(b)
            self._allin.clicked.connect(self._on_allin)
            self._ap_show.clicked.connect(self._on_ap_show)
            self._ap_watch.clicked.connect(self._on_watch)
            tl.addLayout(tot)
            tc.setMinimumHeight(130)
            v.addWidget(tc, 1)
            self._stack.addWidget(page)

        # -- handlers ---------------------------------------------------------- #
        def _stop_watch(self) -> None:
            self._watch.stop()

        def _do(self, fn, *a) -> None:
            self._stop_watch()
            fn(*a)
            self._refresh()

        def _pick_level(self, i: int) -> None:
            self._do(self.session.pick_level, i)

        def _pick_start(self, i: int) -> None:
            self._do(self.session.pick_start, i)

        def _on_undo(self) -> None:
            self._do(self.session.undo)

        def _on_reset(self) -> None:
            self._do(self.session.reset)

        def _on_random(self) -> None:
            self._do(self.session.random)

        def _on_every(self) -> None:
            self._do(self.session.try_every)

        def _on_tab(self, name: str) -> None:
            self.session.tab = name
            self._refresh()

        def _on_watch(self) -> None:
            if self._watch.isActive():
                self._watch.stop()
            elif self.session.watch_step():
                self._watch.start()
            self._refresh()

        def _watch_tick(self) -> None:
            if not self.session.watch_step():
                self._watch.stop()
            self._refresh()

        def _on_hover(self, b) -> None:
            self._hovering = b
            self._fill_inside()

        # level 1
        def _on_walk_block(self, b: int) -> None:
            self._do(self.session.walk_click, b)

        def _on_walk_show(self) -> None:
            self._do(self.session.walk_show)

        def _on_path_box(self, i: int) -> None:
            self._do(self.session.pick_path, self._path_box.itemData(i))

        def _on_path(self, path: str) -> None:
            self._do(self.session.pick_path, path)

        def _on_hide_ib(self) -> None:
            self._do(self.session.toggle_hide_ib)

        # level 2
        def _on_off_edit(self) -> None:
            v = parse_num(self._off_edit.text())
            if v == self.session.byte.offset:
                return
            self._do(self.session.set_offset, self._off_edit.text())

        def _on_offset(self, v: int) -> None:
            self._do(self.session.set_offset, v)

        def _on_slot(self, slot: str) -> None:
            self._do(self.session.click_slot, slot)

        def _on_entry(self, b: int, e: int) -> None:
            self._do(self.session.click_entry, b, e)

        def _on_eof(self) -> None:
            self._do(self.session.press_eof)

        def _on_byte_show(self) -> None:
            self._do(self.session.byte_show)

        # level 3
        def _on_make_write(self) -> None:
            self._do(self.session.make_write, self._mk_off.text(), self._mk_n.text())

        def _on_make_preset(self, o: int, n: int) -> None:
            self._do(self.session.make_write, o, n)

        # level 4
        def _on_op_box(self, _i: int) -> None:
            st = self.session.append
            op = self._op_box.currentData()
            self._do(self.session.set_write, op, st.n, (st.off or 1024) if op == "at" else 0)

        def _on_ap_edit(self) -> None:
            st = self.session.append
            n = self._ap_n.text()
            off = self._ap_off.text() if st.op == "at" else 0
            if parse_num(n) == st.n and (st.op != "at" or parse_num(off) == st.off):
                return
            self._do(self.session.set_write, st.op, n, off)

        def _on_ap_preset(self, preset) -> None:
            op, n, off = preset
            self._do(self.session.set_write, op, n, off)

        def _on_ap_block(self, b: int) -> None:
            self._do(self.session.select_block, b)

        def _on_commit(self, op: str) -> None:
            self._do(self.session.commit, op)

        def _on_ap_show(self) -> None:
            self._do(self.session.append_show)

        def _on_allin(self) -> None:
            self._do(self.session.thats_all)

        def _on_plan(self) -> None:
            self._do(self.session.show_plan)

        # -- drawing ----------------------------------------------------------- #
        def _refresh(self) -> None:
            s = self.session
            lv = s.level
            k = self._k
            self._level_title.setText(f"{lv.n} · {lv.title}")
            self._level_source.setText(lv.source)
            self._level_source.setToolTip(lv.source)
            for i, b in enumerate(self._level_btns):
                b.setChecked(i == s.level_index)
            _clear_layout(self._chips_row)
            self._chips = {}
            for c in goal_chips(lv, s.achieved):
                lbl = _Chip(("✓ " if c.done else "○ ") + c.text)
                lbl.setToolTip(c.tooltip + (" (done)" if c.done else " (not yet)"))
                lbl.setStyleSheet(_scss(
                    "font-size:12px;padding:3px 9px;border-radius:12px;"
                    + (f"background:{k.ok_bg};color:{k.ok_fg};border:1px solid {k.ok};"
                       f"font-weight:600;" if c.done else
                       f"background:{k.panel};color:{k.text};border:1px solid {k.line2};")))
                self._chips_row.addWidget(lbl)
                self._chips[c.id] = lbl
            self._every.setText(lv.every or "Try every")
            self._undo.setEnabled(s.can_undo())
            self._random.setEnabled(lv.id == "make" or any(st.random for st in lv.starts))
            # starts
            for b in self._start_btns:
                b.setParent(None)
                b.deleteLater()
            self._start_btns = []
            for i, st in enumerate(lv.starts):
                label_ = st.label + (f" · {s.seed()}" if st.random and i == s.start_index
                                     else "")
                b = QPushButton(label_)
                b.setCheckable(True)
                b.setChecked(i == s.start_index)
                b.setStyleSheet(self._css("st"))
                if st.random:
                    b.setToolTip("A fresh disk from a seed; Random makes a new one")
                b.clicked.connect(lambda _c=False, i=i: self._pick_start(i))
                self._starts_row.insertWidget(self._starts_row.count() - 1, b)
                self._start_btns.append(b)
            page = {"walk": 0, "byte": 1, "make": 2, "append": 3}.get(lv.id, 0)
            self._stack.setCurrentIndex(page)
            getattr(self, f"_refresh_{lv.id}", lambda: None)()
            self._fill_inside()
            self._fill_tabs()
            self._fill_side()

        def _fill_inside(self) -> None:
            s = self.session
            lid = s.level.id
            if lid not in ("walk", "append"):
                return
            b = self._hovering if self._hovering is not None else s.inspect
            lbl = self._walk_inside if lid == "walk" else self._ap_inside
            if lid == "walk":
                fs = s.world("walk").fs
                fallback = WALK_INSIDE
            else:
                w = s.world("append")
                written = {bb for bb, op in s.append.done if op == "W"}
                fs = s.write_res().fs if b in written else w.fs
                fallback = (f"Point at a block to see what it holds. Blocks from "
                            f"{w.fs.next_free()} on are free.")
            if b is None:
                lbl.setText(f"<span style='color:{self._k.muted}'>{_esc(fallback)}</span>")
                return
            head, rest = block_info(fs, b)
            lbl.setText(f"<b>{_esc(head)}</b> {_esc(rest)}")

        def _fill_tabs(self) -> None:
            k = self._k
            s = self.session
            _clear_layout(self._tabs_row)
            self._tab_btns = {}
            for key, text in TABS.get(s.level.id, ()):
                b = QPushButton(text)
                b.setCheckable(True)
                b.setChecked(key == s.tab)
                b.setStyleSheet(_scss(
                    f"QPushButton{{border:0;border-bottom:2px solid transparent;"
                    f"background:{k.panel2};color:{k.text2};font-size:12px;min-height:34px;}}"
                    f"QPushButton:checked{{border-bottom:2px solid {k.accent};background:"
                    f"{k.panel};color:{k.accent_ink};font-weight:700;}}"))
                b.clicked.connect(lambda _c=False, key=key: self._on_tab(key))
                self._tabs_row.addWidget(b, 1)
                self._tab_btns[key] = b

        # -- level 1 ----------------------------------------------------------- #
        def _refresh_walk(self) -> None:
            s = self.session
            st = s.walk
            w = s.world("walk")
            rows = s.walk_rows()
            v = s.walk_verdict()
            done = v["done"]
            complete = done >= len(rows)
            self._path_box.blockSignals(True)
            self._path_box.clear()
            for ino in w.fs.tree_order():
                self._path_box.addItem(ino.path, ino.path)
            self._path_box.setCurrentIndex(max(0, self._path_box.findData(st.path)))
            self._path_box.blockSignals(False)
            self._walk_show.setEnabled(not complete)
            self._walk_watch.setEnabled(not complete)
            self._walk_watch.setText("Stop" if self._watch.isActive() else "Watch")
            if st.msg:
                self._set_banner(self._walk_banner, "bad", st.msg)
            elif st.ok:
                self._set_banner(self._walk_banner, "ok", st.ok)
            elif complete:
                r = sum(1 for x in rows if x.op == "R")
                end = s.walk_end()
                link = (f" The link led to i-node {end}."
                        if end != w.fs.by_path(st.path).num else "")
                self._set_banner(self._walk_banner, "ok",
                                 f"{st.path} is open: {plural(r, 'read')}"
                                 + (" (with Show me)." if st.hints else ".") + link)
            else:
                self._set_banner(self._walk_banner, "ask",
                                 f"The kernel needs {rows[done].need}. Click the block that "
                                 "holds it.", "Next")
            marks = walk_marks(rows, done)
            self._walk_map.set_disk(w.fs, disk_blocks(w.fs, marks=marks), marks, sel=None)
            trs = [((r.n, r.block, r.why), r.kind) for r in walk_table(rows, done)]
            self._walk_table.setText(self._table_html(("#", "Block", "Why"), trs,
                                                      ("22", "110", "")))
            count, rest = walk_totals(rows, done, v["wrong"], st.hints)
            self._walk_count.setText(count)
            self._walk_total.setText(rest)
            self._walk_total.setToolTip(rest)

        # -- level 2 ----------------------------------------------------------- #
        def _refresh_byte(self) -> None:
            s = self.session
            st = s.byte
            w = s.world("byte")
            done = st.stage == DONE
            if not self._off_edit.hasFocus():
                self._off_edit.setText(num(st.offset))
            self._eof_btn.setEnabled(not done)
            self._byte_show.setEnabled(not done)
            _clear_layout(self._off_flow)
            self._off_btns = []
            self._off_flow.addWidget(self._cap("Offsets"))
            for o in w.offsets:
                b = QPushButton(num(o))
                b.setCheckable(True)
                b.setChecked(o == st.offset)
                b.setStyleSheet(self._css("pre"))
                b.clicked.connect(lambda _c=False, o=o: self._on_offset(o))
                self._off_flow.addWidget(b)
                self._off_btns.append(b)
            if st.msg:
                self._set_banner(self._byte_banner, "bad", st.msg)
            elif st.ok:
                self._set_banner(self._byte_banner, "ok" if done else "info", st.ok)
            else:
                self._set_banner(self._byte_banner, "ask",
                                 f"Which pointer in the i-node does the kernel follow for byte "
                                 f"{num(st.offset)}?", "Next")
            hops = s.read_hops()
            stage = len(hops) if done else st.stage
            picks = [hops[i][1] for i in range(min(stage, len(hops)))]
            self._ptr_view.set_view(w.fs, w.inum, hops, stage, picks, click=not done)
            rows = [(read_log_cells(x)[:3] + (read_log_cells(x)[3]
                                              + ("  shown" if x.shown else ""),), "")
                    for x in reversed(st.log)]
            if rows:
                html = self._table_html(("Offset", "File block", "Pointer", "Reads"), rows,
                                        ("70", "70", "", "60"))
            else:
                html = (f"<span style='color:{self._k.muted}'>Your reads appear here, in the "
                        "tutorial’s table format: Offset · File block · Pointer · Reads."
                        "</span>")
            self._byte_table.setText(html)

        # -- level 3 ----------------------------------------------------------- #
        def _refresh_make(self) -> None:
            s = self.session
            st = s.make
            w = s.world("make")
            fs, results = s.make_state()
            ino = fs.inodes[w.inum]
            for e, v in ((self._mk_n, st.n), (self._mk_off, st.off)):
                if not e.hasFocus():
                    e.setText(num(v))
            _clear_layout(self._mk_flow)
            self._mk_presets = []
            self._mk_flow.addWidget(self._cap("Tutorial"))
            for o, n in w.presets:
                b = QPushButton(f"{num(n)} at {num(o)}")
                b.setStyleSheet(self._css("pre"))
                b.clicked.connect(lambda _c=False, o=o, n=n: self._on_make_preset(o, n))
                self._mk_flow.addWidget(b)
                self._mk_presets.append(b)
            if st.msg:
                self._set_banner(self._make_banner, "bad", st.msg)
            elif results:
                o, n = st.writes[-1]
                self._set_banner(self._make_banner, "info", write_summary(results[-1], o, n))
            else:
                self._set_banner(self._make_banner, "ask", s.level.asks, "GINI asks")
            self._make_ptr.set_view(fs, w.inum)
            data = list(F.walk_data(fs, ino))
            idx = F.index_blocks(fs, ino)
            k = self._k
            self._make_stats.setText(
                f"Size <b style='{MONO_CSS}'>{num(ino.size)}</b> bytes &nbsp;·&nbsp; "
                f"Data blocks <b>{len(data)}</b> &nbsp;·&nbsp; Index blocks <b>{len(idx)}</b>"
                + (f" <span style='color:{k.muted};{MONO_CSS}'>("
                   + ", ".join(str(b) for b in idx) + ")</span>" if idx else "")
                + f" &nbsp;·&nbsp; <span style='color:{k.muted}'>Next free block: "
                  f"{fs.next_free()}</span>")

        # -- level 4 ----------------------------------------------------------- #
        def _refresh_append(self) -> None:
            s = self.session
            st = s.append
            w = s.world("append")
            ino = w.fs.inodes[w.inum]
            res = s.write_res()
            complete = s.append_complete()
            self._op_box.setCurrentIndex(0 if st.op == "append" else 1)
            at = st.op == "at"
            for wdg in (self._lseek_a, self._ap_off, self._lseek_b):
                wdg.setVisible(at)
            if not self._ap_n.hasFocus():
                self._ap_n.setText(num(st.n))
            if not self._ap_off.hasFocus():
                self._ap_off.setText(num(st.off))
            note = (f"O_APPEND: at the end, offset {num(ino.size)} · " if not at else "") + \
                f"{ino.path} · {num(ino.size)} bytes"
            self._ap_note.setText(note)
            self._ap_note.setToolTip(note)
            _clear_layout(self._ap_flow)
            self._ap_presets = []
            for p in w.presets:
                op, n, off = p
                b = QPushButton(f"+{num(n)}" if op == "append" else f"{num(n)} at {num(off)}")
                b.setCheckable(True)
                b.setChecked(op == st.op and n == st.n and (op == "append" or off == st.off))
                b.setStyleSheet(self._css("pre"))
                b.clicked.connect(lambda _c=False, p=p: self._on_ap_preset(p))
                self._ap_flow.addWidget(b)
                self._ap_presets.append(b)
            p0, p1 = res.plan[0]["fb"], res.plan[-1]["fb"]
            self._strip.set_cells(file_strip(w.fs, w.inum, res),
                                  f"File blocks {p0} to {p1} are touched")
            marks = trace_marks(st.done)
            written = {b for b, op in st.done if op == "W"}

            def kind(b, _res=res, _written=written):
                if b in _written and b >= F.FIRST_DATA:
                    return kind_of(_res.fs, b)
                return None
            self._ap_map.set_disk(w.fs, disk_blocks(w.fs, res.fs, marks=marks), marks,
                                  sel=st.sel, kind=kind)
            r, wn = F.counts([F.Row(b, op, "") for b, op in st.done])
            if st.msg:
                self._set_banner(self._ap_banner, "bad", st.msg)
            elif st.ok:
                self._set_banner(self._ap_banner, "info", st.ok)
            elif complete:
                self._set_banner(self._ap_banner, "ok",
                                 f"Complete: {r} R + {wn} W"
                                 + (" (with Show me)" if st.hints else "")
                                 + ". Every block was read before it was written, and written "
                                   "before the block that points to it.")
            elif not st.done:
                self._set_banner(self._ap_banner, "ask", s.level.asks, "GINI asks")
            else:
                self._set_banner(self._ap_banner, "info", "")
            trs = [((t.n, t.block, (t.op, t.op), t.why), "") for t in trace_table(res, st.done)]
            self._ap_table.setText(self._table_html(("#", "Block", "R/W", "Why"), trs,
                                                    ("22", "100", "34", "")))
            self._pending.setVisible(not complete)
            self._pend_n.setText(str(len(st.done) + 1))
            if st.sel is None:
                self._pend_block.setText("?")
                self._pend_hint.setText("click a block on the disk")
                self._r_btn.setVisible(False)
                self._w_btn.setVisible(False)
            else:
                self._pend_block.setText(F.label(st.sel))
                self._pend_hint.setText("read it, or write it?")
                self._r_btn.setVisible(True)
                self._w_btn.setVisible(True)
                self._r_btn.setToolTip(f"Read {F.label(st.sel)}")
                self._w_btn.setToolTip(f"Write {F.label(st.sel)}")
            self._ap_count.setText(f"{r} R + {wn} W")
            rest = (f"{num(st.refused)} refused · Show me used {plural(st.hints, 'time')}")
            self._ap_total.setText(rest)
            self._ap_total.setToolTip(rest)
            for b in (self._allin, self._ap_show, self._ap_watch):
                b.setEnabled(not complete)
            self._ap_watch.setText("Stop" if self._watch.isActive() else "Watch")

        # -- the side panel ----------------------------------------------------- #
        def _side_label(self, html: str, kind: str = "") -> QLabel:
            k = self._k
            css = f"font-size:12px;color:{k.text};"
            if kind == "ok":
                css += (f"background:{k.ok_bg};color:{k.ok_fg};border:1px solid {k.ok};"
                        "border-radius:8px;padding:8px 10px;")
            elif kind == "info":
                css += (f"background:{k.panel2};border:1px solid {k.line};border-radius:8px;"
                        "padding:8px 10px;")
            elif kind == "note":
                css = f"font-size:11px;color:{k.muted};"
            lbl = _wrap(html, css)
            self._side_col.addWidget(lbl)
            self._side_labels.append(lbl)
            return lbl

        def _fill_side(self) -> None:
            _clear_layout(self._side_col)
            self._side_labels: list = []
            self._path_rows: list = []
            self._every_rows: list = []
            self._open_btns: list = []
            self._bands = None
            self._chart = None
            self._hide_ib_btn = None
            self._plan_btn = None
            s = self.session
            getattr(self, f"_side_{s.level.id}", lambda: None)()
            self._side_col.addStretch(1)

        def _side_walk(self) -> None:
            s = self.session
            st = s.walk
            fs = s.world("walk").fs
            k = self._k
            if s.tab == "table":
                row = QHBoxLayout()
                note = QLabel("Click a name to open it.")
                note.setStyleSheet(_scss(f"font-size:11px;color:{k.muted};"))
                row.addWidget(note, 1)
                self._hide_ib_btn = QPushButton("Show i-node blocks" if st.hide_ib
                                                else "Hide i-node blocks")
                self._hide_ib_btn.setStyleSheet(self._css("small"))
                self._hide_ib_btn.clicked.connect(self._on_hide_ib)
                row.addWidget(self._hide_ib_btn)
                self._side_col.addLayout(row)
                for p, i, ib, d, c in fs.table():
                    r = _PathRow(k, p, f"i-node {i} · i-node block {'?' if st.hide_ib else ib}"
                                       f" · data {d}", c, p == st.path, self._on_path)
                    self._side_col.addWidget(r)
                    self._path_rows.append(r)
                self._side_label("1 KB blocks · 8 i-nodes per block · 64 entries per directory "
                                 "block · root is i-node 1.", "note")
                return
            if st.every is None:
                self._side_label(_esc(GUESS_PATHS), "info")
                return
            self._side_label("GINI opened every name on this disk, each call starting cold "
                             "(rule 2):")
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(3)
            for c, h in enumerate(("Path", "R", "in buffer", "")):
                lbl = QLabel(h.upper())
                lbl.setStyleSheet(_scss(f"font-size:10px;font-weight:700;color:{k.muted};"))
                grid.addWidget(lbl, 0, c)
            for i, e in enumerate(st.every):
                path = _wrap(_esc(e["path"]), f"{MONO_CSS}font-size:11px;color:{k.text};")
                rr = QLabel(f"<b>{e['reads']}</b>")
                hh = QLabel(str(e["hits"]))
                b = QPushButton("Open")
                b.setStyleSheet(self._css("small"))
                b.clicked.connect(lambda _c=False, p=e["path"]: self._on_path(p))
                for c, wdg in enumerate((path, rr, hh, b)):
                    grid.addWidget(wdg, i + 1, c)
                self._every_rows.append((e["path"], e["reads"], e["hits"]))
                self._open_btns.append(b)
            grid.setColumnStretch(0, 1)
            self._side_col.addLayout(grid)
            self._side_label(_esc(EVERY_PATH_BOX), "ok")

        def _side_byte(self) -> None:
            s = self.session
            w = s.world("byte")
            fs, ino = w.fs, w.fs.inodes[w.inum]
            k = self._k
            if s.tab == "file":
                data = list(F.walk_data(fs, ino))
                idx = F.index_blocks(fs, ino)
                made = "".join(f"<li>{num(n)} bytes at {num(o)}</li>" for o, n in w.writes)
                self._side_label(f"<b style='{MONO_CSS}'>{_esc(ino.path)}</b> was made by:"
                                 f"<ol style='margin:0;{MONO_CSS}'>{made}</ol>")
                self._side_label(self._kv_table((
                    ("Size", f"<b>{num(ino.size)}</b>"),
                    ("Data blocks", f"<b>{len(data)}</b>: file blocks "
                                    + ", ".join(num(d[0]) for d in data)),
                    ("Index blocks", f"<b>{len(idx)}</b>: "
                                     + (", ".join(str(b) for b in idx) or "–")))))
                self._side_label(self._table_html(
                    ("Pointer", "File blocks", "Index blocks on the way"),
                    [(("direct[0..11]", "0–11", "0"), ""),
                     (("single[n−12]", "12–267", "1"), ""),
                     (("double[a][b]", "268–65,803", "2"), ""),
                     (("triple", "65,804 on", "3"), "")]))
                self._side_label("Byte n is in file block ⌊n / 1,024⌋. In the double range, "
                                 "a = ⌊(n − 268) / 256⌋ and b = (n − 268) mod 256 for file "
                                 "block n. Point at a cell of an index block to see its entry.",
                                 "note")
                return
            ev = s.byte.every
            if ev is None:
                self._side_label(_esc(GUESS_BYTES), "info")
                return
            last = (ino.size - 1) // BS
            self._side_label(f"A 1-byte read at the start of every file block of "
                             f"{_esc(ino.path)} ({num(last + 1)} file blocks):")
            out = []
            for bd in bands(last):
                cells = []
                for e in range(bd.n):
                    fb = bd.first + e
                    c = ev.get(fb)
                    if c is None:
                        cells.append((k.panel, f"file block {fb}: past the end"))
                        continue
                    kind, n = c
                    tip = (f"file block {fb}: " + (plural(n, "read") if kind == "data" else
                                                   f"hole, found with {plural(n, 'read')}"))
                    cells.append((self._cost_fill(kind, n), tip))
                out.append((bd.title, cells))
            self._bands = _BandsView(k)
            self._bands.set_bands(out)
            self._side_col.addWidget(self._bands)
            legend = " &nbsp; ".join(
                f"<span style='color:{self._cost_fill(kd, n)}'>■</span> "
                f"{byte_legend_text(kd, n)} · {cnt}" for kd, n, cnt in byte_legend(ev))
            self._side_label(legend, "note")
            self._side_label(_esc(EVERY_BYTE_BOX), "ok")

        def _cost_fill(self, kind: str, n: int) -> str:
            c = self._k.cost
            if kind == "eof":
                return self._k.panel
            if kind == "data":
                return [c["c1"], c["c1"], c["c2"], c["c3"], c["c3"]][min(n, 4)]
            return [c["h0"], c["h1"], c["h2"], c["h2"]][min(n, 3)]

        def _kv_table(self, rows) -> str:
            k = self._k
            trs = "".join(f"<tr><td style='color:{k.muted}'>{a}</td><td style='{MONO_CSS}'>"
                          f"{b}</td></tr>" for a, b in rows)
            return f"<table cellspacing='0' cellpadding='2'>{trs}</table>"

        def _side_make(self) -> None:
            s = self.session
            st = s.make
            k = self._k
            if s.tab == "writes":
                _fs, results = s.make_state()
                if not results:
                    self._side_label("No writes yet. Try the tutorial’s lines on a fresh file "
                                     "each time (Reset), or build one file with several writes.",
                                     "info")
                for (o, n), r in zip(st.writes, results):
                    rr, ww = F.counts(r.rows)
                    took = taken_text(r.alloc) if r.alloc else "nothing"
                    self._side_label(
                        f"<b style='{MONO_CSS}'>{num(n)} bytes at {num(o)}</b><br>"
                        f"{file_blocks_text(r)} · takes {took}<br>"
                        f"<span style='color:{k.muted}'>{rr} R + {ww} W, with the i-node in "
                        "memory</span>", "info")
                self._side_label("The file system takes free blocks in increasing order, and "
                                 "takes an index block when it first needs it, just before the "
                                 "blocks under it.", "note")
                return
            ev = st.every
            if ev is None:
                self._side_label(_esc(GUESS_MAKE), "info")
                return
            self._side_label("A 1-byte write into each file block of the file as it is now: how "
                             "many free blocks it takes.")
            fills = [k.panel2, k.cost["c1"], k.cost["c2"], k.cost["c3"], k.cost["h2"]]
            out = []
            for bd in bands(F.D0 + 2 * F.NPTR):
                cells = []
                for e in range(bd.n):
                    fb = bd.first + e
                    n = ev.get(fb, 0)
                    cells.append((fills[min(n, 4)],
                                  f"file block {fb}: a 1-byte write takes {plural(n, 'block')}"))
                out.append((bd.title, cells))
            self._bands = _BandsView(k)
            self._bands.set_bands(out)
            self._side_col.addWidget(self._bands)
            legend = " &nbsp; ".join(f"<span style='color:{fills[n]}'>■</span> "
                                     f"{plural(n, 'block')}" for n in range(4))
            self._side_label(legend + f" · triple range: {ev['triple']}", "note")
            self._side_label(_esc(EVERY_MAKE_BOX), "ok")

        def _side_append(self) -> None:
            s = self.session
            st = s.append
            k = self._k
            res = s.write_res()
            w = s.world("append")
            ino = w.fs.inodes[w.inum]
            if s.tab == "plan":
                if not st.plan:
                    self._side_label(_esc(PLAN_FIRST), "info")
                    self._plan_btn = QPushButton("Show the plan")
                    self._plan_btn.setStyleSheet(self._css("small"))
                    self._plan_btn.clicked.connect(self._on_plan)
                    self._side_col.addWidget(self._plan_btn, 0, Qt.AlignLeft)
                    return
                self._side_label(f"Bytes <b style='{MONO_CSS}'>{num(res.offset)}–"
                                 f"{num(res.end - 1)}</b> ({plural(st.n, 'byte')}):")
                self._side_label(self._table_html(("File block", "Bytes written", "Status"),
                                                  [(r, "") for r in plan_rows(res)]))
                self._side_label(
                    f"Blocks taken from the free list: <b style='{MONO_CSS}'>"
                    f"{taken_text(res.alloc)}</b>." if res.alloc
                    else "No block is taken from the free list.")
                self._side_label(_esc(PLAN_RULES), "note")
                return
            if s.tab == "table":
                for p, i, ib, d, c in w.fs.table():
                    r = _PathRow(k, p, f"i-node {i} · i-node block {ib} · data {d}", c,
                                 p == ino.path)
                    self._side_col.addWidget(r)
                    self._path_rows.append(r)
                self._side_label(f"Free blocks start at {w.fs.next_free()}.", "note")
                return
            ev = st.every
            if ev is None:
                self._side_label(_esc(GUESS_LENGTH), "info")
                return
            what = "Appending" if ev["op"] == "append" else f"Writing at {num(ev['off'])}"
            self._side_label(f"{what} 1 to {num(ev['upto'])} bytes:")
            self._chart = _StepChart(k)
            self._chart.set_every(ev)
            self._side_col.addWidget(self._chart)
            self._side_label(f"<span style='color:{k.rd};font-weight:700'>R</span> reads · "
                             f"<span style='color:{k.wr};font-weight:700'>W</span> writes",
                             "note")
            rows = [((f"{num(a)}–{num(b)}", f"{r} R + {wv} W"), "")
                    for a, b, r, wv in ev["iv"][:12]]
            self._side_label(self._table_html(("Bytes", "Count"), rows))
            if len(ev["iv"]) > 12:
                self._side_label(f"… and {len(ev['iv']) - 12} more intervals.", "note")
            self._side_label(_esc(EVERY_APPEND_BOX if ev["op"] == "append" else EVERY_AT_BOX),
                             "ok")

        def closeEvent(self, e) -> None:
            self._watch.stop()
            super().closeEvent(e)

    def open_fs_lab(parent, theme: ThemeManager, device=None, on_event=None) -> FsLab:
        """The entry point: opens the lab with no machine, no provider and no container. A T-Lab
        is a simulator; nothing here needs Docker."""
        w = FsLab(parent, theme, lab=F.get("t-lab-04"), device=device, on_event=on_event)
        w.show()
        w.raise_()
        return w

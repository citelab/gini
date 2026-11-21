"""The file-system lab's engine: the toy UNIX-like file system of the FS tutorial, and its rules.

T-Lab 04 ("You are the file system") is a simulator and not xv6, on purpose, for the same reason
T-Lab 02 is. Students can recite "12 direct, single, double, triple" and still cannot say which
blocks `open()` reads, why a hole costs 1 read in one place and 0 in another, or in what order an
append reads and writes. A real kernel with a buffer cache answers none of that: it caches, it
batches, and it cannot be asked whether an order of block writes is *acceptable*. This module
can. It is the tutorial's disk and the tutorial's seven counting rules, fixed once, so that a
student can open the lab next to the tutorial and watch every answer there come out of the window.

It is a port of the reference engine the professor approved with the design mockup
(`docs/design/t-lab-04-reference/fslab.py`, kept outside the repository), and the port is checked
by that folder's 153 golden checks, carried over one for one into `tests/test_fslab_golden.py`.
Where this file and the reference disagree, the reference is what the professor saw; fix the port.
The additions are only what a shipped lab needs and a reference does not: a loader that refuses a
bad level file in one sentence instead of an `assert`, frozen level objects for the window, a
`World` per start (with the prototype's Random starts made deterministic from a seed, so the proof
chain can rebuild them), the goal rules of §5 as pure functions, and two sweeps that give the
reference's answers without its cost.

Layout (the tutorial's, word for word):
  1 KB blocks, 4-byte pointers, so an index block holds 256 pointers.
  block 0 boot, block 1 superblock (free-block and free-i-node lists),
  blocks 2-33 the i-node table (8 i-nodes of 128 bytes per block: i-node i is in block 2 + i//8),
  blocks 34 onward data (file data, directory data, index blocks, link paths).
  An i-node has direct[0..11] (file blocks 0-11), single (12-267), double (268-65,803), triple.
  A directory entry is 16 bytes, so a directory block holds 64 entries. The root is i-node 1.
  Free blocks are handed out in increasing order.

Counting rules:
  (1) every block read is one R, every block written is one W;
  (2) no caching between calls: every call starts cold;
  (3) within one call the kernel keeps the blocks it has read and writes each changed block once;
  (4) changing part of an existing block costs R + W; a new block, or one overwritten
      completely, costs W;
  (5) a call that allocates anything costs R + W of the superblock, once;
  (6) the i-node of an open file is in memory; when a call changes it, its i-node block costs
      R + W;
  (7) timestamps are ignored.

Three calls are modelled:
  open_call(fs, path)            the path walk: R rows and 'hit' rows (a block already in the
                                 buffer)
  read_call(fs, inum, offset)    a 1-byte read of an open file: index blocks, then the data block
  write_call(fs, inum, offset, n, append=...)  a write of an open file, with allocation; it
                                 returns the canonical trace and the partial order any student
                                 order must respect (check_step / next_step).

Pure, like the rest of `gini.domain`: no Qt, no I/O except reading a level file, and no
randomness except what `random_fs`, `random_holes` and `random_offsets` derive from the seed they
are handed. The purity is what lets the Teaching Center, which installs gini-core too, replay a
submitted walk, read or trace with this same code (`walk_clicks`, `pointer_clicks`,
`trace_steps`) once it learns to — until then it reports these entries as *not checked*.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BS = 1024                 # block size
NPTR = BS // 4            # 256 pointers per index block
NDIRECT = 12
IPB = 8                   # i-nodes per i-node-table block (128 bytes each)
EPB = BS // 16            # 64 directory entries per block
S0 = NDIRECT              # first file block served by single
D0 = S0 + NPTR            # 268
T0 = D0 + NPTR * NPTR     # 65,804
TEND = T0 + NPTR ** 3     # first file block past the triple region
SUPER = 1
FIRST_DATA = 34

TLABS_DIR = Path(__file__).parent / "tlabs"


class LevelError(ValueError):
    """A level file that would not run. The message is one sentence naming what is wrong."""


def _refuse(cond: bool, msg: str) -> None:
    if cond:
        raise LevelError(msg)


def iblock(i: int) -> int:
    """The i-node-table block that holds i-node i."""
    return 2 + i // IPB


def num(n: int) -> str:
    """12,345 the way the notes print numbers."""
    return f"{n:,}"


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
         8: "eight", 9: "nine", 10: "ten"}


def words(n: int) -> str:
    return WORDS.get(n, str(n))


# --------------------------------------------------------------------------- pointers

def route(fb: int):
    """Where file block fb hangs: (top slot of the i-node, entry indices through the index
    blocks)."""
    if fb < 0 or fb >= TEND:
        raise ValueError(f"file block {fb} is outside the file")
    if fb < S0:
        return f"direct[{fb}]", []
    if fb < D0:
        return "single", [fb - S0]
    if fb < T0:
        k = fb - D0
        return "double", [k // NPTR, k % NPTR]
    k = fb - T0
    return "triple", [k // (NPTR * NPTR), (k // NPTR) % NPTR, k % NPTR]


def pointer_name(fb: int) -> str:
    top, idx = route(fb)
    return top + "".join(f"[{e}]" for e in idx)


def region(fb: int) -> str:
    return route(fb)[0].split("[")[0]


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def label(b: int) -> str:
    """How the tutorial's trace tables name a block."""
    if b == SUPER:
        return "superblock"
    if 2 <= b < FIRST_DATA:
        return f"i-node block {b}"
    return f"data {b}"


# --------------------------------------------------------------------------- the file system

@dataclass
class Inode:
    num: int
    kind: str                      # dir | file | link
    path: str
    size: int = 0
    direct: list = field(default_factory=lambda: [0] * NDIRECT)
    single: int = 0
    double: int = 0
    triple: int = 0
    entries: list = field(default_factory=list)   # dirs: [(position, name, inum)]
    count: int = 0                 # dirs: number of entries, named or not
    target: str = ""               # links: the path held in its data block

    def top(self, slot: str) -> int:
        if slot.startswith("direct"):
            return self.direct[int(slot[7:-1])]
        return getattr(self, slot)

    def set_top(self, slot: str, b: int):
        if slot.startswith("direct"):
            self.direct[int(slot[7:-1])] = b
        else:
            setattr(self, slot, b)


@dataclass
class Block:
    kind: str                      # dir | data | index | link | other
    owner: int = 0                 # i-node number
    fb: int = -1                   # file block (dir, data, link)
    what: str = ""                 # index blocks: single, double, … "single under double[1]"
    ptrs: dict = field(default_factory=dict)      # index blocks: entry -> block


class FS:
    def __init__(self, name: str, first_free: int, used_other=()):
        self.name = name
        self.inodes: dict[int, Inode] = {}
        self.blocks: dict[int, Block] = {}
        self.first_free = first_free
        self.taken: set[int] = set()   # blocks below first_free are in use; these came later
        for b in used_other:
            self.blocks[b] = Block("other")

    # ---- building
    def _name(self, path):
        return path.rstrip("/").split("/")[-1] or "/"

    def mkdir(self, path, inum, blocks, entries, count=None):
        """entries: [(name, inum)] in order, or [(position, name, inum)] for a big directory."""
        es = []
        for k, e in enumerate(entries):
            es.append(tuple(e) if len(e) == 3 else (k, e[0], e[1]))
        n = count if count is not None else len(es)
        ino = Inode(inum, "dir", path, size=n * 16, entries=es, count=n)
        _refuse(len(blocks) != -(-n // EPB),
                f"{path}: {n} entries need {-(-n // EPB)} blocks, not {len(blocks)}")
        for k, b in enumerate(blocks):
            ino.direct[k] = b
            self.blocks[b] = Block("dir", inum, k)
        self.inodes[inum] = ino
        return ino

    def mkfile(self, path, inum, size, data, single=0):
        """data: the disk blocks of file blocks 0, 1, 2, ... (no holes); single: its index
        block."""
        ino = Inode(inum, "file", path, size=size)
        _refuse(len(data) != -(-size // BS),
                f"{path}: {num(size)} bytes need {-(-size // BS)} blocks, not {len(data)}")
        _refuse(len(data) > D0, f"{path}: a file on a level disk stops at the single range")
        for fb, b in enumerate(data):
            self.blocks[b] = Block("data", inum, fb)
            if fb < S0:
                ino.direct[fb] = b
            else:
                if not ino.single:
                    _refuse(not single, f"{path} needs a single-indirect block")
                    ino.single = single
                    self.blocks[single] = Block("index", inum, what="single")
                self.blocks[single].ptrs[fb - S0] = b
        self.inodes[inum] = ino
        return ino

    def mklink(self, path, inum, block, target):
        ino = Inode(inum, "link", path, size=len(target), target=target)
        ino.direct[0] = block
        self.blocks[block] = Block("link", inum, 0)
        self.inodes[inum] = ino
        return ino

    def mkempty(self, path, inum):
        """A new, empty file; its name goes into the next free entry of its directory."""
        ino = Inode(inum, "file", path)
        self.inodes[inum] = ino
        parent = path.rsplit("/", 1)[0] or "/"
        for d in self.inodes.values():
            if d.kind == "dir" and d.path == parent:
                d.entries.append((d.count, path.rsplit("/", 1)[1], inum))
                d.count += 1
                d.size = d.count * 16
                _refuse(d.count > EPB * len(self.dir_blocks(d)),
                        f"{parent}: the directory block is full")
        return ino

    # ---- queries
    def by_path(self, path):
        for ino in self.inodes.values():
            if ino.path == path:
                return ino
        raise KeyError(path)

    def dir_blocks(self, ino):
        return [b for b in ino.direct if b]

    def next_free(self):
        b = self.first_free
        while b in self.taken or (b in self.blocks and self.blocks[b].kind != "free"):
            b += 1
        return b

    def alloc(self, block: Block):
        b = self.next_free()
        self.taken.add(b)
        self.blocks[b] = block
        return b

    def clone(self):
        return copy.deepcopy(self)

    def max_block(self):
        return max([self.first_free - 1] + list(self.blocks))

    def tree_order(self):
        """The i-nodes in the order of the tutorial's table: a directory, then its entries in
        turn."""
        out, seen = [], set()

        def visit(inum):
            if inum in seen or inum not in self.inodes:
                return
            seen.add(inum)
            ino = self.inodes[inum]
            out.append(ino)
            if ino.kind == "dir":
                for _, name, child in sorted(ino.entries):
                    if name not in (".", ".."):
                        visit(child)
        visit(1)
        out += [i for i in self.inodes.values() if i.num not in seen]
        return out

    def to_json(self):
        return {
            "name": self.name, "first_free": self.first_free, "taken": sorted(self.taken),
            "inodes": {str(k): {"num": v.num, "kind": v.kind, "path": v.path, "size": v.size,
                                 "direct": v.direct, "single": v.single, "double": v.double,
                                 "triple": v.triple, "entries": [list(e) for e in v.entries],
                                 "count": v.count, "target": v.target}
                    for k, v in self.inodes.items()},
            "blocks": {str(k): {"kind": v.kind, "owner": v.owner, "fb": v.fb, "what": v.what,
                                 "ptrs": {str(e): c for e, c in v.ptrs.items()}}
                    for k, v in self.blocks.items()}}

    def table(self):
        """The rows of the tutorial's table: name, i-node, i-node block, data blocks,
        contents."""
        rows = []
        for ino in self.tree_order():
            if ino.kind == "dir":
                named = ", ".join(f"{n} {i}" for _, n, i in ino.entries)
                if ino.count > len(ino.entries):
                    named = (f"{ino.count} entries in {len(self.dir_blocks(ino))} blocks; "
                             + ", ".join(f"{n} {i}" + ("" if p < 2 else f" (entry {p})")
                                         for p, n, i in ino.entries))
                rows.append((ino.path, ino.num, iblock(ino.num), span(self.dir_blocks(ino)),
                             named))
            elif ino.kind == "link":
                rows.append((ino.path, ino.num, iblock(ino.num), str(ino.direct[0]),
                             (f"symbolic link; block {ino.direct[0]} holds the path "
                             f"{ino.target}")))
            else:
                bl = file_blocks(self, ino)
                nb = len(bl)
                desc = f"{num(ino.size)} bytes: {nb} block{'s' if nb != 1 else ''}"
                if ino.single:
                    desc += f"; single-indirect block {ino.single}"
                rows.append((ino.path, ino.num, iblock(ino.num), span(bl) if bl else "–", desc))
        return rows


def span(bs):
    """[43, 44, ..., 54] -> '43–54'; gaps are listed."""
    bs = sorted(bs)
    if not bs:
        return ""
    out, a, p = [], bs[0], bs[0]
    for b in bs[1:] + [None]:
        if b is not None and b == p + 1:
            p = b
            continue
        out.append(str(a) if a == p else f"{a}–{p}")
        if b is not None:
            a = p = b
    return ", ".join(out)


def file_blocks(fs, ino):
    """Data blocks of a file in file-block order (holes skipped)."""
    return [b for _fb, b in walk_data(fs, ino)]


def walk_data(fs, ino):
    """(file block, data block) for every file block that exists."""
    for k, b in enumerate(ino.direct):
        if b:
            yield k, b

    def down(b, base, span_, depth):
        blk = fs.blocks[b]
        for e in sorted(blk.ptrs):
            c = blk.ptrs[e]
            if depth == 1:
                yield base + e, c
            else:
                yield from down(c, base + e * span_ // NPTR, span_ // NPTR, depth - 1)
    if ino.single:
        yield from down(ino.single, S0, NPTR, 1)
    if ino.double:
        yield from down(ino.double, D0, NPTR * NPTR, 2)
    if ino.triple:
        yield from down(ino.triple, T0, NPTR ** 3, 3)


def index_blocks(fs, ino):
    out = []

    def down(b, depth):
        out.append(b)
        if depth > 1:
            for e in sorted(fs.blocks[b].ptrs):
                down(fs.blocks[b].ptrs[e], depth - 1)
    for slot, d in (("single", 1), ("double", 2), ("triple", 3)):
        if ino.top(slot):
            down(ino.top(slot), d)
    return out


def lookup(fs, ino, fb):
    """Follow the pointers toward file block fb. Returns the hops [(container, slot, value)]:
    container is 'inode' for the pointer in the i-node, or the index block that holds the
    entry."""
    top, idx = route(fb)
    hops = [("inode", top, ino.top(top))]
    cur = ino.top(top)
    for e in idx:
        if not cur:
            break
        nxt = fs.blocks[cur].ptrs.get(e, 0)
        hops.append((cur, e, nxt))
        cur = nxt
    return hops


# --------------------------------------------------------------------------- rows

@dataclass
class Row:
    block: int
    op: str            # R | W | hit
    why: str
    need: str = ""     # what the kernel is after (for hints on a wrong click)

    def as_tuple(self):
        return (self.block, self.op)


def counts(rows):
    r = sum(1 for x in rows if x.op == "R")
    w = sum(1 for x in rows if x.op == "W")
    return r, w


# --------------------------------------------------------------------------- open: the path walk

def open_call(fs: FS, path: str, follow: bool = True):
    """The blocks read by open(path): R rows, and 'hit' rows for blocks already read in this
    call. Returns (rows, inode reached)."""
    rows: list[Row] = []
    buf: set[int] = set()

    def visit(b, why, need):
        if b in buf:
            rows.append(Row(b, "hit", why, need))
        else:
            buf.add(b)
            rows.append(Row(b, "R", why, need))

    def walk(p, hops=0):
        if hops > 8:
            raise ValueError("too many symbolic links")
        comps = [c for c in p.split("/") if c]
        cur = 1
        visit(iblock(1), "root i-node (1): where is the root directory’s data?" if comps
              else "root i-node (1)", "i-node 1, the root")
        curpath = "/"
        for k, name in enumerate(comps):
            d = fs.inodes[cur]
            if d.kind != "dir":
                raise ValueError(f"{curpath} is not a directory")
            found = None
            dname = "root directory" if curpath == "/" else curpath
            for bi, b in enumerate(fs.dir_blocks(d)):
                lo, hi = bi * EPB, min(d.count, (bi + 1) * EPB) - 1
                hit = [e for e in d.entries if lo <= e[0] <= hi and e[1] == name]
                if hit:
                    found = hit[0][2]
                    extra = f" (entry {hit[0][0]})" if len(fs.dir_blocks(d)) > 1 else ""
                    visit(b, f"{dname}: {name} → i-node {found}{extra}",
                          f"the data of {curpath}")
                    break
                visit(b, f"{dname}, entries {lo}–{hi}: no {name}", f"the data of {curpath}")
            if found is None:
                raise KeyError(f"no {name} in {curpath}")
            child = (curpath.rstrip("/") + "/" + name)
            last = k == len(comps) - 1
            ino = fs.inodes[found]
            if ino.kind == "link" and (follow or not last):
                visit(iblock(found),
                      f"i-node {found}: {name} is a symbolic link; its data is block "
                      f"{ino.direct[0]}", f"i-node {found}, of {child}")
                visit(ino.direct[0], f"the path {ino.target}",
                      f"the data of {child}: the path it holds")
                rest = "/".join(comps[k + 1:])
                return walk(ino.target + ("/" + rest if rest else ""), hops + 1)
            if last:
                what = {"file": "itself (mode, size, pointers)", "dir": "itself",
                        "link": "itself"}[ino.kind]
                visit(iblock(found), f"i-node {found}: {name} {what}",
                      f"i-node {found}, of {child}")
            else:
                visit(iblock(found), f"i-node {found}: where is {child}’s data?",
                      f"i-node {found}, of {child}")
            cur = found
            curpath = child
        return cur

    end = walk(path)
    return rows, end


# --------------------------------------------------------------------------- read one byte

def read_call(fs: FS, inum: int, offset: int):
    """A 1-byte read at offset of an open file (its i-node is in memory, rule 6).
    Returns dict(rows, fb, pointer, result) where result is 'data', 'hole' or 'eof',
    and 'zero' says where the kernel found the 0 for a hole."""
    ino = fs.inodes[inum]
    if offset >= ino.size:
        return {"rows": [], "fb": offset // BS, "pointer": pointer_name(offset // BS),
                "result": "eof",
                    "why": f"offset {num(offset)} is at or past the end (size {num(ino.size)}): "
                        "0 bytes, no read"}
    fb = offset // BS
    hops = lookup(fs, ino, fb)
    rows = []
    names = {"single": "the single-indirect block", "double": "the double-indirect block",
             "triple": "the triple-indirect block"}
    top, idx = route(fb)
    for depth, (cont, slot, val) in enumerate(hops):
        if cont == "inode":
            continue
        what = (names[top] if depth == 1 else
                f"the index block under {top}" + "".join(f"[{e}]" for e in idx[:depth - 1]))
        rows.append(Row(cont, "R", f"{what}: entry {slot} → {val if val else '0'}"))
    last = hops[-1]
    if last[2]:
        rows.append(Row(last[2], "R", f"file block {fb}: the data"))
        return {"rows": rows, "fb": fb, "pointer": pointer_name(fb), "result": "data",
                "block": last[2],
                    "why": f"{pointer_name(fb)}: {len(rows)} read{'s' if len(rows) != 1 else ''}"}
    if last[0] == "inode":
        zero = (f"the {last[1]} pointer in the i-node is 0"
                if not last[1].startswith("direct") else f"{last[1]} is 0")
    elif len(hops) == 2 and top != "single":
        zero = f"entry {last[1]} of the {top}-indirect block is 0"
    else:
        zero = "that entry is 0"
    return {"rows": rows, "fb": fb, "pointer": pointer_name(fb), "result": "hole", "zero": zero,
                "why": f"{pointer_name(fb)}: a hole; {zero}"}


# --------------------------------------------------------------------------- write of an open file

@dataclass
class Dep:
    before: tuple      # (block, op)
    after: tuple
    reason: str


@dataclass
class WriteResult:
    rows: list          # canonical order
    deps: list          # the partial order: every Dep must hold
    fs: FS              # the file system after the write
    plan: list          # per file block: dict(fb, lo, hi, status, block)
    alloc: list         # [(block, what)] in allocation order
    offset: int
    end: int
    notes: dict         # why-not texts: block -> sentence, for blocks the call does not touch


def keep_text(p, q, valid_end):
    """What a partial write must keep in a block: positions p..q are written; valid_end is how
    many bytes of the block held data before."""
    if p > 0 and q >= min(BS, valid_end) - 1:
        return f"keep its first {num(p)} bytes"
    if p == 0:
        return f"keep its bytes {num(q + 1)}–1,023"
    return f"keep its bytes outside positions {num(p)}–{num(q)}"


def node_name(key):
    """An index block by its place: ('single', ()) is the single-indirect block, ('double', (1,))
    the single-indirect block under double[1], and so on."""
    top, pre = key
    if not pre:
        return f"{top}-indirect block"
    inner = {("double", 1): "single", ("triple", 1): "double",
             ("triple", 2): "single"}[(top, len(pre))]
    return f"{inner}-indirect block under {top}" + "".join(f"[{e}]" for e in pre)


def entry_name(key, e):
    top, pre = key
    return top + "".join(f"[{x}]" for x in pre) + f"[{e}]"


def existing(fs, ino, key):
    """The block number of an index block that already exists, or 0."""
    top, pre = key
    cur = ino.top(top)
    for e in pre:
        if not cur:
            return 0
        cur = fs.blocks[cur].ptrs.get(e, 0)
    return cur


def write_call(fs: FS, inum: int, offset: int, n: int, append: bool = False,
               index_first: bool = True):
    """write(fd, buf, n) at offset (or at the end, with O_APPEND) of an open file.
    Missing blocks are taken from the free list in increasing order: with index_first, a missing
    index block is taken when it is first needed, just before the blocks under it; otherwise all
    data blocks are taken first and the index blocks after them."""
    fs2 = fs.clone()
    ino = fs2.inodes[inum]
    ino0 = fs.inodes[inum]          # the i-node as it was: existence is judged on it
    old_size = ino.size
    if append:
        offset = old_size
    if n <= 0:
        raise ValueError("write at least one byte")
    end = offset + n
    first, last = offset // BS, (end - 1) // BS
    ib = iblock(inum)

    # pass 1: what exists, what is missing, in the order the kernel meets it
    need = []                 # missing blocks, in order of need: ('idx', key) | ('data', fb)
    parent_of = {}            # missing key -> ('inode', slot) | (index key, entry)
    locate = []               # existing index keys read to find the way, in order
    loc_for = {}              # existing index key -> first file block it was read for
    per_fb = []
    for fb in range(first, last + 1):
        lo, hi = max(offset, fb * BS), min(end, (fb + 1) * BS) - 1
        top, idx = route(fb)
        par = ("inode", top)
        for k in range(len(idx)):
            key = (top, tuple(idx[:k]))
            if existing(fs, ino0, key):
                if key not in loc_for:
                    loc_for[key] = fb
                    locate.append(key)
            elif ("idx", key) not in parent_of:
                need.append(("idx", key))
                parent_of[("idx", key)] = par
            par = (key, idx[k])
        hops = lookup(fs, ino0, fb)
        b = hops[-1][2] if len(hops) == len(idx) + 1 else 0
        if not b:
            need.append(("data", fb))
            parent_of[("data", fb)] = par
        per_fb.append((fb, lo, hi, b, par))

    # pass 2: block numbers from the free list
    order = need if index_first else ([k for k in need if k[0] == "data"]
                                      + [k for k in need if k[0] == "idx"])
    numbers = {}
    for k in order:
        blk = (Block("index", inum, what=node_name(k[1])) if k[0] == "idx"
               else Block("data", inum, k[1]))
        numbers[k] = fs2.alloc(blk)

    def block_of(key):
        return numbers.get(("idx", key)) or existing(fs, ino0, key)

    # pass 3: set the pointers
    inode_changes, changed = [], {}
    new_entries = {}           # index block -> [(entry name, child)]
    parents = {}               # new block -> block that will hold its pointer ('inode' for i-node)
    for k in need:
        child = numbers[k]
        par = parent_of[k]
        if par[0] == "inode":
            ino.set_top(par[1], child)
            inode_changes.append(f"{par[1]} = {child}")
            parents[child] = "inode"
        else:
            pkey, e = par
            pb = block_of(pkey)
            fs2.blocks[pb].ptrs[e] = child
            new_entries.setdefault(pb, []).append((entry_name(pkey, e), child))
            parents[child] = pb
            if ("idx", pkey) not in numbers and pb not in changed:
                changed[pb] = pkey
    if end > old_size:
        ino.size = end
        inode_changes.insert(0, f"size {num(end)}")

    # the canonical order
    rows, located_via = [], {}
    plan = []
    read_idx = set()
    for fb, lo, hi, b, par in per_fb:
        top, idx = route(fb)
        for k in range(len(idx)):
            key = (top, tuple(idx[:k]))
            if key in loc_for and key not in read_idx and loc_for[key] == fb:
                read_idx.add(key)
                xb = existing(fs, ino0, key)
                nxt = fs.blocks[xb].ptrs.get(idx[k], 0)
                if nxt:
                    why = (f"{node_name(key)}: {entry_name(key, idx[k])} → {nxt}, on the way "
                           f"to file block {fb}")
                else:
                    why = (f"{node_name(key)}: {entry_name(key, idx[k])} is 0, so file block "
                           f"{fb} needs a block")
                rows.append(Row(xb, "R", why))
                if k > 0:
                    pkey = (top, tuple(idx[:k - 1]))
                    if existing(fs, ino0, pkey):
                        located_via[xb] = existing(fs, ino0, pkey)
        p, q = lo - fb * BS, hi - fb * BS
        if b:
            if par[0] != "inode":
                located_via[b] = existing(fs, ino0, par[0])
            valid = max(0, min(BS, old_size - fb * BS))
            if q - p + 1 == BS:
                rows.append(Row(b, "W", f"file block {fb}: all 1,024 bytes are replaced, so "
                                        "there is nothing to keep"))
                plan.append({"fb": fb, "lo": lo, "hi": hi, "status": "replaced", "block": b,
                             "used": valid})
            else:
                rows.append(Row(b, "R", f"file block {fb} changes in part: "
                                        f"{keep_text(p, q, valid)}"))
                rows.append(Row(b, "W", f"bytes {num(lo)}–{num(hi)} into positions "
                                        f"{num(p)}–{num(q)}"))
                plan.append({"fb": fb, "lo": lo, "hi": hi, "status": "partial", "block": b,
                             "used": valid})
        else:
            plan.append({"fb": fb, "lo": lo, "hi": hi, "status": "new",
                         "block": numbers[("data", fb)], "used": 0})
    alloc = [(numbers[k], "index" if k[0] == "idx" else "data") for k in order]
    if alloc:
        listing = ", ".join(f"{b} (index)" if w == "index" else str(b) for b, w in alloc)
        rows.append(Row(SUPER, "R", f"take {words(len(alloc))} free "
                                    f"block{'s' if len(alloc) > 1 else ''}: {listing}"))
        rows.append(Row(SUPER, "W", "they are no longer free" if len(alloc) > 1
                        else "it is no longer free"))
    first_new = True
    for d in plan:
        if d["status"] == "new":
            txt = f"file block {d['fb']}, new" + (": written without a read" if first_new else "")
            if d["hi"] - d["lo"] + 1 < BS:
                txt += f" (bytes {num(d['lo'])}–{num(d['hi'])})"
            rows.append(Row(d["block"], "W", txt))
            first_new = False
    idx_rows = []
    for k in need:
        if k[0] == "idx":
            b = numbers[k]
            ent = ", ".join(f"{a} = {c}" for a, c in new_entries.get(b, []))
            idx_rows.append((-(len(k[1][1]) + 1), Row(b, "W", f"new {node_name(k[1])}: {ent}")))
    for b, key in changed.items():
        ent = ", ".join(f"{a} = {c}" for a, c in new_entries[b])
        idx_rows.append((-(len(key[1]) + 1), Row(b, "W", f"{node_name(key)}: add {ent}")))
    rows += [r for _, r in sorted(idx_rows, key=lambda t: t[0])]
    if inode_changes:
        rows.append(Row(ib, "R", "keep the other seven i-nodes in the block"))
        rows.append(Row(ib, "W", f"i-node {inum}: " + ", ".join(inode_changes)))

    # the partial order every acceptable order respects
    have = {(r.block, r.op) for r in rows}
    deps = []
    changed_blocks = set(changed)
    for b in dict.fromkeys(r.block for r in rows):
        if (b, "R") in have and (b, "W") in have:
            if b == ib:
                why = (f"read {label(b)} before writing it: the other seven i-nodes in it must "
                       "be kept (rule 6)")
            elif b == SUPER:
                why = "read the superblock before writing it: it holds the free lists (rule 5)"
            elif b in changed_blocks:
                why = (f"read {label(b)} before writing it: the entries already in it must be "
                       "kept (rule 4)")
            else:
                why = (f"read {label(b)} before writing it: only part of it changes, and the "
                       "rest must be kept (rule 4)")
            deps.append(Dep((b, "R"), (b, "W"), why))
    for child, via in located_via.items():
        for op in ("R", "W"):
            if (child, op) in have:
                deps.append(Dep((via, "R"), (child, op),
                                f"read {label(via)} first: the kernel finds the number of "
                                f"block {child} in it"))
    for b, _ in alloc:
        deps.append(Dep((SUPER, "R"), (b, "W"),
                        f"read the superblock first: that is where the kernel learns that "
                        f"block {b} is free (rule 5)"))
    for child, par in parents.items():
        pb = ib if par == "inode" else par
        if (pb, "W") in have:
            deps.append(Dep((child, "W"), (pb, "W"),
                            f"write {label(child)} before {label(pb)}: {label(pb)} will point "
                            "to it, and a pointer must never lead to a block that is not on "
                            "the disk yet"))

    notes = {}
    if not alloc:
        notes[SUPER] = "Nothing is allocated, so the superblock is not touched (rule 5)."
    if not inode_changes:
        notes[ib] = (f"i-node {inum} does not change (same size, same pointers), so its block "
                     "is neither read nor written (rules 6 and 7).")
    return WriteResult(rows, deps, fs2, plan, alloc, offset, end, notes)


# --------------------------------------------------------------------------- checking an order

def check_step(res: WriteResult, done: list, cand: tuple):
    """Is cand = (block, op) an acceptable next step after the steps in done?
    Returns (ok, message)."""
    have = {(r.block, r.op): r for r in res.rows}
    b, op = cand
    if cand in done:
        return False, (f"{cap(label(b))} is already {'read' if op == 'R' else 'written'} in "
                       "this call: rule 3 keeps it, and each changed block is written once.")
    if cand not in have:
        if b in res.notes:
            return False, res.notes[b]
        if op == "R" and (b, "W") in have:
            if b in {a for a, _ in res.alloc}:
                return False, (f"Block {b} is new: there is nothing on it to keep, so it is "
                               "written without a read (rule 4).")
            return False, (f"{cap(label(b))} is replaced completely: there is nothing to keep, "
                           "so it is not read (rule 4).")
        if op == "W" and (b, "R") in have:
            return False, f"{cap(label(b))} is only read: this call does not change it."
        return False, f"This call does not touch {label(b)}."
    for d in res.deps:
        if d.after == cand and d.before not in done:
            return False, "Not yet: " + d.reason + "."
    return True, have[cand].why


def next_step(res: WriteResult, done: list):
    """The first step of the canonical order that may come next."""
    for r in res.rows:
        c = (r.block, r.op)
        if c not in done and check_step(res, done, c)[0]:
            return r
    return None


def linear_extensions(res: WriteResult, limit=100000):
    """Every acceptable order of the write's steps (for small writes)."""
    steps = [(r.block, r.op) for r in res.rows]
    out = []

    def go(done):
        if len(out) >= limit:
            return
        if len(done) == len(steps):
            out.append(list(done))
            return
        for s in steps:
            if s not in done and check_step(res, done, s)[0]:
                done.append(s)
                go(done)
                done.pop()
    go([])
    return out


# --------------------------------------------------------------------------- try every ...

def every_path(fs: FS):
    out = []
    for ino in fs.tree_order():
        rows, _ = open_call(fs, ino.path)
        r, _ = counts(rows)
        out.append({"path": ino.path, "reads": r, "hits": sum(1 for x in rows if x.op == "hit")})
    return out


def every_offset(fs: FS, inum: int):
    """Cost of a 1-byte read in every file block of the file (one offset per block)."""
    ino = fs.inodes[inum]
    lastfb = (ino.size - 1) // BS
    out = []
    for fb in range(lastfb + 1):
        res = read_call(fs, inum, fb * BS)
        out.append((fb, len(res["rows"]), res["result"]))
    return out


def every_length(fs: FS, inum: int, upto: int, offset=None, append=True):
    """[(n, R, W)] for writes of 1 … upto bytes.

    The reference calls `write_call` once per length, and every call deep-copies the disk:
    thousands of copies on the GUI thread for one press of Try every length. The counts are a
    function of three things only — the last file block touched, whether the write ends on a
    block boundary (which decides "replaced whole" against "changed in part" at both ends), and
    whether it grows the file (which decides the i-node block) — so one call per distinct triple
    gives the reference's answer for every n. `tests/test_fslab_golden.py` checks it against the
    one-call-per-length loop."""
    ino = fs.inodes[inum]
    off = ino.size if append else (offset if offset is not None else 0)
    memo: dict = {}
    out = []
    for n in range(1, upto + 1):
        end = off + n
        key = ((end - 1) // BS, end % BS == 0, end > ino.size)
        if key not in memo:
            memo[key] = counts(write_call(fs, inum, off, n, append=append).rows)
        out.append((n,) + memo[key])
    return out


def intervals(seq):
    """[(n, r, w), ...] -> [(n_from, n_to, r, w)] where the counts stay the same."""
    out = []
    for n, r, w in seq:
        if out and out[-1][2:] == (r, w) and out[-1][1] == n - 1:
            out[-1] = (out[-1][0], n, r, w)
        else:
            out.append((n, n, r, w))
    return out


def alloc_cost(fs: FS, inum: int, fb: int):
    """How many blocks a 1-byte write into file block fb would allocate."""
    res = write_call(fs, inum, fb * BS, 1)
    return len(res.alloc)


def blocks_to_take(fs: FS, inum: int, fb: int) -> int:
    """`alloc_cost` without the write: the data block if it is missing, plus every index block
    missing on its way. Try every block asks this for a thousand file blocks at once, and a
    `write_call` per block is a thousand deep copies; the golden tests hold the two equal."""
    _top, idx = route(fb)
    hops = lookup(fs, fs.inodes[inum], fb)
    return len(idx) + 1 - sum(1 for h in hops if h[2])


# --------------------------------------------------------------------------- the file systems

def tutorial_fs():
    """The sample file system of the FS tutorial (Nov 20)."""
    fs = FS("Tutorial", first_free=61, used_other=range(34, 40))
    fs.mkdir("/", 1, [40], [(".", 1), ("..", 1), ("users", 10)])
    fs.mkdir("/users", 10, [41], [(".", 10), ("..", 1), ("raj", 19)])
    fs.mkdir("/users/raj", 19, [42], [(".", 19), ("..", 10), ("notes", 42), ("lab", 37),
                                      ("m", 20)])
    fs.mkfile("/users/raj/notes", 42, 11500, list(range(43, 55)))
    fs.mkdir("/users/raj/lab", 37, [55], [(".", 37), ("..", 19), ("main.c", 52)])
    fs.mkfile("/users/raj/lab/main.c", 52, 4000, list(range(56, 60)))
    fs.mklink("/users/raj/m", 20, 60, "/users/raj/lab/main.c")
    return fs


def tutorial_big_users():
    """Problem 1(e): /users has grown to 100 entries in two blocks, and raj is entry 80."""
    fs = tutorial_fs()
    u = fs.inodes[10]
    u.entries = [(0, ".", 10), (1, "..", 1), (80, "raj", 19)]
    u.count = 100
    u.size = 1600
    u.direct[1] = 61
    fs.blocks[61] = Block("dir", 10, 1)
    fs.first_free = 62
    return fs


def tutorial_link_to_notes():
    """Problem 1(d): block 60 holds /users/raj/notes."""
    fs = tutorial_fs()
    fs.inodes[20].target = "/users/raj/notes"
    return fs


def yours_fs():
    """A second tree in the same layout, for new numbers: shared i-node blocks, a two-block
    directory, a link, and a file that already has a single-indirect block."""
    fs = FS("Yours", first_free=66)
    fs.mkdir("/", 1, [34], [(".", 1), ("..", 1), ("etc", 6), ("courses", 26)])
    fs.mkdir("/etc", 6, [35], [(".", 6), ("..", 1), ("motd", 14)])
    fs.mkfile("/etc/motd", 14, 300, [36])
    fs.mkdir("/courses", 26, [37, 38], [(0, ".", 26), (1, "..", 1), (70, "os", 29)], count=90)
    fs.mkdir("/courses/os", 29, [39], [(".", 29), ("..", 26), ("labs", 45), ("t4", 31)])
    fs.mkdir("/courses/os/labs", 45, [40], [(".", 45), ("..", 29), ("fs.c", 53), ("out", 60)])
    fs.mkfile("/courses/os/labs/fs.c", 53, 2500, [41, 42, 43])
    fs.mklink("/courses/os/t4", 31, 44, "/courses/os/labs/fs.c")
    fs.mkfile("/courses/os/labs/out", 60, 20000, list(range(45, 57)) + list(range(58, 66)),
              single=57)
    return fs


def holes_file(fs, writes, path="/users/raj/h", inum=23):
    """A new, empty file that gets the writes [(offset, n)], in order. Returns (fs, inum,
    results)."""
    fs = fs.clone()
    fs.mkempty(path, inum)
    out = []
    for off, n in writes:
        res = write_call(fs, inum, off, n)
        fs = res.fs
        out.append(res)
    return fs, inum, out


TUTORIAL_HOLES = [(0, 2000), (100000, 100), (600000, 1000)]
YOURS_HOLES = [(0, 3000), (20000, 50), (280000, 200), (800000, 10)]


# --------------------------------------------------------------------------- random practice

NAMES_DIR = ["home", "users", "srv", "var", "proj", "lab", "src", "docs", "work", "os", "cs",
             "data"]
NAMES_FILE = ["a.c", "b.txt", "notes", "log", "main.c", "out", "x.h", "todo", "report", "fs.c"]


def random_fs(seed: int):
    """A tree shaped like the tutorial's (/d1/d2/{file, d3/{file2}, link}) with fresh numbers."""
    rng = random.Random(seed)
    used_inodes = {1}

    def ino():
        while True:
            i = rng.randint(2, 120)
            if i not in used_inodes:
                used_inodes.add(i)
                return i
    d1, d2, d3 = rng.sample(NAMES_DIR, 3)
    f1, f2, ln = rng.sample(NAMES_FILE, 3)
    ln = rng.choice(["m", "cur", "ln", "last", "go"])
    i_d1, i_d2, i_d3, i_f1, i_f2, i_ln = (ino() for _ in range(6))
    # sometimes the link shares an i-node block with its directory
    if rng.random() < 0.5:
        base = (i_d2 // IPB) * IPB
        cands = [x for x in range(base, base + IPB) if x not in used_inodes and x > 1]
        if cands:
            used_inodes.discard(i_ln)
            i_ln = rng.choice(cands)
            used_inodes.add(i_ln)
    b = FIRST_DATA + rng.randint(0, 6)
    fs = FS(f"Random {seed}", first_free=0, used_other=range(FIRST_DATA, b))

    def take(k):
        nonlocal b
        out = list(range(b, b + k))
        b += k
        return out
    big = rng.random() < 0.35
    fs.mkdir("/", 1, take(1), [(".", 1), ("..", 1), (d1, i_d1)])
    if big:
        pos = rng.randint(64, 99)
        fs.mkdir(f"/{d1}", i_d1, take(2), [(0, ".", i_d1), (1, "..", 1), (pos, d2, i_d2)],
                 count=100)
    else:
        fs.mkdir(f"/{d1}", i_d1, take(1), [(".", i_d1), ("..", 1), (d2, i_d2)])
    fs.mkdir(f"/{d1}/{d2}", i_d2, take(1), [(".", i_d2), ("..", i_d1), (f1, i_f1), (d3, i_d3),
                                            (ln, i_ln)])
    s1 = rng.randint(2, 11) * BS - rng.randint(1, 900)
    fs.mkfile(f"/{d1}/{d2}/{f1}", i_f1, s1, take(-(-s1 // BS)))
    fs.mkdir(f"/{d1}/{d2}/{d3}", i_d3, take(1), [(".", i_d3), ("..", i_d2), (f2, i_f2)])
    s2 = rng.randint(1, 6) * BS - rng.randint(1, 900)
    fs.mkfile(f"/{d1}/{d2}/{d3}/{f2}", i_f2, s2, take(-(-s2 // BS)))
    target = rng.choice([f"/{d1}/{d2}/{d3}/{f2}", f"/{d1}/{d2}/{f1}"])
    fs.mklink(f"/{d1}/{d2}/{ln}", i_ln, take(1)[0], target)
    fs.first_free = b
    return fs


def random_holes(seed: int):
    """The writes [(offset, n)] that make level 2's Random file: a start in the direct range, one
    write in the single range, one in the double range, and six times in ten a second one there,
    under another double[a]. The prototype's `randomHoles`, with Python's generator in place of
    its xorshift: what must agree is the face and the chain's rebuild, and both call this."""
    rng = random.Random(seed * 7919 + 13)
    ri = rng.randint
    w = [(0, ri(1, 4) * 500)]
    w.append((ri(13, 260) * BS + ri(0, 900), ri(1, 300)))
    a = ri(0, 2)
    w.append(((D0 + a * NPTR + ri(0, 255)) * BS + ri(0, 1000), ri(1, 1500)))
    if rng.random() < 0.6:
        a2 = (a + ri(1, 2)) % 4
        w.append(((D0 + a2 * NPTR + ri(0, 255)) * BS + ri(0, 1000), ri(1, 300)))
    w.sort()
    return w


def random_offsets(fs: FS, inum: int, seed: int):
    """Level 2's Random presets: one offset inside each of the first four data blocks, random
    offsets anywhere in the file until there are seven, and the size itself (past the end).

    The prototype drew the first four from anywhere in the block, so an offset in the file's
    last, partly filled block could land past the end and be offered as "data"; here it is drawn
    from the bytes the block holds."""
    ino = fs.inodes[inum]
    rng = random.Random((seed * 2654435761) & 0xFFFFFFFF)
    out = set()
    for fb, _b in list(walk_data(fs, ino))[:4]:
        out.add(fb * BS + rng.randrange(max(1, min(BS, ino.size - fb * BS))))
    while len(out) < 7:
        out.add(rng.randrange(ino.size))
    out.add(ino.size)
    return sorted(out)


# --------------------------------------------------------------------------- the level file

@dataclass(frozen=True)
class NewFile:
    """A file a start makes before the level opens: a new, empty file and the writes into it."""
    path: str
    inode: int
    writes: tuple = ()           # ((offset, n), ...)


@dataclass(frozen=True)
class Start:
    """One of a level's starts. `writes` is level 4's presets, each ("append", n, 0) or
    ("at", n, offset); `presets` is level 3's, each (offset, n)."""
    label: str
    disk: str = ""
    random: bool = False
    path: str = ""
    file: str = ""
    writes: tuple = ()
    new_file: NewFile | None = None
    offsets: tuple = ()
    offset: int = 0
    presets: tuple = ()


@dataclass(frozen=True)
class Goal:
    """A goal chip and the rule that reaches it (design §5). `kind` is one of `path` (a start
    and a path), `link`, `second_block`, `reads`, `shape`, `trace`."""
    id: str
    chip: str
    text: str
    kind: str
    start: str = ""
    path: str = ""
    result: str = ""
    reads: int | None = None
    reads_at_least: int | None = None
    size_at_least: int | None = None
    blocks_at_most: int | None = None
    ranges: tuple = ()
    takes: str = ""

    @property
    def needs_no_hints(self) -> bool:
        """Every goal is reached without Show me or Watch, except the three that are about the
        answer rather than the route to it: Past the end, Big and thin, All three ranges."""
        return not (self.kind == "shape" or (self.kind == "reads" and self.result == "eof"))


@dataclass(frozen=True)
class Level:
    id: str
    n: int
    title: str
    source: str
    call: str                    # open | read | write
    asks: str
    every: str
    starts: tuple
    goals: tuple
    index_first: bool = True

    def start(self, label_: str) -> Start | None:
        return next((s for s in self.starts if s.label == label_), None)

    def goal(self, goal_id: str) -> Goal | None:
        return next((g for g in self.goals if g.id == goal_id), None)


@dataclass(frozen=True)
class LevelFile:
    """One T-Lab of this engine: what the hub needs to draw a tile, the levels, and the disks."""
    id: str
    title: str
    levels: tuple = ()
    summary: str = ""
    hue: str = ""
    released: bool = True
    engine: str = "fs"
    disks: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    def level(self, level_id: str) -> Level | None:
        return next((lv for lv in self.levels if lv.id == level_id), None)

    def disk(self, name: str) -> FS:
        """A fresh copy of a named disk: a world may change it, the level file never does."""
        return self.disks[name].clone()


def _blocks(spec):
    """[40] or [[43, 54]] or [[45, 56], [58, 65]]: a list of blocks, with runs written
    [first, last]."""
    out = []
    for x in spec:
        out += list(range(x[0], x[1] + 1)) if isinstance(x, list) else [x]
    return out


def build_disk(name, spec):
    """A disk of the level file: its names, in any order (a directory before what is in it)."""
    _refuse(not isinstance(spec, dict) or "first_free" not in spec or "names" not in spec,
            f"the disk {name!r} needs first_free and names")
    fs = FS(name, first_free=spec["first_free"], used_other=_blocks(spec.get("other", [])))
    for n in spec["names"]:
        _refuse("path" not in n or "inode" not in n,
                f"a name on the disk {name!r} has no path or no inode")
        if "dir" in n:
            es = [tuple(e) for e in n["entries"]]
            fs.mkdir(n["path"], n["inode"], _blocks(n["dir"]), es, count=n.get("count"))
        elif "link" in n:
            fs.mklink(n["path"], n["inode"], n["block"], n["link"])
        else:
            fs.mkfile(n["path"], n["inode"], n["size"], _blocks(n["data"]),
                      single=n.get("single", 0))
    return fs


def _write_preset(w) -> tuple:
    if "append" in w:
        return ("append", int(w["append"]), 0)
    return ("at", int(w.get("bytes") or 0), int(w.get("at") or 0))


def _start_from_dict(lid: str, s: dict) -> Start:
    _refuse(not s.get("label"), f"a start of level {lid!r} has no label")
    nf = s.get("new_file")
    new_file = None
    if nf:
        new_file = NewFile(path=str(nf["path"]), inode=int(nf["inode"]),
                           writes=tuple((int(o), int(n)) for o, n in nf.get("writes") or []))
    return Start(label=str(s["label"]), disk=str(s.get("disk") or ""),
                 random=bool(s.get("random")), path=str(s.get("path") or ""),
                 file=str(s.get("file") or ""),
                 writes=tuple(_write_preset(w) for w in s.get("writes") or []),
                 new_file=new_file, offsets=tuple(int(o) for o in s.get("offsets") or []),
                 offset=int(s.get("offset") or 0),
                 presets=tuple((int(o), int(n)) for o, n in s.get("presets") or []))


def _goal_from_dict(lid: str, g: dict) -> Goal:
    _refuse(not g.get("id") or not g.get("chip"), f"a goal of level {lid!r} has no id or chip")

    def opt(k):
        return int(g[k]) if g.get(k) is not None else None
    kind = str(g.get("kind") or ("path" if g.get("path") else ""))
    _refuse(kind not in ("path", "link", "second_block", "reads", "shape", "trace"),
            f"goal {g['id']!r} of level {lid!r} has an unknown kind {kind!r}")
    return Goal(id=str(g["id"]), chip=str(g["chip"]), text=str(g.get("text") or ""), kind=kind,
                start=str(g.get("start") or ""), path=str(g.get("path") or ""),
                result=str(g.get("result") or ""), reads=opt("reads"),
                reads_at_least=opt("reads_at_least"), size_at_least=opt("size_at_least"),
                blocks_at_most=opt("blocks_at_most"),
                ranges=tuple(str(r) for r in g.get("ranges") or ()),
                takes=str(g.get("takes") or ""))


def _check_start(lid: str, call: str, s: Start, disks: dict, index_first: bool) -> None:
    """The start opens on something the engine can do, or the file is refused in one
    sentence."""
    if s.random:
        return
    where = f"level {lid!r}, start {s.label!r}"
    _refuse(s.disk not in disks, f"{where} names a disk {s.disk!r} that the file does not have")
    fs = disks[s.disk]
    if s.path:
        try:
            open_call(fs, s.path)
        except (KeyError, ValueError) as e:
            raise LevelError(f"{where} opens {s.path}, which the disk cannot walk ({e})") from e
    if s.file:
        try:
            ino = fs.by_path(s.file)
        except KeyError as e:
            raise LevelError(f"{where} writes {s.file}, which is not on the disk") from e
        _refuse(ino.kind != "file", f"{where} writes {s.file}, which is not a file")
        for op, n, off in s.writes:
            try:
                write_call(fs, ino.num, off, n, append=op == "append", index_first=index_first)
            except (KeyError, ValueError) as e:
                raise LevelError(f"{where} has a write of {n} bytes at {off} that the engine "
                                 f"cannot do ({e})") from e
    if s.new_file is not None:
        nf = s.new_file
        _refuse(nf.inode in fs.inodes, f"{where} makes {nf.path} with i-node {nf.inode}, "
                                       "which is already in use")
        try:
            hfs, h, _ = holes_file(fs, list(nf.writes), path=nf.path, inum=nf.inode)
            for o in s.offsets:
                read_call(hfs, h, o)
            for o, n in s.presets:
                write_call(hfs, h, o, n)
        except (KeyError, ValueError) as e:
            raise LevelError(f"{where} makes a file the engine cannot ({e})") from e
    _refuse(call == "open" and not s.path, f"{where} has no path to open")
    _refuse(call == "read" and s.new_file is None, f"{where} has no file to read")


def from_dict(d: dict) -> LevelFile:
    """The level file, checked: every start names a disk that exists and a path that is on it,
    and every preset is a write the engine can do. Refuses with one sentence, never an assert."""
    d = d or {}
    lab_id = str(d.get("id") or "")
    _refuse(not lab_id, "the level file has no id")
    _refuse(str(d.get("engine") or "fs") != "fs", f"'{lab_id}' is not a file-system lab")
    disks = {str(k): build_disk(str(k), v) for k, v in (d.get("disks") or {}).items()}
    levels = []
    for i, L in enumerate(d.get("levels") or []):
        lid = str(L.get("id") or "")
        _refuse(not lid, f"level {i + 1} has no id")
        call = str(L.get("call") or "")
        _refuse(call not in ("open", "read", "write"), f"level {lid!r} has an unknown call "
                                                       f"{call!r}")
        index_first = str(L.get("allocation") or "index_first") != "index_last"
        starts = tuple(_start_from_dict(lid, s) for s in L.get("starts") or [])
        _refuse(not starts, f"level {lid!r} has no starts")
        for s in starts:
            _check_start(lid, call, s, disks, index_first)
        goals = tuple(_goal_from_dict(lid, g) for g in L.get("goals") or [])
        labels = {s.label for s in starts}
        for g in goals:
            _refuse(bool(g.start) and g.start not in labels,
                    f"goal {g.id!r} of level {lid!r} names a start {g.start!r} it does not have")
        levels.append(Level(id=lid, n=i + 1, title=str(L.get("title") or lid),
                            source=str(L.get("source") or ""), call=call,
                            asks=str(L.get("asks") or ""), every=str(L.get("every") or ""),
                            starts=starts, goals=goals, index_first=index_first))
    ids = [lv.id for lv in levels]
    _refuse(len(set(ids)) != len(ids), f"'{lab_id}' has two levels with the same id")
    return LevelFile(id=lab_id, title=str(d.get("title") or lab_id), levels=tuple(levels),
                     summary=str(d.get("summary") or ""), hue=str(d.get("hue") or ""),
                     released=bool(d.get("released", True)), disks=disks)


def from_yaml(text: str) -> LevelFile:
    return from_dict(yaml.safe_load(text) or {})


def load(path) -> LevelFile:
    return from_yaml(Path(path).read_text(encoding="utf-8"))


def get(lab_id: str = "t-lab-04") -> LevelFile | None:
    """The shipped file-system lab with this id, through the catalog that knows every engine."""
    from .tlab_catalog import files
    for p in files("fs"):
        lab = load(p)
        if lab.id == lab_id:
            return lab
    return None


# --------------------------------------------------------------------------- worlds

@dataclass
class World:
    """What a start opens on, for the level's call: the disk, and the path (open) or the open
    file's i-node (read, write), with the start's presets. `seed` is set for a Random start, and
    is all the chain needs to rebuild one."""
    fs: FS
    label: str
    seed: int | None = None
    path: str = ""
    inum: int = 0
    writes: tuple = ()           # level 2: the writes that made the file
    offsets: tuple = ()          # level 2: the offset presets
    offset: int = 0              # level 2: the offset it opens on
    presets: tuple = ()          # level 3: (offset, n); level 4: (op, n, offset)


def _random_world(lab: LevelFile, level: Level, seed: int) -> World:
    if level.call == "open":
        fs = random_fs(seed)
        files = [i for i in fs.tree_order() if i.kind == "file"]
        return World(fs=fs, label="Random", seed=seed, path=files[-1].path)
    if level.call == "read":
        writes = random_holes(seed)
        hfs, h, _ = holes_file(tutorial_fs(), writes, "/users/raj/h", 23)
        offs = random_offsets(hfs, h, seed)
        return World(fs=hfs, label="Random", seed=seed, inum=h, writes=tuple(writes),
                     offsets=tuple(offs), offset=offs[3])
    fs = random_fs(seed)
    f = next(i for i in fs.tree_order() if i.kind == "file")
    used = f.size % BS or BS
    room = BS - used
    presets = [("append", 500 + (seed * 937) % 4000, 0), ("append", room, 0),
               ("append", room + 1, 0), ("at", 1024, 0)]
    return World(fs=fs, label="Random", seed=seed, inum=f.num,
                 presets=tuple(p for p in presets if p[1] >= 1))


def start_world(lab: LevelFile, level_id: str, label_: str, seed: int | None = None) -> World:
    """What a start opens on. A Random start is rebuilt from its seed, so the same seed gives the
    same world on every machine."""
    level = lab.level(level_id)
    if level is None:
        raise KeyError(level_id)
    s = level.start(label_)
    if s is None:
        raise KeyError(label_)
    if s.random:
        return _random_world(lab, level, int(seed if seed is not None else 1))
    fs = lab.disk(s.disk)
    if s.new_file is not None:
        nf = s.new_file
        hfs, h, _ = holes_file(fs, list(nf.writes), path=nf.path, inum=nf.inode)
        return World(fs=hfs, label=s.label, inum=h, writes=nf.writes, offsets=s.offsets,
                     offset=s.offset, presets=s.presets)
    if s.file:
        return World(fs=fs, label=s.label, inum=fs.by_path(s.file).num, presets=s.writes)
    return World(fs=fs, label=s.label, path=s.path)


# --------------------------------------------------------------------------- replaying a student

def walk_clicks(fs, path, clicks):
    """Level 1: the student's clicks on the disk, judged as the window judges them. A click on
    the next block to read is accepted, and any blocks before it that are already in the buffer
    are passed over (rule 3); a click on the next block already in the buffer is accepted as a
    hit."""
    rows, _ = open_call(fs, path)
    done, wrong = 0, 0
    for b in clicks:
        if done >= len(rows):
            break
        j = done
        while j < len(rows) and rows[j].op == "hit":
            j += 1
        if rows[done].op == "hit" and rows[done].block == b:
            done += 1
        elif j < len(rows) and rows[j].block == b:
            done = j + 1
        else:
            wrong += 1
    return {"complete": done == len(rows), "done": done, "wrong": wrong, "reads": counts(rows)[0],
                "second_block": any(" entries " in r.why and ": no " in r.why for r in rows),
                "link": fs.by_path(path).kind == "link"}


def pointer_clicks(fs, inum, offset, clicks):
    """Level 2: clicks are the slot of the i-node ('double') and then entries (1, 61), or 'eof'.
    Returns dict(complete, wrong, result, reads)."""
    res = read_call(fs, inum, offset)
    wrong = 0
    if res["result"] == "eof":
        ok = "eof" in clicks
        return {"complete": ok, "wrong": len([c for c in clicks if c != "eof"]), "result": "eof",
                    "reads": 0}
    hops = lookup(fs, fs.inodes[inum], res["fb"])
    stage = 0
    for c in clicks:
        if stage >= len(hops):
            break
        want = hops[stage][1]
        if c == want:
            v = hops[stage][2]
            stage += 1
            if not v or stage == len(hops):
                return {"complete": True, "wrong": wrong, "result": res["result"],
                            "reads": len(res["rows"])}
        else:
            wrong += 1
    return {"complete": False, "wrong": wrong, "result": res["result"], "reads": len(res["rows"])}


def trace_steps(res: WriteResult, steps):
    """Level 4: the student's (block, op) steps; refused ones are skipped, as in the window."""
    done, refused = [], 0
    for s in steps:
        s = tuple(s)
        ok, _ = check_step(res, done, s)
        if ok:
            done.append(s)
        else:
            refused += 1
    return {"complete": len(done) == len(res.rows), "refused": refused, "done": done}


# --------------------------------------------------------------------------- goals (design §5)

def walk_goals(level: Level, start: str, path: str, w: dict) -> tuple:
    """The goals a completed walk reaches, given `walk_clicks`'s verdict. Whether Show me was
    used is the caller's to judge (`Goal.needs_no_hints`)."""
    if not w.get("complete"):
        return ()
    out = []
    for g in level.goals:
        on_path = g.kind == "path" and (not g.start or g.start == start) and g.path == path
        if (on_path or (g.kind == "link" and w.get("link"))
                or (g.kind == "second_block" and w.get("second_block"))):
            out.append(g.id)
    return tuple(out)


def read_goals(level: Level, result: str, reads: int) -> tuple:
    """The goals a finished 1-byte read reaches: its result (data, hole, eof) and its reads."""
    out = []
    for g in level.goals:
        if g.kind != "reads" or (g.result and g.result != result):
            continue
        if g.reads is not None and reads != g.reads:
            continue
        if g.reads_at_least is not None and reads < g.reads_at_least:
            continue
        out.append(g.id)
    return tuple(out)


def shape_goals(level: Level, fs: FS, inum: int) -> tuple:
    """The goals the file as it now is reaches: its size against its blocks, and its ranges."""
    ino = fs.inodes[inum]
    data = list(walk_data(fs, ino))
    nblocks = len(data) + len(index_blocks(fs, ino))
    have = {region(fb) for fb, _ in data}
    out = []
    for g in level.goals:
        if g.kind != "shape":
            continue
        if g.size_at_least is not None and ino.size < g.size_at_least:
            continue
        if g.blocks_at_most is not None and nblocks > g.blocks_at_most:
            continue
        if g.ranges and not set(g.ranges) <= have:
            continue
        out.append(g.id)
    return tuple(out)


def trace_goals(level: Level, res: WriteResult) -> tuple:
    """The goals a write's trace reaches once it is built to the end: takes nothing (and reads
    something), takes an index block, or has no read at all."""
    r, _w = counts(res.rows)
    out = []
    for g in level.goals:
        if g.kind != "trace":
            continue
        if g.takes == "none" and not (not res.alloc and r > 0):
            continue
        if g.takes == "index" and not any(w == "index" for _, w in res.alloc):
            continue
        if g.reads is not None and r != g.reads:
            continue
        out.append(g.id)
    return tuple(out)

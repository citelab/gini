"""xv6 file-system state model — the Storage face's read side.

xv6's on-disk layout is a fixed sequence of regions (boot | super | log | inodes | bitmap |
data); a file is a `dinode` with up to 12 direct + 1 indirect block pointers; a directory is an
array of `dirent {inum, name}`; the buffer cache holds recently-used blocks; and crash-safe
writes go through a small write-ahead LOG (a transaction of block numbers that is committed then
installed). This module models all of that as pure data, with parsers for the GDB reads that
produce it (superblock, log header) — so the layout maths, the parsers, and the DemoDisk feed
are all unit-tested without a disk image. The live reads are Mac-side (GDB + xv6 symbols).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BSIZE = 1024                      # xv6 block size
IPB = BSIZE // 64                 # inodes per block (sizeof(dinode) == 64) -> 16
BPB = BSIZE * 8                   # bitmap bits per block -> 8192
DIRSIZ = 14
_ITYPE = {0: "free", 1: "dir", 2: "file", 3: "dev"}


@dataclass
class Superblock:
    size: int = 0                 # total blocks
    nblocks: int = 0              # data blocks
    ninodes: int = 0
    nlog: int = 0
    logstart: int = 0
    inodestart: int = 0
    bmapstart: int = 0


@dataclass
class Region:
    name: str
    start: int
    count: int

    @property
    def end(self) -> int:
        return self.start + max(self.count, 1) - 1


@dataclass
class Inode:
    inum: int
    type: str                     # free|dir|file|dev
    nlink: int = 0
    size: int = 0
    blocks: list = field(default_factory=list)   # data block numbers (direct+indirect)


@dataclass
class Dirent:
    inum: int
    name: str
    is_dir: bool = False
    depth: int = 0                # for a flattened tree view


@dataclass
class Buf:
    blockno: int
    valid: bool = True
    dirty: bool = False
    refcnt: int = 0


@dataclass
class LogState:
    start: int = 0
    size: int = 0
    outstanding: int = 0          # syscalls in flight in this group commit
    committing: bool = False
    blocks: list = field(default_factory=list)    # dest block numbers in the current transaction

    @property
    def phase(self) -> str:
        if self.committing:
            return "committing"
        if self.blocks:
            return "building"
        return "idle"


@dataclass
class FsSnapshot:
    sb: Superblock
    regions: list = field(default_factory=list)
    inodes: list = field(default_factory=list)
    tree: list = field(default_factory=list)      # flattened Dirents
    bufs: list = field(default_factory=list)
    log: LogState = field(default_factory=LogState)
    hits: int = 0
    misses: int = 0
    # Provenance so the UI never passes off demo data as real. `source` is "real" (from the
    # running kernel) or "demo" (the DemoDisk stand-in). `ok` is False when a REAL read was
    # attempted but produced nothing — the face must show an error, NOT silently fall to demo.
    source: str = "real"
    ok: bool = True
    # Which real panels this build can actually populate (the rest render "not available (real)"
    # instead of fake data). Superblock + log are dumped today; inodes/dir/bcache are not yet.
    have: tuple = ("layout", "log")

    @property
    def hit_rate(self) -> float:
        tot = self.hits + self.misses
        return (self.hits / tot) if tot else 0.0


# -- layout maths ----------------------------------------------------------- #
def layout(sb: Superblock) -> list[Region]:
    """The fixed on-disk region sequence, derived from the superblock."""
    ninode_blocks = (sb.ninodes + IPB - 1) // IPB if sb.ninodes else 0
    nbitmap = (sb.size + BPB - 1) // BPB if sb.size else 0
    data_start = sb.bmapstart + nbitmap
    data_blocks = max(sb.size - data_start, 0)
    return [
        Region("boot", 0, 1),
        Region("super", 1, 1),
        Region("log", sb.logstart, sb.nlog),
        Region("inodes", sb.inodestart, ninode_blocks),
        Region("bitmap", sb.bmapstart, nbitmap),
        Region("data", data_start, data_blocks),
    ]


# -- parsers (GDB struct prints) -------------------------------------------- #
def _ints(text: str) -> list[int]:
    """All integers in a GDB brace list, expanding `N <repeats K times>`."""
    out: list[int] = []
    for tok in text.split(","):
        tok = tok.strip()
        m = re.match(r"(-?\d+)\s*<repeats\s+(\d+)\s+times>", tok)
        if m:
            out += [int(m.group(1))] * int(m.group(2))
        elif re.match(r"-?\d+$", tok):
            out.append(int(tok))
    return out


def _field(text: str, name: str) -> int | None:
    m = re.search(rf"\b{name}\s*=\s*(-?\d+)", text)
    return int(m.group(1)) if m else None


def parse_superblock(text: str) -> Superblock:
    """GDB `p *sb` -> Superblock (fields matched by name; magic/order-independent)."""
    g = lambda n: _field(text, n) or 0
    return Superblock(size=g("size"), nblocks=g("nblocks"), ninodes=g("ninodes"),
                      nlog=g("nlog"), logstart=g("logstart"), inodestart=g("inodestart"),
                      bmapstart=g("bmapstart"))


def parse_logheader(text: str, start: int = 0, size: int = 0,
                    outstanding: int = 0, committing: bool = False) -> LogState:
    """Parse the write-ahead log state. `n` = blocks in this transaction; `block[]` = dests.
    Also reads `start`/`outstanding`/`committing` from the serial dump if present (the `\\bstart`
    boundary avoids matching the superblock's logstart/inodestart/bmapstart)."""
    n = _field(text, "n") or 0
    m = re.search(r"block\s*=\s*\{([^}]*)\}", text)
    blocks = _ints(m.group(1))[:n] if m else []
    st, out, com = _field(text, "start"), _field(text, "outstanding"), _field(text, "committing")
    return LogState(start=st if st is not None else start, size=size,
                    outstanding=out if out is not None else outstanding,
                    committing=bool(com) if com is not None else committing, blocks=blocks)


def parse_dinode(text: str, inum: int) -> Inode:
    """GDB `p *ip` (dinode) -> Inode (type/nlink/size + addrs block list)."""
    t = _ITYPE.get(_field(text, "type") or 0, "free")
    m = re.search(r"addrs\s*=\s*\{([^}]*)\}", text)
    blocks = [b for b in _ints(m.group(1))] if m else []
    return Inode(inum=inum, type=t, nlink=_field(text, "nlink") or 0,
                 size=_field(text, "size") or 0, blocks=[b for b in blocks if b])


def fs_summary(fs: FsSnapshot) -> str:
    """Compact, LLM-facing summary of the file-system state (for the Ask GINI card, L2)."""
    if fs is None:
        return ""
    sb = fs.sb
    lines = [f"file system: {sb.size} blocks · layout "
             + " | ".join(r.name for r in fs.regions)]
    active = [i for i in fs.inodes if i.type != "free"]
    if active:
        lines.append("  inodes: "
                     + ", ".join(f"#{i.inum} {i.type}({i.size}B)" for i in active[:6]))
    lines.append(f"  buffer cache: {fs.hits} hits / {fs.misses} miss "
                 f"({fs.hit_rate * 100:.0f}% hit)")
    lg = fs.log
    tail = (f" — {len(lg.blocks)} block(s): {', '.join(map(str, lg.blocks))}"
            if lg.blocks else "")
    lines.append(f"  write-ahead log: {lg.phase}{tail}")
    return "\n".join(lines)


# -- offline demo ----------------------------------------------------------- #
class DemoDisk:
    """A deterministic small xv6 file system + a simulated write, so the Storage face is
    explorable and testable without a disk image. Not a real FS — a faithful-shaped stand-in."""

    def __init__(self) -> None:
        self.sb = Superblock(size=2000, nblocks=1954, ninodes=200, nlog=30,
                             logstart=2, inodestart=32, bmapstart=45)
        self._regions = layout(self.sb)
        data0 = self._regions[-1].start
        self.inodes = [
            Inode(1, "dir", 1, 96, [data0]),
            Inode(2, "dev", 1, 0, []),
            Inode(3, "file", 1, 2200, [data0 + 1, data0 + 2, data0 + 3]),
            Inode(4, "dir", 1, 64, [data0 + 4]),
            Inode(5, "file", 2, 900, [data0 + 5]),
        ]
        self.tree = [
            Dirent(1, "/", True, 0),
            Dirent(2, "console", False, 1),
            Dirent(3, "README", False, 1),
            Dirent(4, "usr", True, 1),
            Dirent(5, "usr/sh", False, 2),
        ]
        self.bufs = [Buf(1, True, False, 0), Buf(32, True, False, 1),
                     Buf(data0, True, False, 2), Buf(data0 + 1, True, True, 1),
                     Buf(45, True, False, 0)]
        self.hits, self.misses = 128, 24
        self.log = LogState(start=self.sb.logstart, size=self.sb.nlog)

    def snapshot(self) -> FsSnapshot:
        return FsSnapshot(sb=self.sb, regions=list(self._regions), inodes=list(self.inodes),
                          tree=list(self.tree), bufs=list(self.bufs), log=self.log,
                          hits=self.hits, misses=self.misses, source="demo",
                          have=("layout", "log", "inodes", "dir", "bcache"))

    def simulate_write(self) -> FsSnapshot:
        """Advance the write-ahead log: idle -> building -> committing -> installed(idle)."""
        d0 = self._regions[-1].start
        if self.log.phase == "idle":
            self.log = LogState(self.sb.logstart, self.sb.nlog, outstanding=1,
                                blocks=[45, d0 + 1, self.sb.inodestart])   # bitmap, data, inode
            self.hits += 3
        elif self.log.phase == "building":
            self.log = LogState(self.sb.logstart, self.sb.nlog, committing=True,
                                blocks=list(self.log.blocks))
        else:                                     # committing -> installed, back to idle
            for b in self.log.blocks:
                self.bufs.append(Buf(b, True, False, 0))
            self.misses += 1
            self.log = LogState(self.sb.logstart, self.sb.nlog)
        return self.snapshot()

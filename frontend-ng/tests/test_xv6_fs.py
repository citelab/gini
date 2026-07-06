"""xv6 file-system model — layout maths, GDB parsers, and the DemoDisk write-ahead log."""
from gini.domain.xv6_fs import (
    DemoDisk, Superblock, layout, parse_dinode, parse_logheader, parse_superblock,
)

SB = "$1 = {magic = 271828, size = 2000, nblocks = 1954, ninodes = 200, nlog = 30, " \
     "logstart = 2, inodestart = 32, bmapstart = 45}"
LH = "$2 = {n = 3, block = {45, 587, 32, 0 <repeats 27 times>}}"
DI = "$3 = {type = 2, major = 0, minor = 0, nlink = 1, size = 2200, " \
     "addrs = {586, 587, 588, 0 <repeats 10 times>}}"


def test_parse_superblock_by_field_name():
    sb = parse_superblock(SB)
    assert sb.size == 2000 and sb.ninodes == 200 and sb.nlog == 30
    assert sb.logstart == 2 and sb.inodestart == 32 and sb.bmapstart == 45


def test_layout_regions_are_contiguous_and_ordered():
    regs = layout(parse_superblock(SB))
    names = [r.name for r in regs]
    assert names == ["boot", "super", "log", "inodes", "bitmap", "data"]
    # log has nlog blocks starting at logstart
    log = next(r for r in regs if r.name == "log")
    assert log.start == 2 and log.count == 30
    # data fills the remainder up to size-1
    data = regs[-1]
    assert data.end == 1999


def test_parse_logheader_uses_n_and_expands_repeats():
    lh = parse_logheader(LH, start=2, size=30)
    assert lh.blocks == [45, 587, 32]          # only n=3, repeats not counted
    assert lh.phase == "building"


def test_parse_dinode_maps_type_and_blocks():
    ino = parse_dinode(DI, inum=3)
    assert ino.type == "file" and ino.nlink == 1 and ino.size == 2200
    assert ino.blocks == [586, 587, 588]       # trailing zeros dropped


def test_demodisk_snapshot_and_hit_rate():
    snap = DemoDisk().snapshot()
    assert snap.sb.size == 2000
    assert any(i.type == "dir" for i in snap.inodes)
    assert 0.0 < snap.hit_rate < 1.0
    assert [r.name for r in snap.regions][0] == "boot"


def test_demodisk_write_cycles_the_log():
    d = DemoDisk()
    assert d.log.phase == "idle"
    d.simulate_write(); assert d.log.phase == "building" and d.log.blocks
    d.simulate_write(); assert d.log.phase == "committing"
    d.simulate_write(); assert d.log.phase == "idle"   # installed, back to idle

"""The golden checks for T-Lab 04's engine: every number the FS tutorial gives.

Ported one for one from the reference engine's own test file (`test_fslab.py` beside the
reference `fslab.py`, 153 checks, which the professor approved with the design mockup). Every
number here is printed in the tutorial of November 20 — Problems 1 to 3 and their answers — or
is the Yours disk's, which is new on purpose and checked just as hard. If one of these moves, the
lab no longer matches the tutorial a student has open beside it.

The reference grouped its checks by problem with a counter; here each group is a test named for
what it shows, and every number is kept. Added after the port: the shipped level file loads
through `tlab_catalog` onto the engine's own disks, every goal is reached through the replays the
chain records, Random starts rebuild from their seed, and the two sweeps that skip the
reference's per-case `write_call` give its answers.
"""
from __future__ import annotations

import pytest
from gini.domain import fslab as F
from gini.domain import tlab_catalog as TC


def reads(rows):
    return [r.block for r in rows if r.op == "R"]


def trace(res):
    return [(F.label(r.block), r.op) for r in res.rows]


@pytest.fixture(scope="module")
def fs():
    return F.tutorial_fs()


@pytest.fixture(scope="module")
def holes(fs):
    hfs, h, _ = F.holes_file(fs, F.TUTORIAL_HOLES)
    return hfs, h


@pytest.fixture(scope="module")
def lab():
    lab = TC.get("t-lab-04")
    assert lab is not None
    return lab


# ---------------------------------------------------------------- the layout
def test_i_node_i_is_in_block_2_plus_i_over_8():
    assert [F.iblock(i) for i in (1, 10, 19, 20, 37, 42, 52)] == [2, 3, 4, 4, 6, 7, 8]


def test_pointer_names_follow_the_reach_table():
    assert [F.pointer_name(b) for b in (0, 11, 12, 20, 267, 268, 585, 586, 600, 976, 65803,
                                         65804)] == [
        "direct[0]", "direct[11]", "single[0]", "single[8]", "single[255]", "double[0][0]",
        "double[1][61]", "double[1][62]", "double[1][76]", "double[2][196]",
        "double[255][255]", "triple[0][0][0]"]


# ---------------------------------------------------------------- Problem 1: path translation
def test_1a_main_c_is_nine_reads_in_the_tutorials_order(fs):
    rows, end = F.open_call(fs, "/users/raj/lab/main.c")
    assert reads(rows) == [2, 40, 3, 41, 4, 42, 6, 55, 8]
    assert end == 52


def test_1b_the_link_costs_10_reads_and_finds_i_node_20_in_the_buffer(fs):
    rows, end = F.open_call(fs, "/users/raj/m")
    assert F.counts(rows) == (10, 0)
    assert reads(rows) == [2, 40, 3, 41, 4, 42, 60, 6, 55, 8]
    assert [r.block for r in rows if r.op == "hit"][:1] == [4]
    assert end == 52


def test_1c_notes_is_7_reads_1d_a_link_to_notes_8_1e_a_two_block_users_10(fs):
    assert F.counts(F.open_call(fs, "/users/raj/notes")[0]) == (7, 0)
    assert F.counts(F.open_call(F.tutorial_link_to_notes(), "/users/raj/m")[0]) == (8, 0)
    rows, _ = F.open_call(F.tutorial_big_users(), "/users/raj/lab/main.c")
    assert F.counts(rows) == (10, 0)
    assert reads(rows)[:5] == [2, 40, 3, 41, 61]


# ---------------------------------------------------------------- Problem 2: holes
def test_2a_the_file_with_holes_is_601000_bytes_in_5_data_and_3_index_blocks(holes):
    hfs, h = holes
    ino = hfs.inodes[h]
    assert ino.size == 601000
    assert len(F.file_blocks(hfs, ino)) == 5
    assert len(F.index_blocks(hfs, ino)) == 3
    assert [fb for fb, _ in F.walk_data(hfs, ino)] == [0, 1, 97, 585, 586]
    assert sorted(hfs.blocks[ino.single].ptrs) == [85]
    assert sorted(hfs.blocks[ino.double].ptrs) == [1]


@pytest.mark.parametrize("off, fb, ptr, n, kind", [
    (1500, 1, "direct[1]", 1, "data"), (5000, 4, "direct[4]", 0, "hole"),
    (60000, 58, "single[46]", 1, "hole"), (300000, 292, "double[0][24]", 1, "hole"),
    (599500, 585, "double[1][61]", 3, "data"), (601000, 586, None, 0, "eof")])
def test_2b_each_1_byte_read_costs_what_the_tutorial_says(holes, off, fb, ptr, n, kind):
    hfs, h = holes
    r = F.read_call(hfs, h, off)
    assert (r["fb"], len(r["rows"]), r["result"]) == (fb, n, kind)
    if ptr:
        assert r["pointer"] == ptr


def test_2d_writes_into_holes_take_blocks_and_leave_the_size(holes):
    hfs, h = holes
    r1 = F.write_call(hfs, h, 5000, 1)
    assert [w for _, w in r1.alloc] == ["data"]
    assert r1.fs.inodes[h].size == 601000
    r2 = F.write_call(r1.fs, h, 300000, 1)
    assert [w for _, w in r2.alloc] == ["index", "data"]
    assert r2.fs.inodes[h].size == 601000


@pytest.mark.parametrize("off, n, size, nd, ni", [
    (0, 100, 100, 1, 0), (12288, 100, 12388, 1, 1), (12287, 2, 12289, 2, 1),
    (274432, 100, 274532, 1, 2), (1000000, 1, 1000001, 1, 2)])
def test_2e_one_write_into_a_new_file(fs, off, n, size, nd, ni):
    efs, e, _ = F.holes_file(fs, [(off, n)])
    ei = efs.inodes[e]
    assert (ei.size, len(F.file_blocks(efs, ei)), len(F.index_blocks(efs, ei))) == (size, nd, ni)


def test_2e_one_byte_at_1000000_hangs_under_double_2(fs):
    efs, e, _ = F.holes_file(fs, [(1000000, 1)])
    assert sorted(efs.blocks[efs.inodes[e].double].ptrs) == [2]


# ---------------------------------------------------------------- Problem 3: appending to notes
def test_3a_3000_bytes_touch_file_blocks_11_to_14_and_take_61_first_as_the_index(fs):
    res = F.write_call(fs, 42, 0, 3000, append=True)
    assert [(d["fb"], d["status"]) for d in res.plan] == [
        (11, "partial"), (12, "new"), (13, "new"), (14, "new")]
    assert res.alloc == [(61, "index"), (62, "data"), (63, "data"), (64, "data")]


def test_3b_the_trace_row_by_row_is_3_reads_and_7_writes(fs):
    res = F.write_call(fs, 42, 0, 3000, append=True)
    assert trace(res) == [
        ("data 54", "R"), ("data 54", "W"), ("superblock", "R"), ("superblock", "W"),
        ("data 62", "W"), ("data 63", "W"), ("data 64", "W"), ("data 61", "W"),
        ("i-node block 7", "R"), ("i-node block 7", "W")]
    assert F.counts(res.rows) == (3, 7)
    assert res.rows[0].why == "file block 11 changes in part: keep its first 236 bytes"
    assert res.rows[-1].why == "i-node 42: size 14,500, single = 61"


def test_3b_other_orders_are_judged_by_the_partial_order(fs):
    res = F.write_call(fs, 42, 0, 3000, append=True)
    assert F.check_step(res, [], (62, "W"))[0] is False
    assert F.check_step(res, [(54, "R"), (54, "W"), (1, "R"), (1, "W"), (62, "W"), (63, "W")],
                        (61, "W"))[0] is False
    assert F.check_step(res, [(1, "R")], (62, "W"))[0] is True
    assert F.check_step(res, [], (7, "R"))[0] is True
    assert F.check_step(res, [], (62, "R"))[1].startswith("Block 62 is new")
    assert len(F.linear_extensions(res)) > 1


def test_3b_the_tutorials_append_has_11070_acceptable_orders(fs):
    res = F.write_call(fs, 42, 0, 3000, append=True)
    assert len(F.linear_extensions(res)) == 11070


def test_3c_index_last_takes_the_data_first_and_costs_the_same(fs):
    resl = F.write_call(fs, 42, 0, 3000, append=True, index_first=False)
    assert resl.alloc == [(61, "data"), (62, "data"), (63, "data"), (64, "index")]
    assert F.counts(resl.rows) == (3, 7)
    assert resl.rows[-1].why == "i-node 42: size 14,500, single = 64"


def test_3d_788_bytes_fit_in_the_last_block_789_spill_over(fs):
    r788 = F.write_call(fs, 42, 0, 788, append=True)
    assert trace(r788) == [("data 54", "R"), ("data 54", "W"), ("i-node block 7", "R"),
                           ("i-node block 7", "W")]
    r789 = F.write_call(fs, 42, 0, 789, append=True)
    assert trace(r789) == [("data 54", "R"), ("data 54", "W"), ("superblock", "R"),
                           ("superblock", "W"), ("data 62", "W"), ("data 61", "W"),
                           ("i-node block 7", "R"), ("i-node block 7", "W")]
    assert F.counts(r789.rows) == (3, 5)


def test_3e_overwrites_without_o_append(fs):
    r = F.write_call(fs, 42, 1024, 1024)
    assert trace(r) == [("data 44", "W")]
    assert F.check_step(r, [], (7, "W"))[1].startswith("i-node 42 does not change")
    assert F.check_step(r, [], (1, "R"))[1].startswith("Nothing is allocated")
    r = F.write_call(fs, 42, 1000, 1024)
    assert trace(r) == [("data 43", "R"), ("data 43", "W"), ("data 44", "R"), ("data 44", "W")]


# ---------------------------------------------------------------- the second tree ("Yours")
def test_yours_opens_share_i_node_blocks_and_search_both_blocks_of_courses():
    ys = F.yours_fs()
    assert F.counts(F.open_call(ys, "/etc/motd")[0]) == (4, 0)
    rows, _ = F.open_call(ys, "/courses/os/labs/fs.c")
    assert reads(rows) == [2, 34, 5, 37, 38, 39, 7, 40, 8]
    rows, _ = F.open_call(ys, "/courses/os/t4")
    assert reads(rows) == [2, 34, 5, 37, 38, 39, 44, 7, 40, 8]


def test_yours_append_reads_the_single_indirect_block_first():
    ys = F.yours_fs()
    res = F.write_call(ys, 60, 0, 2000, append=True)
    assert trace(res) == [
        ("data 57", "R"), ("data 65", "R"), ("data 65", "W"), ("superblock", "R"),
        ("superblock", "W"), ("data 66", "W"), ("data 67", "W"), ("data 57", "W"),
        ("i-node block 9", "R"), ("i-node block 9", "W")]
    assert F.counts(res.rows) == (4, 6)
    assert F.check_step(res, [], (65, "R"))[0] is False


def test_yours_file_with_holes_and_its_read_costs():
    ys = F.yours_fs()
    yfs, yh, _ = F.holes_file(ys, F.YOURS_HOLES, path="/courses/os/labs/h", inum=61)
    yi = yfs.inodes[yh]
    assert (yi.size, len(F.file_blocks(yfs, yi)), len(F.index_blocks(yfs, yi))) == (800010, 6, 4)
    assert [(F.read_call(yfs, yh, o)["result"], len(F.read_call(yfs, yh, o)["rows"]))
            for o in (600000, 290000, 280100, 400000)] == [
        ("hole", 1), ("hole", 2), ("data", 3), ("hole", 2)]


# ---------------------------------------------------------------- invariants on random cases
@pytest.mark.parametrize("seed", range(60))
def test_on_random_trees_the_canonical_order_passes_and_no_block_is_touched_twice(seed):
    rf = F.random_fs(seed)
    for row in F.every_path(rf):
        assert row["reads"] >= 1
    ino = next(i for i in rf.inodes.values() if i.kind == "file")
    for n in (1, 500, 1024, 3000, 13000):
        res = F.write_call(rf, ino.num, 0, n, append=True)
        done = []
        for r in res.rows:
            ok, msg = F.check_step(res, done, (r.block, r.op))
            assert ok, (seed, n, r, msg)
            done.append((r.block, r.op))
        assert len(done) == len(set(done))


@pytest.mark.parametrize("off, n", [(0, 1), (5000, 9000), (270000, 9000), (67383290, 20)])
def test_every_write_of_a_new_file_passes_in_order_and_fails_with_a_dependency_reversed(fs, off,
                                                                                      n):
    efs, e, _ = F.holes_file(fs, [])
    res = F.write_call(efs, e, off, n)
    done = []
    for r in res.rows:
        assert F.check_step(res, done, (r.block, r.op))[0], (off, n, r)
        done.append((r.block, r.op))
    for d in res.deps:
        assert not F.check_step(res, [], d.after)[0] or d.before == d.after


# ---------------------------------------------------------------- the level file
def _same_disk(a, b):
    a, b = a.to_json(), b.to_json()
    a.pop("name"), b.pop("name")
    return a == b


def test_the_shipped_level_file_loads_through_the_catalog_onto_the_engines_disks(lab):
    assert lab.engine == "fs" and lab.released
    assert lab.summary == ("Walk a path, follow the pointers to byte n, make a file with holes, "
                           "and count every block a write reads and writes.")
    assert _same_disk(lab.disks["tutorial"], F.tutorial_fs())
    assert _same_disk(lab.disks["yours"], F.yours_fs())


def test_the_level_file_has_the_four_levels_their_starts_and_their_goals(lab):
    assert [(lv.id, lv.call, lv.every) for lv in lab.levels] == [
        ("walk", "open", "Try every path"), ("byte", "read", "Try every block"),
        ("make", "write", "Try every block"), ("append", "write", "Try every length")]
    assert [[s.label for s in lv.starts] for lv in lab.levels] == [
        ["Tutorial", "Yours", "Random"], ["Tutorial", "Yours", "Random"], ["New file"],
        ["Tutorial", "Yours", "Random"]]
    assert [[g.id for g in lv.goals] for lv in lab.levels] == [
        ["main", "link", "two"], ["three", "free", "costly", "eof"], ["thin", "levels"],
        ["nonew", "index", "noread"]]
    walk, byte, make, append = lab.levels
    assert walk.start("Yours").path == "/courses/os/labs/fs.c"
    assert byte.start("Tutorial").offsets == (1500, 5000, 60000, 300000, 599500, 601000)
    assert byte.start("Yours").new_file.writes == tuple(F.YOURS_HOLES)
    assert make.start("New file").presets[4] == (1000000, 1)
    assert append.start("Tutorial").writes == (
        ("append", 3000, 0), ("append", 788, 0), ("append", 789, 0), ("at", 1024, 1024),
        ("at", 1024, 1000))
    assert append.index_first


def test_the_level_files_problem_2_file_is_the_tutorials(lab):
    w = F.start_world(lab, "byte", "Tutorial")
    assert (w.fs.inodes[w.inum].size, len(F.index_blocks(w.fs, w.fs.inodes[w.inum]))) == (
        601000, 3)
    assert w.offset == 300000


def test_on_the_level_files_yours_disk_1024_at_13312_must_read_the_single_indirect_block(lab):
    ys = lab.disks["yours"]
    r = F.write_call(ys, 60, 13312, 1024)
    assert trace(r) == [("data 57", "R"), ("data 59", "W")]
    assert F.counts(F.write_call(ys, 60, 0, 480, append=True).rows) == (3, 2)
    assert F.counts(F.write_call(ys, 60, 0, 481, append=True).rows) == (4, 5)


def test_the_level_files_disks_are_never_changed_by_a_world(lab):
    before = lab.disks["tutorial"].to_json()
    w = F.start_world(lab, "make", "New file")
    F.write_call(w.fs, w.inum, 0, 100)
    assert "/users/raj/new" not in [i.path for i in lab.disks["tutorial"].inodes.values()]
    assert lab.disks["tutorial"].to_json() == before


@pytest.mark.parametrize("patch, says", [
    ({"levels": [{"id": "walk", "call": "open",
                  "starts": [{"label": "T", "disk": "tutorial", "path": "/users/nobody"}]}]},
     "opens /users/nobody, which the disk cannot walk"),
    ({"levels": [{"id": "append", "call": "write",
                  "starts": [{"label": "T", "disk": "tutorial", "file": "/users/raj/notes",
                              "writes": [{"at": 0, "bytes": 0}]}]}]},
     "has a write of 0 bytes at 0 that the engine cannot do"),
    ({"levels": [{"id": "byte", "call": "read",
                  "starts": [{"label": "T", "disk": "tutorial",
                              "new_file": {"path": "/users/raj/h", "inode": 23, "writes": []},
                              "offsets": [-5]}]}]},
     "makes a file the engine cannot"),
    ({"levels": [{"id": "walk", "call": "open",
                  "starts": [{"label": "T", "disk": "nowhere", "path": "/"}]}]},
     "names a disk 'nowhere' that the file does not have"),
])
def test_a_start_the_engine_cannot_do_is_refused_in_one_sentence(patch, says):
    import yaml
    d = yaml.safe_load(TC.files("fs")[0].read_text(encoding="utf-8"))
    d.update(patch)
    with pytest.raises(F.LevelError, match=says) as e:
        F.from_dict(d)
    assert "\n" not in str(e.value)


# ---------------------------------------------------------------- every goal, as the window judges
def test_every_goal_is_reachable_through_the_replays(fs, holes, lab):
    walk, byte, make, append = lab.levels
    w = F.walk_clicks(fs, "/users/raj/lab/main.c", [2, 40, 3, 41, 4, 42, 6, 55, 8])
    assert (w["complete"], w["wrong"], w["reads"]) == (True, 0, 9)
    assert "main" in F.walk_goals(walk, "Tutorial", "/users/raj/lab/main.c", w)
    w = F.walk_clicks(fs, "/users/raj/m", [2, 40, 3, 41, 4, 42, 60, 6, 55, 8])
    assert (w["complete"], w["link"]) == (True, True)
    assert F.walk_goals(walk, "Tutorial", "/users/raj/m", w) == ("link",)
    w = F.walk_clicks(fs, "/users/raj/m", [2, 40, 3, 41, 4, 42, 4, 60, 2, 40, 3, 41, 4, 42, 6, 55,
                                           8])
    assert w["complete"] is True
    assert F.walk_clicks(fs, "/users/raj/lab/main.c", [2, 40, 19])["wrong"] == 1
    ys = lab.disks["yours"]
    w = F.walk_clicks(ys, "/courses/os/labs/fs.c", [2, 34, 5, 37, 38, 39, 7, 40, 8])
    assert (w["complete"], w["second_block"]) == (True, True)
    assert F.walk_goals(walk, "Yours", "/courses/os/labs/fs.c", w) == ("two",)

    hfs, h = holes
    assert F.pointer_clicks(hfs, h, 599500, ["double", 1, 61]) == {
        "complete": True, "wrong": 0, "result": "data", "reads": 3}
    assert F.read_goals(byte, "data", 3) == ("three",)
    assert F.pointer_clicks(hfs, h, 5000, ["direct[4]"])["result"] == "hole"
    assert F.read_goals(byte, "hole", 0) == ("free",)
    assert F.pointer_clicks(hfs, h, 300000, ["single", "double", 0]) == {
        "complete": True, "wrong": 1, "result": "hole", "reads": 1}
    assert F.read_goals(byte, "hole", 1) == ("costly",)
    assert F.pointer_clicks(hfs, h, 601000, ["eof"])["complete"] is True
    assert F.read_goals(byte, "eof", 0) == ("eof",)

    efs, e, _ = F.holes_file(fs, [(1000000, 1)])
    ei = efs.inodes[e]
    assert ei.size >= 1000000 and len(F.file_blocks(efs, ei)) + len(F.index_blocks(efs, ei)) <= 3
    assert F.shape_goals(make, efs, e) == ("thin",)
    efs, e, _ = F.holes_file(fs, [(1000000, 1), (0, 100), (12288, 100)])
    assert {F.region(fb) for fb, _ in F.walk_data(efs, efs.inodes[e])} == {
        "direct", "single", "double"}
    assert F.shape_goals(make, efs, e) == ("levels",)

    for n, off, app, want in [(788, 0, True, "nonew"), (3000, 0, True, "index"),
                              (1024, 1024, False, "noread")]:
        res = F.write_call(fs, 42, off, n, append=app)
        tr = F.trace_steps(res, [(62, "W")] + [(r.block, r.op) for r in res.rows])
        assert tr["complete"] is True, want
        assert F.trace_goals(append, res) == (want,)


def test_the_yours_trap_is_a_whole_block_overwrite_that_still_reads(lab):
    res = F.write_call(lab.disks["yours"], 60, 13312, 1024)
    assert F.trace_goals(lab.level("append"), res) == ("nonew",)


def test_only_past_the_end_and_the_shape_goals_forgive_show_me(lab):
    forgiving = {g.id for lv in lab.levels for g in lv.goals if not g.needs_no_hints}
    assert forgiving == {"eof", "thin", "levels"}


# ---------------------------------------------------------------- random worlds and the sweeps
@pytest.mark.parametrize("level_id", ["walk", "byte", "append"])
def test_a_random_start_is_rebuilt_exactly_from_its_seed(lab, level_id):
    a = F.start_world(lab, level_id, "Random", seed=4242)
    b = F.start_world(lab, level_id, "Random", seed=4242)
    assert a.fs.to_json() == b.fs.to_json()
    assert (a.path, a.inum, a.offsets, a.presets, a.writes) == (
        b.path, b.inum, b.offsets, b.presets, b.writes)
    assert a.seed == 4242


@pytest.mark.parametrize("seed", range(25))
def test_random_holes_spread_over_the_direct_single_and_double_ranges(fs, seed):
    ws = F.random_holes(seed)
    assert 3 <= len(ws) <= 4
    hfs, h, _ = F.holes_file(fs, ws)
    have = {F.region(fb) for fb, _ in F.walk_data(hfs, hfs.inodes[h])}
    assert have == {"direct", "single", "double"}
    offs = F.random_offsets(hfs, h, seed)
    assert offs[-1] == hfs.inodes[h].size and len(offs) >= 8


@pytest.mark.parametrize("off, append", [(None, True), (1000, False), (11000, False)])
def test_every_length_gives_the_one_call_per_length_answer(fs, off, append):
    fast = F.every_length(fs, 42, 2200, offset=off, append=append)
    slow = [(n,) + F.counts(F.write_call(fs, 42, off or 0, n, append=append).rows)
            for n in range(1, 2201)]
    assert fast == slow


def test_the_tutorials_append_intervals_are_788_then_789_on(fs):
    iv = F.intervals(F.every_length(fs, 42, 3000))
    assert iv[0] == (1, 788, 2, 2)
    assert iv[1][0] == 789 and iv[1][2:] == (3, 5)


def test_blocks_to_take_is_alloc_cost_without_the_write(fs):
    efs, e, _ = F.holes_file(fs, [(0, 100), (300000, 5), (1000000, 1)])
    for fb in list(range(0, 1100, 3)) + [F.T0, F.T0 + 70000]:
        assert F.blocks_to_take(efs, e, fb) == F.alloc_cost(efs, e, fb), fb

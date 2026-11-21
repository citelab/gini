"""T-Lab 04's view model and session, asserted without a widget.

Everything the face draws comes out of pure functions over the engine's results, and `Session`
holds what the student chose and judges each click. So the sentences a wrong click gets, the
buffer-hit rows, the badges on the disk map, the pointer refusals, the plan, the file strip, the
Try every tallies and — above all — when a goal is reached and what the chain is told, are all
checked here. `test_fs_lab_face.py` checks the window draws them, fits, and wires its handlers.
"""
from __future__ import annotations

import random

import pytest
from gini.domain import fslab as F

from gini.ui import fs_lab as V

MAIN = [2, 40, 3, 41, 4, 42, 6, 55, 8]


@pytest.fixture(scope="module")
def lab():
    lab = F.get("t-lab-04")
    assert lab is not None
    return lab


@pytest.fixture
def events():
    return []


@pytest.fixture
def s(lab, events):
    return V.Session(lab, on_event=lambda k, p: events.append((k, p)), rng=random.Random(7))


def level(s, lid: str) -> F.Level:
    s.pick_level(next(i for i, lv in enumerate(s.lab.levels) if lv.id == lid))
    return s.level


def start(s, label: str) -> None:
    s.pick_start(next(i for i, st in enumerate(s.level.starts) if st.label == label))


# ---- what a block holds -------------------------------------------------------------- #
def test_the_strip_says_what_an_i_node_block_holds_in_the_designs_words(lab):
    fs = lab.disk("tutorial")
    assert " ".join(V.block_info(fs, 4)) == (
        "i-node block 4 holds i-nodes 16–23 (2 + ⌊i/8⌋ = 4). 19 /users/raj (dir, 80 bytes) · "
        "20 /users/raj/m (symbolic link)")
    assert " ".join(V.block_info(fs, 42)) == (
        "Block 42 · /users/raj, directory block 0, entries 0–4: . 19 · .. 10 · notes 42 · "
        "lab 37 · m 20.")
    assert V.block_info(fs, 1)[1].startswith("The free-block list (61, 62, 63, …")
    assert " ".join(V.block_info(fs, 54)) == (
        "Block 54 · /users/raj/notes, file block 11: bytes 11,264–11,499 (236 bytes; the rest "
        "of the block is unused).")
    assert V.block_info(fs, 60)[1] == "a symbolic link: it holds the path /users/raj/lab/main.c."
    assert V.block_info(fs, 35) == ("Block 35", "is in use by files that are not in the table.")
    assert V.block_info(fs, 70) == ("Block 70", "is free.")


def test_block_kinds_mark_an_i_node_block_in_use_darker(lab):
    fs = lab.disk("tutorial")
    assert [V.kind_of(fs, b) for b in (0, 1, 2, 5, 40, 43, 60, 34, 61)] == [
        "boot", "super", "inode2", "inode", "dir", "data", "link", "other", "free"]
    assert V.disk_blocks(fs) == 80
    assert V.disk_blocks(fs, marks=(95,)) == 100


def test_numbers_are_typed_with_or_without_separators():
    assert [V.parse_num(x) for x in ("300,000", "300 000", "300_000", "12", "x", "")] == [
        300000, 300000, 300000, 12, None, None]


# ---- level 1 ----------------------------------------------------------------------- #
def test_level_1_opens_on_the_tutorials_main_c(s):
    assert s.level.id == "walk" and s.start.label == "Tutorial"
    assert s.walk.path == "/users/raj/lab/main.c"
    assert s.tab == "table"
    assert not s.can_undo()


def test_a_wrong_click_names_what_is_needed_and_the_formula_never_the_answer(s, events):
    s.walk_click(2)
    s.walk_click(40)
    s.walk_click(3)
    s.walk_click(41)
    before = s.walk_done()
    s.walk_click(19)
    assert s.walk_done() == before, "a wrong click changes nothing"
    assert s.walk.msg == ("Not block 19. The kernel needs i-node 19, of /users/raj next. "
                          "i-node 19 is in block 2 + ⌊19/8⌋.")
    assert "block 4" not in s.walk.msg
    assert s.inspect == 19, "the strip shows what the clicked block is"
    s.walk_click(5)
    assert "Its i-node" not in s.walk.msg and s.walk.msg.startswith("Not block 5.")
    s.walk_click(4)
    s.walk_click(9)
    assert s.walk.msg.endswith("Its i-node, already read, lists the block.")
    assert events == []


def test_main_c_walked_in_order_reaches_its_goal_once_with_the_chains_fields(s, events):
    for b in [2, 40, 3, 41, 4, 99] + MAIN[5:]:
        s.walk_click(b)
    assert s.walk_complete()
    assert events == [("fs_walk", {
        "lab": "t-lab-04", "level": "walk", "start": "Tutorial", "seed": None,
        "path": "/users/raj/lab/main.c", "clicks": [2, 40, 3, 41, 4, 99, 42, 6, 55, 8],
        "hints": 0, "goal": "main"})]
    assert "walk:main" in s.achieved
    w = F.walk_clicks(s.world().fs, s.walk.path, events[0][1]["clicks"])
    assert (w["complete"], w["wrong"]) == (True, 1), "the chain's clicks replay as they were"
    s.reset()
    for b in MAIN:
        s.walk_click(b)
    assert len(events) == 1, "a goal is reached once"


def test_the_link_passes_over_buffer_hits_with_a_sentence_and_shows_them_as_no_read_rows(s,
                                                                                         events):
    s.pick_path("/users/raj/m")
    for b in [2, 40, 3, 41, 4, 42, 60]:
        s.walk_click(b)
    assert s.walk.ok == ("Block 4 was already in the buffer (rule 3), so the next read is "
                         "block 60.")
    rows = s.walk_rows()
    s.walk_click(6)
    assert s.walk.ok == ("Blocks 2, 40, 3, 41, 4, 42 were already in the buffer (rule 3), so "
                         "the next read is block 6.")
    table = V.walk_table(rows, s.walk_done())
    hits = [t for t in table if t.kind == "hit"]
    assert hits and all(t.n == "" for t in hits)
    assert hits[0].why.startswith("no read, already in this call’s buffer (rule 3). ")
    assert [t.n for t in table if t.kind == "R"] == [str(i) for i in range(1, 9)]
    for b in (55, 8):
        s.walk_click(b)
    assert [e[1]["goal"] for e in events] == ["link"]
    count, rest = V.walk_totals(rows, s.walk_done(), 0, 0)
    assert count == "10 R" and rest.startswith("7 found in the buffer · 0 wrong clicks")


def test_clicking_the_next_hit_itself_is_a_grey_row(s):
    s.pick_path("/users/raj/m")
    for b in [2, 40, 3, 41, 4, 42]:
        s.walk_click(b)
    s.walk_click(4)
    assert s.walk.ok == "Block 4 is already in this call’s buffer: no read (rule 3)."
    assert V.walk_table(s.walk_rows(), s.walk_done())[-2].kind == "hit"


def test_the_second_directory_block_is_a_yours_goal(s, events):
    start(s, "Yours")
    assert s.walk.path == "/courses/os/labs/fs.c"
    for b in [2, 34, 5, 37, 38, 39, 7, 40, 8]:
        s.walk_click(b)
    assert [(k, p["goal"], p["start"]) for k, p in events] == [("fs_walk", "two", "Yours")]


def test_show_me_makes_a_walk_ineligible_for_its_goal(s, events):
    assert s.walk_show()
    assert s.inspect == 2 and s.walk.hints == 1
    for b in MAIN[1:]:
        s.walk_click(b)
    assert s.walk_complete() and events == []
    assert V.walk_totals(s.walk_rows(), s.walk_done(), 0, 1)[1].endswith("Show me used 1 time")


def test_watch_steps_are_show_mes_and_stop_at_the_end(s, events):
    n = 0
    while s.watch_step():
        n += 1
    assert n == 9 and s.walk.hints == 9 and s.walk_complete() and events == []


def test_undo_takes_back_the_last_block_that_moved_the_walk(s):
    for b in (2, 40, 77, 3):
        s.walk_click(b)
    assert s.walk_done() == 3
    s.undo()
    assert s.walk_done() == 2 and s.walk.clicks == [2, 40, 77]
    s.undo()
    assert s.walk_done() == 1 and s.walk.clicks == [2]
    s.pick_path("/users/raj/m")
    for b in [2, 40, 3, 41, 4, 42, 60, 6]:
        s.walk_click(b)
    assert s.walk_done() == 15
    s.undo()
    assert s.walk_done() == 8, "the passed-over hits go back with the read that passed them"


def test_the_table_tab_can_hide_the_i_node_blocks(s):
    assert not s.walk.hide_ib
    s.toggle_hide_ib()
    s.pick_path("/users/raj/notes")
    assert s.walk.hide_ib, "practice mode survives a new path"


def test_try_every_path_opens_every_name_cold(s):
    s.try_every()
    assert s.tab == "every"
    assert [(e["path"], e["reads"]) for e in s.walk.every] == [
        ("/", 1), ("/users", 3), ("/users/raj", 5), ("/users/raj/notes", 7),
        ("/users/raj/lab", 7), ("/users/raj/lab/main.c", 9), ("/users/raj/m", 10)]


def test_a_random_walk_records_its_seed_and_rebuilds_from_it(s, events, lab):
    s.random()
    assert s.start.label == "Random"
    seed = s.seed()
    w = s.world()
    assert w.seed == seed
    for r in [r for r in s.walk_rows() if r.op == "R"]:
        s.walk_click(r.block)
    assert s.walk_complete()
    rebuilt = F.start_world(lab, "walk", "Random", seed)
    assert rebuilt.path == s.walk.path
    for _k, p in events:
        assert p["seed"] == seed and p["start"] == "Random"
        assert F.walk_clicks(rebuilt.fs, p["path"], p["clicks"])["complete"]


# ---- level 2 ----------------------------------------------------------------------- #
def test_level_2_opens_on_problem_2s_file_at_300000(s):
    level(s, "byte")
    assert s.byte.offset == 300000 and s.file_size() == 601000
    assert s.world().offsets == (1500, 5000, 60000, 300000, 599500, 601000)
    assert s.tab == "file"


def test_three_reads_through_the_double_indirect_block(s, events):
    level(s, "byte")
    s.set_offset("599,500")
    s.click_slot("double")
    assert s.byte.ok == "The kernel reads block 65: 1 read so far. Which entry?"
    s.click_entry(65, 1)
    s.click_entry(66, 61)
    assert s.byte.stage == V.DONE
    assert s.byte.ok == "double[1][61] → block 67: 3 reads, 2 index blocks and the data block."
    assert events == [("fs_read", {
        "lab": "t-lab-04", "level": "byte", "start": "Tutorial", "seed": None, "offset": 599500,
        "clicks": ["double", 1, 61], "hints": 0, "goal": "three"})]
    assert V.read_log_cells(s.byte.log[-1]) == ("599,500", "585", "double[1][61]", "3")


def test_a_wrong_pointer_says_what_it_serves_and_counts_in_the_replay(s, events):
    level(s, "byte")
    s.set_offset(60000)
    s.click_slot("double")
    assert s.byte.msg == ("Not that pointer. double serves file blocks 268–65,803: bytes "
                          "274,432–67,383,295.")
    assert s.byte.stage == 0
    s.click_slot("single")
    s.click_entry(63, 40)
    assert s.byte.msg == "Not that entry. single[40] serves file block 52: bytes 53,248–54,271."
    s.click_entry(99, 46)
    assert s.byte.msg == "The kernel is reading block 63 now. Click an entry in that block."
    s.click_entry(63, 46)
    assert s.byte.ok == "A hole: that entry is 0. The read returns a zero after 1 read."
    _k, p = events[-1]
    assert p["goal"] == "costly" and p["clicks"] == ["double", "single", 40, 46]
    assert F.pointer_clicks(s.world().fs, s.world().inum, 60000, p["clicks"]) == {
        "complete": True, "wrong": 2, "result": "hole", "reads": 1}


def test_a_hole_in_the_i_node_is_free(s, events):
    level(s, "byte")
    s.set_offset(5000)
    s.click_entry(63, 0)
    assert s.byte.msg == "Start in the i-node: which of its pointers serves this offset?"
    s.click_slot("direct[4]")
    assert s.byte.ok == "A hole: direct[4] is 0. The read returns a zero after 0 reads."
    assert [p["goal"] for _k, p in events] == ["free"]


def test_past_the_end_is_said_only_with_the_button(s, events):
    level(s, "byte")
    s.press_eof()
    assert s.byte.msg == ("Not past the end: the size is 601,000, so offset 300,000 is inside "
                          "the file.")
    s.set_offset(601000)
    s.click_slot("double")
    assert s.byte.msg == "Look at the size first: the i-node says 601,000 bytes."
    s.press_eof()
    assert s.byte.ok.startswith("Offset 601,000 is at or past the end (size 601,000)")
    assert events[-1][1]["goal"] == "eof" and events[-1][1]["clicks"] == ["double", "eof"]


def test_show_me_reads_the_byte_but_reaches_nothing(s, events):
    level(s, "byte")
    s.set_offset(599500)
    s.byte_show()
    assert s.byte.stage == V.DONE and s.byte.log[-1].shown
    s.set_offset(601000)
    s.byte_show()
    assert events == [], "Show me says past the end; the goal is for pressing the button"


def test_undo_steps_back_inside_a_read_then_takes_a_finished_read_off_the_table(s):
    level(s, "byte")
    s.set_offset(599500)
    s.click_slot("double")
    s.click_entry(65, 7)
    s.click_entry(65, 1)
    assert s.byte.stage == 2
    s.undo()
    assert s.byte.stage == 1
    assert s.byte.clicks == [("double", True), (7, False)], "the wrong click before it stays"
    s.click_entry(65, 1)
    s.click_entry(66, 61)
    assert len(s.byte.log) == 1
    s.undo()
    assert s.byte.log == [] and s.byte.stage == 0


def test_try_every_block_counts_each_kind_of_read(s):
    level(s, "byte")
    s.try_every()
    assert V.byte_legend(s.byte.every) == (
        ("data", 1, 2), ("data", 2, 1), ("data", 3, 2), ("hole", 0, 10), ("hole", 1, 511),
        ("hole", 2, 61))
    assert [b.title for b in V.bands(586)] == [
        "direct · file blocks 0–11", "single · 12–267", "double[0] · 268–523",
        "double[1] · 524–779"]


def test_a_random_file_spreads_over_three_ranges_and_records_its_seed(s, events):
    level(s, "byte")
    s.random()
    w = s.world()
    assert w.seed == s.seed() and len(w.offsets) >= 8 and w.offsets[-1] == s.file_size()
    s.set_offset(w.offsets[-1])
    s.press_eof()
    assert events[-1][1]["seed"] == w.seed


def test_the_text_for_a_pointer_and_an_entry():
    assert V.slot_text("direct[4]") == "direct[4] serves file block 4: bytes 4,096–5,119."
    assert V.serves_text(("single", []), 46) == (
        "single[46] serves file block 58: bytes 59,392–60,415.")
    assert V.serves_text(("double", []), 1) == (
        "double[1] serves file blocks 524–779: bytes 536,576–798,719.")
    assert V.serves_text(("double", [1]), 61) == (
        "double[1][61] serves file block 585: bytes 599,040–600,063.")


# ---- level 3 ----------------------------------------------------------------------- #
def test_big_and_thin_and_all_three_ranges_are_made_by_writing(s, events):
    level(s, "make")
    assert s.make_write(1000000, 1)
    _fs, results = s.make_state()
    assert V.write_summary(results[-1], 1000000, 1) == (
        "Bytes 1,000,000–1,000,000: file block 976 (double[2][196]). Took 3 blocks: 61 (index), "
        "62 (index), 63.")
    assert events == [("fs_make", {"lab": "t-lab-04", "level": "make",
                                   "writes": [[1000000, 1]], "goal": "thin"})]
    s.make_write("0", "100")
    s.make_write("12,288", "100")
    assert events[-1][1] == {"lab": "t-lab-04", "level": "make",
                             "writes": [[1000000, 1], [0, 100], [12288, 100]],
                             "goal": "levels"}


def test_a_write_the_level_will_not_take_is_refused_in_a_sentence(s):
    level(s, "make")
    assert not s.make_write(0, 0)
    assert s.make.msg == "Give an offset of 0 or more and a length of at least 1 byte."
    assert not s.make_write(0, 400000)
    assert s.make.msg == "Keep a single write to 300,000 bytes or less here."
    assert not s.make_write(F.TEND * F.BS, 1)
    assert s.make.msg == "That is past the largest file this i-node can describe."
    assert s.make.writes == []


def test_try_every_block_on_a_new_file_shows_what_a_write_would_take(s):
    level(s, "make")
    s.make_write(0, 100)
    s.try_every()
    ev = s.make.every
    assert ev[0] == 0 and ev[1] == 1 and ev[12] == 2 and ev[300] == 3 and ev["triple"] == 4
    assert len([k for k in ev if k != "triple"]) == 12 + 256 + 3 * 256


def test_undo_and_reset_on_level_3(s):
    level(s, "make")
    s.make_write(0, 100)
    s.make_write(12288, 100)
    s.undo()
    assert s.make.writes == [(0, 100)]
    s.reset()
    assert s.make.writes == [] and not s.can_undo()


def test_random_on_level_3_is_a_write_from_the_sessions_generator(s):
    level(s, "make")
    s.random()
    assert len(s.make.writes) == 1


# ---- level 4 ----------------------------------------------------------------------- #
def build(s, steps):
    for b, op in steps:
        s.select_block(b)
        s.commit(op)


def test_level_4_opens_on_the_3000_byte_append(s):
    level(s, "append")
    assert (s.append.op, s.append.n) == ("append", 3000)
    assert s.tab == "plan" and not s.append.plan


def test_a_refused_step_says_why_and_changes_nothing(s, events):
    level(s, "append")
    build(s, [(62, "W")])
    assert s.append.msg == ("Not yet: read the superblock first: that is where the kernel learns "
                            "that block 62 is free (rule 5).")
    assert s.append.done == [] and s.append.refused == 1
    build(s, [(62, "R")])
    assert s.append.msg == ("Block 62 is new: there is nothing on it to keep, so it is written "
                            "without a read (rule 4).")
    build(s, [(40, "R")])
    assert s.append.msg == "This call does not touch data 40."
    assert events == []


def test_the_index_block_first_trace_built_by_hand_reaches_its_goal_once(s, events):
    level(s, "append")
    res = s.write_res()
    steps = [(62, "W")] + [(r.block, r.op) for r in res.rows]
    build(s, steps)
    assert s.append_complete()
    assert events == [("fs_trace", {
        "lab": "t-lab-04", "level": "append", "start": "Tutorial", "seed": None, "op": "append",
        "offset": 11500, "n": 3000, "steps": [[b, o] for b, o in steps], "hints": 0,
        "goal": "index"})]
    assert F.trace_steps(res, events[0][1]["steps"])["complete"]
    marks = V.trace_marks(s.append.done)
    assert marks[54] == V.Mark(True, True, "1·2")
    assert marks[62] == V.Mark(False, True, "W5")
    assert next(t.why for t in V.trace_table(res, s.append.done)) == (
        "file block 11 changes in part: keep its first 236 bytes")


@pytest.mark.parametrize("preset, goal", [(("append", 788, 0), "nonew"),
                                          (("at", 1024, 1024), "noread")])
def test_the_tutorials_presets_reach_the_other_two_goals(s, events, preset, goal):
    level(s, "append")
    assert s.set_write(*preset)
    build(s, [(r.block, r.op) for r in s.write_res().rows])
    assert [p["goal"] for _k, p in events] == [goal]
    assert events[0][1]["op"] == preset[0] and events[0][1]["n"] == preset[1]


def test_on_yours_the_whole_block_overwrite_still_reads_the_single_indirect_block(s, events):
    level(s, "append")
    start(s, "Yours")
    s.set_write("at", 1024, 13312)
    build(s, [(59, "W")])
    assert s.append.msg == ("Not yet: read data 57 first: the kernel finds the number of block 59 "
                            "in it.")
    build(s, [(57, "R"), (59, "W")])
    assert [p["goal"] for _k, p in events] == ["nonew"]


def test_show_me_and_watch_make_a_trace_ineligible(s, events):
    level(s, "append")
    s.set_write("append", 788)
    assert s.append_show()
    build(s, [(r.block, r.op) for r in s.write_res().rows[1:]])
    assert s.append_complete() and events == []
    s.set_write("append", 789)
    while s.watch_step():
        pass
    assert s.append_complete() and s.append.hints == 8 and events == []


def test_thats_all_counts_what_is_missing_and_points_at_rules_5_and_6(s):
    level(s, "append")
    build(s, [(54, "R"), (54, "W")])
    s.thats_all()
    assert s.append.msg == ("Not yet: 8 more block accesses to go. Check the superblock (rule 5) "
                            "and the i-node block (rule 6).")


def test_undo_takes_the_last_accepted_step_and_the_refusals_after_it(s):
    level(s, "append")
    build(s, [(54, "R"), (54, "W"), (62, "W")])
    assert len(s.append.steps) == 3 and len(s.append.done) == 2
    s.undo()
    assert s.append.done == [(54, "R")] and len(s.append.steps) == 1


def test_the_plan_answers_only_when_asked(s):
    level(s, "append")
    assert not s.append.plan
    s.show_plan()
    assert V.plan_rows(s.write_res()) == (
        ("11", "11,500–12,287", "exists (54), changed in part"),
        ("12", "12,288–13,311", "new → 62"), ("13", "13,312–14,335", "new → 63"),
        ("14", "14,336–14,499", "new → 64"))
    assert V.taken_text(s.write_res().alloc) == "61 (index), 62, 63, 64"
    s.set_write("append", 788)
    assert not s.append.plan, "a new write is worked out afresh"


def test_the_file_strip_shows_the_blocks_around_the_write(s):
    level(s, "append")
    w = s.world()
    cells = V.file_strip(w.fs, w.inum, s.write_res())
    assert [c.fb for c in cells] == [9, 10, 11, 12, 13, 14, 15]
    assert [c.pointer for c in cells] == ["d[9]", "d[10]", "d[11]", "s[0]", "s[1]", "s[2]",
                                          "s[3]"]
    assert [c.exists for c in cells] == [True, True, True, False, False, False, False]
    assert cells[2].used == 236 and cells[2].cover == (236 / 1024, 1.0)
    assert V.strip_pointer(292) == "[0][24]"


def test_a_typed_write_out_of_range_is_refused(s):
    level(s, "append")
    assert not s.set_write("append", 0)
    assert s.append.msg == "Use 1 to 20,000 bytes and an offset from 0 to 300,000 here."
    assert s.append.n == 3000


def test_try_every_length_finds_the_jumps_at_788_and_789(s):
    level(s, "append")
    s.try_every()
    ev = s.append.every
    assert ev["upto"] == 6000 and ev["iv"][0] == (1, 788, 2, 2) and ev["iv"][1][0] == 789


def test_a_random_append_records_its_seed(s, events, lab):
    level(s, "append")
    s.random()
    seed = s.seed()
    op, n, off = s.world().presets[1]          # fills the last block: takes nothing
    s.set_write(op, n, off)
    build(s, [(r.block, r.op) for r in s.write_res().rows])
    assert events and all(p["seed"] == seed and p["start"] == "Random" for _k, p in events)
    w = F.start_world(lab, "append", "Random", seed)
    p = events[0][1]
    res = F.write_call(w.fs, w.inum, p["offset"], p["n"], append=p["op"] == "append")
    assert F.trace_steps(res, p["steps"])["complete"]


# ---- the frame ---------------------------------------------------------------------- #
def test_each_level_keeps_its_own_state_across_a_level_switch(s):
    s.walk_click(2)
    level(s, "append")
    build(s, [(54, "R")])
    level(s, "walk")
    assert s.walk_done() == 1
    level(s, "append")
    assert s.append.done == [(54, "R")]


def test_goal_chips_say_done_or_not_and_carry_the_goal(s):
    for b in MAIN:
        s.walk_click(b)
    chips = V.goal_chips(s.level, s.achieved)
    assert [(c.id, c.done) for c in chips] == [("main", True), ("link", False), ("two", False)]
    assert chips[0].tooltip == "Open /users/raj/lab/main.c on the Tutorial disk without Show me."


def test_every_level_has_its_tabs():
    assert {k: [t for t, _ in v] for k, v in V.TABS.items()} == {
        "walk": ["table", "every"], "byte": ["file", "every"], "make": ["writes", "every"],
        "append": ["plan", "table", "every"]}

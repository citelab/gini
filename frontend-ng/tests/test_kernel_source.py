"""The GINI Source browser's pure half: which files a block owns, and the jump list.

The jump list is scanned out of the file being shown rather than read from a table, so it can
never drift away from the source on screen — which matters because the source comes from inside
the container and reflects the student's own shadow edits.
"""
from gini.domain.kernel_source import (
    Entry, SourceFile, entries_in, files_for, find_line, parse_source, safe_rel,
)

# a slice of the PATCHED bio.c, as the container serves it
BIO = """// Buffer cache.

#include "types.h"

struct buf*
bread(uint dev, uint blockno)
{
  GINI_SUB(GSUB_BCACHE);  // GINI-xv6: board probe bread
  struct buf *b;
  b = bget(dev, blockno);
  return b;
}

void
bwrite(struct buf *b)
{
  GINI_SUB(GSUB_BCACHE);  // GINI-xv6: board probe bwrite
  virtio_disk_rw(b, 1);
}
"""

# a file with no probes at all — a header, or an internal-only file
PLAIN = """// no probes here

static int
helper(void)
{
  return 1;
}

void
visible(int x)
{
  helper();
}
"""


def test_a_block_maps_to_its_files():
    assert files_for("bcache") == ["kernel/bio.c"]
    assert files_for("memory") == ["kernel/vm.c", "kernel/kalloc.c"]   # two files, in order
    assert files_for("nonsense") == []


def test_jump_list_is_the_probed_entry_points():
    """These are exactly the functions the board counts — 'the doors into this block'."""
    es = entries_in(BIO)
    assert [e.name for e in es] == ["bread", "bwrite"]
    assert all(e.block == "bcache" for e in es)


def test_entry_line_points_at_the_function_name():
    """The probe sits below the opening brace; the useful place to land is the name."""
    es = entries_in(BIO)
    lines = BIO.splitlines()
    assert lines[es[0].line - 1].startswith("bread(")
    assert lines[es[1].line - 1].startswith("bwrite(")


def test_a_file_without_probes_still_gets_an_outline():
    """Never show a student a wall of text with no way in."""
    es = entries_in(PLAIN)
    assert [e.name for e in es] == ["helper", "visible"]
    assert all(e.block == "" for e in es)                  # not a board block, and doesn't claim to
    lines = PLAIN.splitlines()
    assert lines[es[0].line - 1].startswith("helper(")


def test_duplicate_names_are_listed_once():
    assert len(entries_in(BIO + BIO)) == 2


def test_the_agents_refusals_are_errors_not_files():
    """The agent shapes refusals as C comments so they render harmlessly if this check is missed;
    the browser must still show them as errors rather than as a one-line source file."""
    for msg in ("// refused: outside the kernel tree", "// refused: not a source file",
                "// not found: kernel/nope.c", "// no file requested", "// unreadable: EACCES"):
        sf = parse_source("kernel/x.c", msg)
        assert not sf.ok and sf.entries == []
        assert sf.error and not sf.error.startswith("/")
    assert not SourceFile().ok                             # nothing served at all


def test_real_source_parses_and_counts():
    sf = parse_source("kernel/bio.c", BIO)
    assert sf.ok and not sf.error
    assert sf.lines == BIO.count("\n") + 1
    assert [e.name for e in sf.entries] == ["bread", "bwrite"]
    assert sf.entries[0].label.startswith("bread")


def test_paths_that_try_to_escape_are_refused_before_the_request_goes_out():
    """The agent refuses these too — that is the check that matters. This is the near side of the
    same door, so a malformed request never leaves the app."""
    assert safe_rel("kernel/bio.c") == "kernel/bio.c"
    assert safe_rel("kernel/../kernel/fs.c") == "kernel/fs.c"        # normalised, still inside
    for bad in ("../secrets.c", "kernel/../../etc/passwd", "/etc/passwd", "", "Makefile",
                "kernel/bio.txt", ".."):
        assert safe_rel(bad) == "", bad


def test_find_line_for_the_jump():
    assert find_line(BIO, "bwrite(struct buf") == 15
    assert find_line(BIO, "nothing here") == 0

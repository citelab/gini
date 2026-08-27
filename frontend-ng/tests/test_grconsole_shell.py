"""The gRouter console should behave like a shell.

Reported: "the gRouter shell does not have up/down arrow processing and history editing like a
normal shell." It was using bare input(), which has no line editing at all — no Up for the last
command, no Left/Right to fix a typo, no Ctrl-A. Importing readline is the whole fix: Python then
routes input() through it, and the keys already work because terminal_view sends the right escape
sequences.

These tests cover the parts that are logic rather than library: what Tab offers, and that Ctrl-C
abandons the line instead of killing the console. grconsole.py lives in the backend checkout, so
they skip cleanly where that is not present.
"""
import importlib.util
from pathlib import Path

import pytest

GRCONSOLE = (Path(__file__).resolve().parents[2] / "backend" / "grouter-build" / "grconsole.py")
if not GRCONSOLE.exists():
    pytest.skip("backend checkout not present", allow_module_level=True)


@pytest.fixture(scope="module")
def grc():
    spec = importlib.util.spec_from_file_location("grconsole_under_test", GRCONSOLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeReadline:
    """Stands in for readline so completion can be driven without a terminal."""

    def __init__(self, buf):
        self._buf = buf

    def get_line_buffer(self):
        return self._buf

    def get_endidx(self):
        return len(self._buf)


def _complete_all(grc, buf, text):
    grc._readline = _FakeReadline(buf)
    out, i = [], 0
    while True:
        hit = grc._complete(text, i)
        if hit is None:
            return out
        out.append(hit.strip())
        i += 1


def test_tab_completes_a_verb(grc):
    assert "route" in _complete_all(grc, "rou", "rou")


def test_completion_narrows_to_what_was_typed(grc):
    hits = _complete_all(grc, "if", "if")
    assert hits == ["ifconfig"], f"expected just ifconfig, got {hits}"


def test_tab_completes_the_second_word_where_we_know_the_set(grc):
    """`route ` + Tab should offer show/add/del, not the whole verb list again."""
    hits = _complete_all(grc, "route ", "")
    assert set(hits) == {"show", "add", "del"}


def test_an_unknown_verb_offers_nothing_rather_than_guessing(grc):
    """Completing into a command that does not exist is worse than not completing: Tab silently
    inserts something the router will reject."""
    assert _complete_all(grc, "banana ", "") == []


def test_completion_never_breaks_the_prompt(grc):
    """A completer that raises takes the whole REPL down with it."""
    grc._readline = None                       # the worst case: not wired up at all
    assert grc._complete("rou", 0) is None


def test_the_verb_list_matches_the_router(grc):
    """Completion is only as good as this list. Checked against registerCLI() in cli.c so a new
    gRouter command does not silently stay uncompletable."""
    cli_c = GRCONSOLE.parents[1] / "src" / "grouter" / "cli.c"   # backend/src/grouter/cli.c
    if not cli_c.exists():
        pytest.skip("gRouter C source not present")
    import re
    registered = set(re.findall(r'registerCLI\("([a-z_]+)"', cli_c.read_text()))
    missing = registered - set(grc.COMMANDS)
    assert not missing, f"gRouter commands with no Tab completion: {sorted(missing)}"


def test_subcommands_only_cover_verbs_that_exist(grc):
    unknown = set(grc.SUBCOMMANDS) - set(grc.COMMANDS)
    assert not unknown, f"SUBCOMMANDS names verbs that are not commands: {sorted(unknown)}"


def test_ctrl_c_abandons_the_line_and_does_not_exit(grc):
    """A student interrupting a half-typed command expects a fresh prompt. Letting
    KeyboardInterrupt escape drops them out of the router CLI entirely — and with tmux underneath,
    into a bare shell they did not ask for.

    Read from the source: driving this needs a real terminal to deliver the signal.
    """
    import ast
    tree = ast.parse(GRCONSOLE.read_text())
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    handlers = [h for t in ast.walk(main) if isinstance(t, ast.Try) for h in t.handlers]
    caught = {getattr(h.type, "id", "") for h in handlers}
    assert "KeyboardInterrupt" in caught, "Ctrl-C escapes and quits the console"
    assert "EOFError" in caught, "Ctrl-D no longer exits cleanly"


def test_history_setup_is_best_effort(grc, tmp_path, monkeypatch):
    """A Python built without readline, or a read-only home, must degrade to the old behaviour
    rather than crash at start-up."""
    monkeypatch.setattr(grc, "HISTORY", str(tmp_path / "nested" / "hist"))
    grc.setup_readline("r1")                   # unwritable path: must not raise
    grc.save_history()                         # nor here


def test_save_history_without_readline_is_a_noop(grc):
    grc._readline = None
    grc.save_history()


def test_the_one_shot_path_is_untouched(grc):
    """probe_runner, the HUDs and element_query all use `--once`, which must not acquire a prompt,
    a history file or a completer."""
    import ast
    tree = ast.parse(GRCONSOLE.read_text())
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    src = ast.unparse(main)
    once = src.index("--once")
    setup = src.index("setup_readline")
    assert once < setup, "readline is set up before the --once early return"

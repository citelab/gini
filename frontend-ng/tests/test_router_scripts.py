"""The router module directory behind GINI Source's router mode.

~/.gini/scripts is shared by every gRouter (mounted at /scripts), so this listing is what a
student reads before deciding to load a module. It must never raise: a missing directory, an
unreadable file, or a file that is not valid UTF-8 all have to degrade to something showable.
"""
from gini.domain.router_scripts import line_count, list_modules, read_module


def test_lists_only_lua_sorted(tmp_path):
    (tmp_path / "mcast_tree.lua").write_text("-- tree\n")
    (tmp_path / "rip_reference.lua").write_text("-- rip\n")
    (tmp_path / "notes.txt").write_text("not a module")
    (tmp_path / "sub").mkdir()
    names = [m.name for m in list_modules(tmp_path)]
    assert names == ["mcast_tree.lua", "rip_reference.lua"]


def test_missing_directory_is_empty_not_an_error(tmp_path):
    assert list_modules(tmp_path / "does-not-exist") == []


def test_entry_labels_and_load_path(tmp_path):
    (tmp_path / "x.lua").write_text("-- tiny\n")
    m = list_modules(tmp_path)[0]
    assert m.size_kb == 1                    # a small module still reads as 1 kB, never 0
    assert m.label.startswith("x.lua")
    assert m.load_path == "/scripts/x.lua"   # what the student types on the router console


def test_size_rounds_up(tmp_path):
    (tmp_path / "big.lua").write_text("x" * 3000)
    assert list_modules(tmp_path)[0].size_kb == 3      # ceil(3000/1024)


def test_read_module_returns_text(tmp_path):
    p = tmp_path / "m.lua"
    p.write_text("function tick() end\n")
    text, err = read_module(p)
    assert err == "" and "tick" in text


def test_read_module_reports_missing_file(tmp_path):
    text, err = read_module(tmp_path / "gone.lua")
    assert text == "" and "could not read" in err


def test_read_module_survives_bad_encoding(tmp_path):
    p = tmp_path / "weird.lua"
    p.write_bytes(b"-- \xff\xfe not utf-8\n")
    text, err = read_module(p)
    assert err == "" and text            # replacement chars beat showing nothing


def test_line_count():
    assert line_count("") == 0
    assert line_count("a\n") == 1
    assert line_count("a\nb") == 2
    assert line_count("a\nb\n") == 2

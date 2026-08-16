"""Syscall Builder dialog — renders offscreen, generates code, gates Apply, calls back."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _builder(app, **kw):
    from gini.ui.syscall_builder import SyscallBuilder
    return SyscallBuilder(None, _theme(app), **kw)


def test_generate_previews_the_five_edits(app):
    b = _builder(app)
    b.name_edit.setText("hello")
    b.body_edit.setPlainText("  return 42;")
    b._on_generate()
    text = b.preview.toPlainText()
    assert "#define SYS_hello" in text
    assert "kernel/sysproc.c" in text and "user/usys.pl" in text
    assert b.apply_btn.isEnabled()
    b.close()


def test_invalid_name_blocks_apply_and_shows_error(app):
    b = _builder(app)
    b.name_edit.setText("fork")            # collides with a stock syscall
    b._on_generate()
    assert not b.apply_btn.isEnabled()
    assert "already an xv6" in b.status.text()
    assert b.preview.toPlainText() == ""
    b.close()


def test_apply_callback_receives_codegen(app):
    got = {}
    b = _builder(app, on_apply=lambda cg: got.update(n=cg.number, name=cg.syscall_h))
    b.name_edit.setText("mycall")
    b.body_edit.setPlainText("  return 1;")
    b._on_generate()
    b._on_apply()
    assert got.get("n") == 23               # first free number after the 22 stock syscalls
    assert "SYS_mycall" in got.get("name", "")
    b.close()


def test_args_table_add_remove_caps_at_six(app):
    b = _builder(app)                       # starts with one row ("n")
    for _ in range(10):
        b._add_arg_row("x", "int")
    assert b.args_tbl.rowCount() == 6       # capped at 6 (a0–a5)
    b._remove_arg_row()
    assert b.args_tbl.rowCount() == 5
    b.close()

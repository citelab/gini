"""open_terminal picks the right launcher per platform (regression: Windows had none)."""
import gini.services.terminal as term


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(term.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    return calls


def test_windows_launches_a_console_via_cmd(monkeypatch):
    monkeypatch.setattr(term.sys, "platform", "win32")
    monkeypatch.setattr(term.shutil, "which", lambda _x: None)      # no Windows Terminal
    calls = _capture(monkeypatch)
    ok, msg = term.open_terminal("GINI M1", r"C:\proj", "docker compose exec svc sh")
    assert ok is True                                              # no longer falls through to "no terminal"
    cmdline = calls[0][0][0]
    assert cmdline.startswith("start ") and "cmd /k" in cmdline
    assert "docker compose exec svc sh" in cmdline
    assert calls[0][1].get("cwd") == r"C:\proj" and calls[0][1].get("shell") is True


def test_windows_prefers_windows_terminal_when_present(monkeypatch):
    monkeypatch.setattr(term.sys, "platform", "win32")
    monkeypatch.setattr(term.shutil, "which", lambda x: "wt.exe" if x == "wt" else None)
    calls = _capture(monkeypatch)
    ok, _ = term.open_terminal("GINI M1", r"C:\proj", "docker compose exec svc sh")
    assert ok is True
    argv = calls[0][0][0]
    assert argv[0] == "wt" and "-d" in argv and r"C:\proj" in argv    # Windows Terminal, in the cwd


def test_macos_still_uses_osascript(monkeypatch):
    monkeypatch.setattr(term.sys, "platform", "darwin")
    calls = _capture(monkeypatch)
    ok, _ = term.open_terminal("GINI M1", "/proj", "docker compose exec svc sh")
    assert ok is True and calls[0][0][0][0] == "osascript"

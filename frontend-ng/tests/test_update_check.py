"""Is there a newer gBuilder, and is the advice we give about it safe to follow?

The check itself is a one-line HTTP read; everything worth testing is the judgement around it. Two
ways this feature can do harm, and both are covered here:

* telling a student to run the wrong command — `pip install --upgrade` inside a pipx venv, or
  either command against a source checkout, which would replace the tree they are working in;
* telling them to upgrade when they are on a development build that is AHEAD of PyPI, where
  "upgrading" moves them backwards.

No test here touches the network. `latest_version` is the only thing that does, and it is replaced
wherever a test needs an answer from it.
"""
from __future__ import annotations

import json
from pathlib import Path

from gini.services import update_check as uc


def _pypi(version: str):
    """Stand in for PyPI's JSON, in the shape the real endpoint returns it."""
    class _R:
        def read(self):
            return json.dumps({"info": {"version": version}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return lambda *a, **k: _R()


def test_a_higher_release_is_newer_and_an_equal_or_lower_one_is_not():
    assert uc.is_newer("6.11.4", "6.11.3")
    assert uc.is_newer("6.12.0", "6.11.9")
    assert uc.is_newer("6.11.10", "6.11.9"), "string comparison would say 6.11.10 < 6.11.9"
    assert not uc.is_newer("6.11.3", "6.11.3")
    assert not uc.is_newer("6.11.2", "6.11.3")


def test_a_development_build_sits_below_the_release_it_is_leading_up_to():
    """A checkout between tags is `6.11.4.dev23`. It is ahead of released 6.11.3 — so PyPI's 6.11.3
    is not an upgrade — and behind the 6.11.4 it will become."""
    assert uc.is_newer("6.11.4", "6.11.4.dev23+g1a2b3c")
    assert not uc.is_newer("6.11.3", "6.11.4.dev23+g1a2b3c")


def test_an_unparseable_version_is_never_a_reason_to_tell_someone_to_reinstall():
    for bad in ("", "unknown", "junk", None):
        assert uc.is_newer(bad, "6.11.3") is False
        assert uc.is_newer("6.11.4", bad) is False


def test_the_fallback_ordering_agrees_with_packaging(monkeypatch):
    """`packaging` is not a declared dependency — setuptools-scm pulling it at build time is not
    the same as it being there at runtime — so the hand-rolled path has to reach the same answers."""
    import builtins
    pairs = [("6.11.4", "6.11.3"), ("6.11.3", "6.11.3"), ("6.11.10", "6.11.9"),
             ("6.11.4", "6.11.4.dev23+gabc"), ("6.11.3", "6.11.4.dev23+gabc"), ("7.0.0", "6.11.3")]
    with_packaging = [uc.is_newer(a, b) for a, b in pairs]

    real = builtins.__import__

    def no_packaging(name, *a, **k):
        if name.startswith("packaging"):
            raise ImportError("pretend it is not installed")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_packaging)
    assert [uc.is_newer(a, b) for a, b in pairs] == with_packaging


def test_each_install_method_gets_the_command_that_matches_it():
    assert uc.upgrade_command(uc.PIPX) == "pipx upgrade gini-toolkit"
    assert "pip install --upgrade gini-toolkit" in uc.upgrade_command(uc.VENV)
    assert "pip install --upgrade gini-toolkit" in uc.upgrade_command(uc.USER)


def test_no_command_is_offered_for_an_install_we_cannot_identify_or_must_not_touch():
    assert uc.upgrade_command(uc.SOURCE) == ""
    assert uc.upgrade_command(uc.UNKNOWN) == ""


def test_only_the_toolkit_is_named_because_the_core_floor_carries_the_rest():
    """Naming gini-core too would look safer and is not: the floor in pyproject.toml always equals
    the release version, and that is enforced by scripts/release.sh rather than remembered."""
    for kind in (uc.PIPX, uc.VENV, uc.USER):
        assert "gini-core" not in uc.upgrade_command(kind)


def test_a_source_checkout_is_told_to_use_git_and_never_pip():
    said = uc.advice({"ok": True, "installed": "6.11.3", "latest": "6.11.4", "newer": True,
                      "kind": uc.SOURCE, "command": ""})
    assert "pip" not in said["command"]
    assert said["command"] == "", "a Copy-command button here would hand over a damaging command"
    assert "git pull" in said["detail"]
    assert "replace the tree" in said["detail"]


def test_an_unidentified_install_is_told_in_words_rather_than_guessed_at():
    said = uc.advice({"ok": True, "installed": "6.11.3", "latest": "6.11.4", "newer": True,
                      "kind": uc.UNKNOWN, "command": ""})
    assert said["command"] == ""
    assert "could not tell how this copy was installed" in said["detail"]


def test_an_available_upgrade_names_the_command_and_warns_about_the_images():
    said = uc.advice({"ok": True, "installed": "6.11.3", "latest": "6.11.4", "newer": True,
                      "kind": uc.PIPX, "command": "pipx upgrade gini-toolkit"})
    assert "6.11.4 is available" in said["headline"]
    assert said["command"] == "pipx upgrade gini-toolkit"
    assert "container images" in said["detail"], (
        "gBuilder pins images to its own version, so an upgrade that stops at the package leaves "
        "Run broken until the next launch — the student has to be told that up front")


def test_a_build_ahead_of_pypi_is_not_offered_a_downgrade():
    said = uc.advice({"ok": True, "installed": "6.11.4.dev23", "latest": "6.11.3", "newer": False,
                      "kind": uc.SOURCE, "command": ""})
    assert "ahead" in said["headline"]
    assert "backwards" in said["detail"]
    assert said["command"] == ""


def test_being_up_to_date_says_so_plainly():
    said = uc.advice({"ok": True, "installed": "6.11.3", "latest": "6.11.3", "newer": False,
                      "kind": uc.PIPX, "command": ""})
    assert "up to date" in said["headline"]
    assert said["command"] == ""


def test_an_unreachable_pypi_is_a_sentence_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("Name or service not known")

    monkeypatch.setattr(uc.urllib.request, "urlopen", boom)
    r = uc.check(timeout=0.1)
    assert r["ok"] is False
    assert r["newer"] is False
    assert r["installed"], "we still know our own version with the network down"
    said = uc.advice(r)
    assert "could not check" in said["headline"]
    assert "never needs the internet to run a lab" in said["detail"], (
        "a lab machine with no internet must not be left thinking something is wrong with GINI")


def test_a_reachable_pypi_answers_the_question(monkeypatch):
    monkeypatch.setattr(uc.urllib.request, "urlopen", _pypi("99.0.0"))
    r = uc.check(timeout=0.1)
    assert r["ok"] is True
    assert r["latest"] == "99.0.0"
    assert r["newer"] is True


def test_nothing_about_this_machine_is_sent(monkeypatch):
    """One GET to a public URL, no body, no headers of ours, no version in the query. A course
    hands this tool to students; it must not report who is running what."""
    seen = {}

    def record(url, *a, **k):
        seen["url"] = url
        seen["kwargs"] = k
        return _pypi("6.11.3")()

    monkeypatch.setattr(uc.urllib.request, "urlopen", record)
    uc.check(timeout=0.1)
    assert seen["url"] == "https://pypi.org/pypi/gini-toolkit/json"
    assert isinstance(seen["url"], str), "a Request object could carry headers; a bare URL cannot"
    assert set(seen["kwargs"]) <= {"timeout"}


def test_a_checkout_is_recognised_as_a_source_install():
    """The one kind where being wrong does damage: a developer told to pip-install over their own
    working copy. Asserted only when this suite IS running from a tree — which is how `dev.sh
    install` and CI both run it — so the test states a fact rather than an environment."""
    kind = uc.install_kind()
    assert kind in (uc.PIPX, uc.VENV, uc.USER, uc.SOURCE, uc.UNKNOWN)
    from_tree = "/src/gini/" in str(Path(uc.__file__).resolve()).replace("\\", "/")
    if from_tree:
        assert kind == uc.SOURCE

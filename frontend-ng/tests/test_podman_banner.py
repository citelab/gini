"""Podman announces itself on stderr, and GINI reported it as the error.

From the Trottier lab, running a topology under Podman:

    Name resolution over the drawn network could not be set up on:
      M1: >>>> Executing external compose provider "/usr/bin/podman-compose". Please refer to
      the documentation for details. <

Podman 5 delegates `podman compose` to an external provider and says so, on stderr, for every
command. It is not an error, it is not about the command that ran, and it arrives FIRST — so code
reporting "the first 120 characters of stderr" reports the banner on every Podman machine, for
every failure, and the real cause never reaches the screen.
"""
from __future__ import annotations

from gini.setup.runtime import compose_error

BANNER = (b'>>>> Executing external compose provider "/usr/bin/podman-compose". '
          b'Please refer to the documentation for details. <\n')


def test_the_banner_alone_is_not_an_error():
    assert compose_error(BANNER) == ""


def test_the_real_error_survives_the_banner():
    raw = BANNER + b'Error: no container with name or ID "gini-m1" found: no such container\n'
    out = compose_error(raw)
    assert "no such container" in out
    assert "compose provider" not in out


def test_the_end_of_stderr_is_kept_rather_than_the_beginning():
    """A traceback puts its point on the last line and its preamble on the first. Truncating the
    front keeps the preamble, which is how a real error becomes a quote of somebody's boilerplate.
    """
    raw = BANNER + b"\n".join([b"Traceback (most recent call last):",
                               b'  File "/usr/bin/podman-compose", line 1, in <module>',
                               b"  File \"/usr/lib/python3/podman_compose.py\", line 9",
                               b"TypeError: exec() got an unexpected keyword argument 'T'"])
    out = compose_error(raw, limit=200)
    assert "TypeError" in out
    assert "Traceback (most recent" not in out


def test_a_docker_error_is_untouched():
    """Nothing about this may change what a Docker machine sees."""
    raw = b"Error response from daemon: container not running\n"
    assert compose_error(raw) == "Error response from daemon: container not running"


def test_empty_stays_empty_rather_than_becoming_a_message():
    assert compose_error(b"") == ""
    assert compose_error(None) == ""


def test_text_as_well_as_bytes():
    assert "boom" in compose_error("boom")


def test_the_app_names_the_engine_it_is_actually_using(monkeypatch):
    """The same log said "Topology running on Docker." on a machine with no Docker installed.
    `engine_name` exists for this and its own docstring says why: saying Docker when the machine
    has Podman is confusing, and on a campus box it reads as the app describing a different
    computer."""
    from gini.setup import runtime

    monkeypatch.setattr(runtime, "detect_engine", lambda run=None: "podman")
    assert runtime.engine_name() == "Podman"

    # String LITERALS, not raw source: the comment explaining this fix contains the phrase, and a
    # substring search over the file matches its own explanation and calls that a regression.
    import ast
    import pathlib as _p

    tree = ast.parse(_p.Path(runtime.__file__).parents[1].joinpath(
        "ui", "main_window.py").read_text(encoding="utf-8"))
    said = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    bad = [t for t in said if "on Docker" in t or "via Docker" in t]
    assert not bad, f"a hardcoded engine name is back in a user-facing message: {bad}"

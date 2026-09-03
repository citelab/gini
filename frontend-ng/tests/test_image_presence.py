"""Deciding whether a lab image is on this machine — the check that refused to start topologies.

The report was "every now and then gBuilder gives this weird message and refuses to start", with:

    Run failed: The real gRouter image 'gini-grouter' isn't built yet, and I can't find the
    backend to build it.
    Build it once:
      cd /Users/…/pipx/venvs/gini-toolkit/lib/backend && docker build …

Two defects in one message, and the intermittency is the important one.

Docker Desktop's Resource Saver pauses the VM when the machine goes idle. The first command after
a wake reaches a daemon that ANSWERS — so it is not a connection failure, and no "is Docker
running?" check catches it — out of an image store that has not finished loading. `docker image
inspect gini-grouter` comes back "No such image" for an image that is right there; seconds later
the same command succeeds. Reproduced exactly that way while diagnosing it.

The second defect: from a wheel or pipx install there is no `backend/` and never was, so the
advice was a `cd` into a directory that does not exist. `gini-setup` is the answer there.
"""
from __future__ import annotations

import subprocess

import pytest

from gini.services.orchestrator import Orchestrator


MISS = 'Error response from daemon: {"message":"No such image: gini-grouter"}'


class _Docker:
    """A fake `docker`, answering the way the real one did on the machine this was found on.

    `listed` is what `docker images` reports; `resolvable` is what `docker image inspect` can find
    by name. THEY CAN DISAGREE, and that disagreement is the bug being tested — so they are
    separate fields here rather than one notion of "present".
    """

    def __init__(self, fail_first: int = 0, never: bool = False,
                 listed: str = "", resolvable: bool = True, tag_works: bool = True):
        self.fail_first, self.never, self.calls = fail_first, never, 0
        self.listed, self.resolvable, self.tag_works = listed, resolvable, tag_works
        self.tagged: list = []

    def __call__(self, argv, **kw):
        if argv[:2] == ["docker", "images"]:
            return subprocess.CompletedProcess(argv, 0, self.listed, "")
        if argv[:2] == ["docker", "tag"]:
            self.tagged.append(argv[2:])
            if self.tag_works:
                self.resolvable = True
            return subprocess.CompletedProcess(argv, 0 if self.tag_works else 1, "", "")
        self.calls += 1                                  # image inspect
        miss = self.never or self.calls <= self.fail_first or not self.resolvable
        return subprocess.CompletedProcess(argv, 1 if miss else 0, "", MISS if miss else "")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The retry pauses are real seconds; a test must not spend them."""
    monkeypatch.setattr("gini.services.orchestrator.time.sleep", lambda *_a: None)


def _present(monkeypatch, docker):
    """Both modules, because the work spans both on purpose: the retry is in the orchestrator and
    the tag repair is in `setup.images`, shared with `missing_locally` — the same defect strikes
    Run and setup from two directions and is fixed in one place."""
    monkeypatch.setattr("gini.services.orchestrator.subprocess.run", docker)
    monkeypatch.setattr("gini.setup.images.subprocess.run", docker)
    return Orchestrator._image_present("gini-grouter")


def test_an_image_that_is_there_is_found_first_time(monkeypatch):
    d = _Docker()
    assert _present(monkeypatch, d) is True
    assert d.calls == 1, "no retry when the first answer is yes"


def test_a_waking_daemon_does_not_read_as_a_missing_image(monkeypatch):
    """THE bug. One "No such image" from a daemon that is loading its store is not evidence."""
    d = _Docker(fail_first=1)
    assert _present(monkeypatch, d) is True
    assert d.calls == 2


def test_a_slow_wake_is_still_not_a_missing_image(monkeypatch):
    d = _Docker(fail_first=2)
    assert _present(monkeypatch, d) is True


def test_an_image_that_really_is_absent_is_still_reported_absent(monkeypatch):
    """The retry must not turn a real absence into a hang or a false yes."""
    d = _Docker(never=True)
    assert _present(monkeypatch, d) is False
    assert d.calls == 3, "it gives up rather than retrying for ever"


# ---- a name Docker has lost track of --------------------------------------------- #
# The report came back after the first fix, because retrying does not help when nothing is waking
# up. On Docker Desktop 27.4.0, right after `pull` + `tag` of an image already present under
# another tag — exactly what `gini-setup` does on every upgrade — three answers disagreed:
#
#   docker images gini-grouter        -> gini-grouter latest 57b66b555d00 154MB
#   docker image inspect gini-grouter -> No such image: gini-grouter
#   docker image inspect 57b66b555d00 -> RepoTags: [gini-grouter:latest, ...]
#
# Only the middle one is wrong, and it is the one everything asked. Re-tagging repaired it.
LISTED = "\n".join(("sha256:57b66b555d00 gini-grouter:latest",
                    "sha256:aaaaaaaaaaaa ghcr.io/gini-toolkit/gini-grouter:6.5.1",
                    "sha256:bbbbbbbbbbbb <none>:<none>"))


def test_a_lost_tag_is_repaired_rather_than_reported_missing(monkeypatch):
    d = _Docker(listed=LISTED, resolvable=False)
    assert _present(monkeypatch, d) is True
    assert d.tagged == [["sha256:57b66b555d00", "gini-grouter:latest"]]


def test_the_repair_is_confirmed_and_not_assumed(monkeypatch):
    """A `docker tag` that returns 0 without fixing the index must still read as absent. Compose
    resolves `image: gini-grouter` through the same index, so a run waved past here fails deeper
    in, with a message far less legible than this one."""
    d = _Docker(listed=LISTED, resolvable=False, tag_works=False)
    assert _present(monkeypatch, d) is False


def test_a_genuinely_absent_image_is_not_invented(monkeypatch):
    """Nothing in the listing carries that name, so there is nothing to repair."""
    d = _Docker(listed="sha256:cccccccccccc some-other-image:latest\n", resolvable=False)
    assert _present(monkeypatch, d) is False
    assert d.tagged == []


def test_an_untagged_name_is_matched_as_latest(monkeypatch):
    """`docker image inspect gini-grouter` means `gini-grouter:latest`, and the listing prints the
    tag in full — so the name has to be normalised before comparing, or the repair never matches."""
    d = _Docker(listed=LISTED, resolvable=False)
    monkeypatch.setattr("gini.services.orchestrator.subprocess.run", d)
    monkeypatch.setattr("gini.setup.images.subprocess.run", d)
    assert Orchestrator._image_present("gini-grouter") is True


def test_a_broken_listing_does_not_raise(monkeypatch):
    def boom(argv, **kw):
        if argv[:2] == ["docker", "images"]:
            raise OSError("docker went away")
        return subprocess.CompletedProcess(argv, 1, "", MISS)
    monkeypatch.setattr("gini.services.orchestrator.subprocess.run", boom)
    monkeypatch.setattr("gini.setup.images.subprocess.run", boom)
    assert Orchestrator._image_present("gini-grouter") is False


# ---- and the advice that goes with it -------------------------------------------- #
def test_a_wheel_install_is_not_told_to_cd_into_a_backend_it_does_not_have():
    """pipx and pip installs have no `backend/`. The old message printed a build command rooted in
    the venv — a path that does not exist — which is advice nobody can follow."""
    msg = Orchestrator._no_image_advice("gini-grouter", "cd /nope && docker build .", False)
    assert "gini-setup" in msg
    assert "docker build" not in msg and "cd " not in msg


def test_a_source_checkout_is_still_told_how_to_build():
    msg = Orchestrator._no_image_advice("gini-grouter", "cd /repo/backend && docker build .", True)
    assert "docker build" in msg
    assert "gini-setup" not in msg


def test_a_stopped_daemon_says_so_instead_of_blaming_the_image(monkeypatch):
    """A daemon that is down and an image that is absent look identical to `inspect`, and they
    need opposite advice — the same distinction `setup/runtime.docker_state` already draws."""
    monkeypatch.setattr("gini.setup.runtime.docker_state", lambda **_k: "stopped")
    msg = Orchestrator._docker_not_ready(None)
    assert "not answering" in msg and "Start Docker" in msg


def test_a_healthy_daemon_has_nothing_to_say(monkeypatch):
    monkeypatch.setattr("gini.setup.runtime.docker_state", lambda **_k: "ok")
    assert Orchestrator._docker_not_ready(None) == ""


def test_a_broken_docker_check_does_not_take_the_run_down(monkeypatch):
    """It runs on the failure path of something already going wrong. It must not add an exception
    to it."""
    def boom(**_k):
        raise OSError("no docker here")
    monkeypatch.setattr("gini.setup.runtime.docker_state", boom)
    assert Orchestrator._docker_not_ready(None) == ""


# ---- Windows: CRLF ---------------------------------------------------------------- #
# Asked for deliberately. This project has been bitten by Windows line endings before — see
# `orchestrator._put`, which pins UTF-8 and "\n" for every file written into a Linux container,
# after cp1252 turned an em-dash into byte 0x97 and killed run_fabric.py, and after CRLF broke sh.
#
# That hazard is on the WRITE side and none of this code writes anything. What it does is parse
# `docker images` output, so the question is whether a Windows daemon's CRLF can break the parse.
# It cannot, on two independent counts, and both are pinned here because the machine that would
# catch a regression is not one anybody has to hand.
CRLF_LISTING = "sha256:57b66b555d00 gini-grouter:latest\r\nsha256:aaa ghcr.io/x/y:6.5.1\r\n"


def test_a_windows_listing_with_crlf_still_finds_the_image(monkeypatch):
    """Belt: `subprocess.run(text=True)` opens the pipe in universal-newline mode, so "\\r\\n"
    arrives as "\\n" whatever the platform. Braces: this feeds RAW CRLF past that translation, and
    it still parses — `splitlines()` splits on "\\r\\n" and `.strip()` takes any stray "\\r" off."""
    d = _Docker(listed=CRLF_LISTING, resolvable=False)
    assert _present(monkeypatch, d) is True
    assert d.tagged == [["sha256:57b66b555d00", "gini-grouter:latest"]]


def test_a_carriage_return_never_reaches_the_tag_command(monkeypatch):
    """The failure that would actually hurt: `docker tag <id> gini-grouter:latest\\r` succeeds
    against a name nothing will ever ask for, so the repair reports success and Run fails anyway —
    the exact shape of bug this whole fix exists to remove."""
    d = _Docker(listed=CRLF_LISTING, resolvable=False)
    _present(monkeypatch, d)
    for args in d.tagged:
        for a in args:
            assert "\r" not in a and "\n" not in a, f"a newline rode into `docker tag`: {a!r}"


def test_the_format_argument_survives_windows_argument_quoting():
    """`{{.ID}} {{.Repository}}:{{.Tag}}` contains a space. On Windows the argv list is joined into
    one command line, and a format split in half would make every line unparseable."""
    import subprocess as sp
    argv = ["docker", "images", "--no-trunc", "--format", "{{.ID}} {{.Repository}}:{{.Tag}}"]
    assert '"{{.ID}} {{.Repository}}:{{.Tag}}"' in sp.list2cmdline(argv)

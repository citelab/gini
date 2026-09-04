"""gini-setup — pure logic (OS plan, image refs/tags, marker, runtime detection, CLI --check)."""
import types

from gini.setup import IMAGES, REGISTRY, images, marker, runtime
from gini.setup.cli import main as setup_main


def test_detect_os_maps_platform(monkeypatch):
    for sysname, expect in [("Darwin", "macos"), ("Linux", "linux"), ("Windows", "windows")]:
        monkeypatch.setattr(runtime.platform, "system", lambda s=sysname: s)
        assert runtime.detect_os() == expect


def test_runtime_plan_is_per_os():
    assert "Colima" in runtime.runtime_plan("macos")["runtime"]
    assert runtime.runtime_plan("macos")["auto"]                 # mac can auto-install
    assert runtime.runtime_plan("linux")["auto"] == []           # linux is guided (sudo)
    assert "Docker Desktop" in runtime.runtime_plan("windows")["runtime"]  # no Colima on Windows


def test_docker_available_needs_cli_and_daemon(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: None)
    assert runtime.docker_available() is False                   # no CLI
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: "/usr/bin/docker")
    ok = types.SimpleNamespace(returncode=0)
    bad = types.SimpleNamespace(returncode=1)
    assert runtime.docker_available(run=lambda *a, **k: ok) is True
    assert runtime.docker_available(run=lambda *a, **k: bad) is False


def test_image_tag_pins_release_else_latest():
    assert images.image_tag("6.1.0") == "6.1.0"                  # clean release -> pinned
    assert images.image_tag("6.0.1.dev0") == "latest"           # dev build -> latest
    assert images.image_tag("0.0.0+unknown") == "latest"
    assert images.image_tag(None) == "latest"


def test_image_refs_cover_all_custom_images():
    refs = images.image_refs("6.1.0")
    assert len(refs) == len(IMAGES)
    assert f"{REGISTRY}/gini-xv6:6.1.0" in refs
    assert all(r.startswith(REGISTRY + "/") for r in refs)


# --------------------------------------------------------------------------- #
# a fake docker that has IDENTITIES, not just names
# --------------------------------------------------------------------------- #
# The old fakes answered "does this name exist" with an exit code and no output, which is exactly
# the question that hid a three-week regression: a machine kept `gini-xv6:latest` pointing at
# August's 6.0.0 while the marker claimed 6.6.0, and every check said present. So the fake now
# models what Docker actually is — a map from name to image id — because the code under test now
# asks which image a name resolves to.
class FakeDocker:
    def __init__(self, store: dict | None = None, tag_sticks: bool = True):
        self.store = dict(store or {})       # name -> image id
        self.tag_sticks = tag_sticks         # False = `docker tag` returns 0 and changes nothing
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        import subprocess as sp
        self.calls.append(list(cmd))
        if cmd[:2] == ["docker", "pull"]:
            ref = cmd[2]
            if ref in self.pullable:
                self.store[ref] = self.pullable[ref]
                return sp.CompletedProcess(cmd, 0, "", "")
            return sp.CompletedProcess(cmd, 1, "", "no such image")
        if cmd[:2] == ["docker", "tag"]:
            src, dst = cmd[2], cmd[3]
            if self.tag_sticks and src in self.store:
                self.store[dst] = self.store[src]
            return sp.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["docker", "image", "inspect"]:
            names = cmd[5:] if cmd[3:5] == ["--format", "{{.Id}}"] else cmd[3:]
            if any(n not in self.store for n in names):
                return sp.CompletedProcess(cmd, 1, "", "No such image")
            return sp.CompletedProcess(cmd, 0, "\n".join(self.store[n] for n in names) + "\n", "")
        if cmd[:2] == ["docker", "images"]:
            rows = "\n".join(f"{i} {n}" for n, i in self.store.items())
            return sp.CompletedProcess(cmd, 0, rows, "")
        return sp.CompletedProcess(cmd, 0, "", "")

    pullable: dict = {}


XV6 = f"{REGISTRY}/gini-xv6:6.6.0"
POX = f"{REGISTRY}/gini-pox:6.6.0"


def test_pull_images_records_success_and_failure():
    d = FakeDocker()
    d.pullable = {"r/gini-xv6:1": "sha256:aaa"}          # oszoo is not published
    res = dict(images.pull_images(["r/gini-xv6:1", "r/gini-oszoo:1"], run=d))
    assert res["r/gini-xv6:1"] is True and res["r/gini-oszoo:1"] is False


def test_local_name_is_the_name_the_runtime_actually_resolves():
    assert images.local_name("ghcr.io/gini-toolkit/gini-grouter:6.1.0") == "gini-grouter:latest"
    assert images.local_name("ghcr.io/gini-toolkit/gini-xv6:latest") == "gini-xv6:latest"


def test_a_pull_also_tags_the_image_under_the_name_the_runtime_looks_for():
    """THE bug that left every pip install unable to Run anything.

    `docker pull` leaves an image under its REGISTRY reference, and nothing looks for it there:
    the orchestrator inspects `gini-grouter`, the compiler writes `gini-xv6:latest` into the
    compose file. Setup reported four successful pulls and the machine still had nothing runnable
    — and the error surfaced later, elsewhere, as "the gRouter image isn't built yet", pointing at
    a backend/ that a wheel does not contain.
    """
    ref = f"{REGISTRY}/gini-grouter:6.1.0"
    d = FakeDocker()
    d.pullable = {ref: "sha256:grouter"}
    assert dict(images.pull_images([ref], run=d))[ref] is True
    assert ["docker", "pull", ref] in d.calls
    assert ["docker", "tag", ref, "gini-grouter:latest"] in d.calls


def test_an_image_that_pulls_but_cannot_be_tagged_counts_as_a_failure():
    """Otherwise setup writes a marker saying this machine is ready, over an image nothing finds."""
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=1 if cmd[1] == "tag" else 0)

    res = dict(images.pull_images(["r/gini-xv6:1"], run=fake_run))
    assert res["r/gini-xv6:1"] is False


def test_docker_state_tells_a_stopped_engine_from_an_absent_one(monkeypatch):
    ok = types.SimpleNamespace(returncode=0)
    bad = types.SimpleNamespace(returncode=1)
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: None)
    assert runtime.docker_state() == "missing"
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: "/usr/bin/docker")
    assert runtime.docker_state(run=lambda *a, **k: ok) == "ok"
    assert runtime.docker_state(run=lambda *a, **k: bad) == "stopped"   # installed, not answering


def test_every_os_says_how_to_START_the_runtime_not_only_how_to_install_it():
    for os_name in ("macos", "linux", "windows", "unknown-os"):
        assert runtime.runtime_plan(os_name).get("start"), os_name


def test_missing_locally_asks_about_the_ref_AND_the_name_it_resolves_to():
    """Both, and that is the fix. The runtime looks up `gini-xv6:latest`, so that name has to be
    asked about — but asking ONLY about it cannot tell 6.6.0's kernel from August's, because
    `local_name` has already thrown the version away."""
    d = FakeDocker({XV6: "sha256:new", "gini-xv6:latest": "sha256:new"})
    assert images.missing_locally([XV6], run=d) == []
    first = d.calls[0]
    assert "gini-xv6:latest" in first, "the name the runtime resolves"
    assert XV6 in first, "and the version that name is supposed to be"


def test_missing_locally_names_exactly_what_is_gone():
    d = FakeDocker({POX: "sha256:pox", "gini-pox:latest": "sha256:pox"})   # pox here, xv6 not
    assert images.missing_locally([XV6, POX], run=d) == [XV6]


def test_an_unreachable_docker_does_not_claim_the_images_are_gone():
    """That machine is already heading for NEEDS_RUNTIME; guessing here would offer a download to
    somebody whose engine is simply stopped."""
    def boom(cmd, **k):
        raise OSError("cannot talk to docker")

    assert images.missing_locally([f"{REGISTRY}/gini-xv6:6.1.1"], run=boom) == []


def test_marker_roundtrip_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME", str(tmp_path))
    assert marker.is_setup_done() is False
    marker.write_marker({"version": "6.1.0", "images": ["r/gini-xv6:6.1.0"]})
    assert marker.is_setup_done() is True
    assert marker.setup_version() == "6.1.0"
    assert marker.needs_update("6.1.0") is False
    assert marker.needs_update("6.2.0") is True                  # app upgraded past setup


def test_cli_check_reports_without_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GINI_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "docker_available", lambda *a, **k: False)
    rc = setup_main(["--check"])
    out = capsys.readouterr().out
    assert rc == 0 and "runtime: NOT found" in out and "not run yet" in out


# --------------------------------------------------------------------------- #
# a tag Docker has lost track of
# --------------------------------------------------------------------------- #
# The user-visible complaint was two-headed: gBuilder "goes into pulling the images even when I
# don't expect it to find anything new", and Run refused to start a topology saying the gRouter
# image was not built. One cause. Docker's name index can stop resolving a tag the image itself
# still claims — seen on Docker Desktop 27.4.0 right after `pull` + `tag` of an image already
# present under another tag, which is exactly what `pull_images` does on every upgrade.
#
# `missing_locally` asks `docker image inspect <name>`, gets "No such image", and concludes all
# four are gone. So every launch re-downloaded them, the pull re-tagged, the tag did not stick,
# and the next launch did it again.
LISTING = "\n".join(("sha256:aaaaaaaaaaaa gini-xv6:latest",
                     "sha256:bbbbbbbbbbbb gini-oszoo:latest",
                     "sha256:cccccccccccc <none>:<none>"))


def _docker(resolvable: set, listing: str = LISTING):
    """A docker whose LISTING and whose INSPECT disagree — which is the whole bug."""
    seen = {"tagged": []}

    def run(argv, **kw):
        import subprocess as sp
        if argv[:2] == ["docker", "images"]:
            return sp.CompletedProcess(argv, 0, listing, "")
        if argv[:2] == ["docker", "tag"]:
            seen["tagged"].append(argv[3])
            resolvable.add(argv[3])
            return sp.CompletedProcess(argv, 0, "", "")
        # docker image inspect --format {{.Id}} NAME…  — the names start after the format value
        names = list(argv[5:])
        ok = all(n in resolvable for n in names)
        return sp.CompletedProcess(argv, 0 if ok else 1, "", "")
    run.seen = seen
    return run


def test_a_lost_tag_does_not_look_like_a_missing_image():
    """THE fix for "it keeps downloading the images". The tag is repaired and nothing is reported
    missing, so no download is proposed."""
    refs = [f"{REGISTRY}/gini-xv6:6.5.2", f"{REGISTRY}/gini-oszoo:6.5.2"]
    run = _docker(resolvable=set())
    assert images.missing_locally(refs, run=run) == []
    assert run.seen["tagged"] == ["gini-xv6:latest", "gini-oszoo:latest"]


def test_an_image_that_is_really_gone_is_still_reported():
    """The repair must not make setup blind. Nothing in the listing carries that name."""
    refs = [f"{REGISTRY}/gini-grouter:6.5.2"]
    run = _docker(resolvable=set())
    assert images.missing_locally(refs, run=run) == refs
    assert run.seen["tagged"] == []


def test_the_common_case_still_costs_one_call():
    """When every name resolves to the image its version wants, nothing else is asked — this runs
    at every launch, so the healthy path must stay a single docker invocation even though it now
    asks a harder question."""
    d = FakeDocker({XV6: "sha256:new", "gini-xv6:latest": "sha256:new"})
    assert images.missing_locally([XV6], run=d) == []
    assert len(d.calls) == 1, "the healthy path must not inspect twice, list, or tag"


def test_repair_tag_uses_the_run_it_is_given():
    """`run=subprocess.run` as a DEFAULT ARGUMENT binds at import, so patching this module's
    `subprocess` would be ignored and the real daemon reached instead. That is not hypothetical:
    it is what these tests did until the binding moved into the body."""
    import inspect as _inspect
    assert _inspect.signature(images.repair_tag).parameters["run"].default is None


# --------------------------------------------------------------------------- #
# a name that resolves to the WRONG image
# --------------------------------------------------------------------------- #
# Found on a real machine, three weeks after it happened. Installed 6.0.0 in August; every upgrade
# since left `gini-xv6:latest` pointing at August's kernel. `local_name()` discards the version by
# design, so "is gini-xv6 present?" answered yes every time, `needs_update` believed a marker that
# claimed 6.6.0, and the panel never appeared. Six releases of kernel work never arrived, and the
# first symptom loud enough to notice was the OS HUD reporting "this kernel has no board support"
# — in front of a class.
OLD, NEW = "sha256:august", "sha256:current"


def test_a_stale_image_is_missing_even_though_the_name_resolves():
    """THE bug. The name is there, the image behind it is three weeks old, and every check that
    asks only about presence says the machine is ready."""
    d = FakeDocker({"gini-xv6:latest": OLD})          # the 6.6.0 ref was never pulled
    assert images.missing_locally([XV6], run=d) == [XV6]


def test_a_current_image_is_not_reported_missing():
    d = FakeDocker({XV6: NEW, "gini-xv6:latest": NEW})
    assert images.missing_locally([XV6], run=d) == []


def test_a_stale_name_is_retagged_rather_than_re_downloaded():
    """The cheap half. When the version's own image IS here and only the plain name points
    elsewhere — what a half-finished upgrade leaves behind — that is a rename, not a gigabyte."""
    d = FakeDocker({XV6: NEW, "gini-xv6:latest": OLD})
    assert images.missing_locally([XV6], run=d) == []
    assert ["docker", "tag", XV6, "gini-xv6:latest"] in d.calls
    assert d.store["gini-xv6:latest"] == NEW


def test_a_retag_that_does_not_stick_is_not_believed():
    """`docker tag` returning 0 is not evidence the name moved — this machine has been seen to
    report exactly that. An unconfirmed repair would turn a visible problem into a silent wrong
    version, which is the failure this whole change exists to end."""
    d = FakeDocker({XV6: NEW, "gini-xv6:latest": OLD}, tag_sticks=False)
    assert images.missing_locally([XV6], run=d) == [XV6]


def test_the_check_survives_a_docker_that_answers_nothing():
    """An unreachable engine must not read as "every image is gone" — that machine is heading for
    NEEDS_RUNTIME, and a download offered to someone whose Docker is merely stopped is noise."""
    def dead(cmd, **k):
        raise OSError("docker not running")
    assert images.missing_locally([XV6], run=dead) == []


# --------------------------------------------------------------------------- #
# a pull is verified, not assumed
# --------------------------------------------------------------------------- #
def test_a_pull_is_confirmed_by_asking_what_the_name_resolves_to():
    d = FakeDocker()
    d.pullable = {XV6: NEW}
    assert dict(images.pull_images([XV6], run=d))[XV6] is True
    assert d.store["gini-xv6:latest"] == NEW


def test_a_pull_that_reports_success_but_leaves_nothing_is_a_failure():
    """The state a real marker was found in: four 6.6.0 pulls recorded as successful, none of the
    images on disk, every plain name still on August's. Two exit codes of 0 were the whole of the
    evidence, and a marker is believed for weeks afterwards — so a false success here is not one
    bad launch, it is every launch until somebody notices."""
    d = FakeDocker({"gini-xv6:latest": OLD})
    d.pullable = {}                                   # `docker pull` will refuse
    assert dict(images.pull_images([XV6], run=d))[XV6] is False


def test_a_tag_that_silently_does_not_move_the_name_is_a_failure():
    """Pull succeeds, tag returns 0, and the name still points at the old image. Recording that as
    success is what let a stale kernel run for three weeks."""
    d = FakeDocker({"gini-xv6:latest": OLD}, tag_sticks=False)
    d.pullable = {XV6: NEW}
    assert dict(images.pull_images([XV6], run=d))[XV6] is False


# --------------------------------------------------------------------------- #
# when a pull fails, say why
# --------------------------------------------------------------------------- #
# A student on an M3 pressed "Get images" on 6.6.0, 6.6.1 and 6.7.0 and each time got:
#
#   "None of the images could be downloaded. If this version was never published for arm64,
#    that is the likely reason."
#
# arm64 WAS published and pulled fine elsewhere, so the only message they had pointed away from
# the cause — and the real one had already been thrown away by `except Exception: return False`
# inside pull_one. Three versions of guessing, with the evidence deleted at the source each time.
def test_a_failed_pull_reports_what_docker_said():
    said = []
    d = FakeDocker()
    d.pullable = {}                                   # docker will refuse

    def run(cmd, **kw):
        import subprocess as sp
        if cmd[:2] == ["docker", "pull"]:
            return sp.CompletedProcess(cmd, 1, "", "Error response from daemon: manifest unknown")
        return d(cmd, **kw)

    images.pull_images([XV6], run=run, on_error=lambda ref, text: said.append((ref, text)))
    assert said and "manifest unknown" in said[0][1]


def test_a_credential_helper_failure_reaches_the_student_verbatim():
    """The real one, from a student's M3 in September 2026.

    Docker printed the cause on the very first attempt. `pull_one` threw it away and the panel
    guessed at arm64 instead, so three versions of gBuilder were installed chasing a message that
    named the wrong thing — while the machine had been saying "credentials" the whole time.

    Note what this test does NOT do: it does not parse the message, classify it, or translate it
    into advice of our own. GINI cannot fix a broken credential helper and should not pretend to
    recognise one. Passing it through unedited is the entire feature.
    """
    import subprocess as sp
    cred = ('error getting credentials - err: exec: "docker-credential-desktop": '
            'executable file not found in $PATH, out: ``')
    said = {}
    images.pull_images(
        [XV6],
        run=lambda cmd, **kw: sp.CompletedProcess(cmd, 1, "", cred),
        on_error=lambda ref, text: said.__setitem__(ref, text))
    assert "docker-credential-desktop" in said.get(XV6, ""), "the word that identifies the fault"
    assert "arm64" not in said.get(XV6, "")


def test_docker_missing_from_PATH_says_so_and_names_the_cause():
    """The macOS one. A GUI app launched from the Dock inherits /usr/bin:/bin:/usr/sbin:/sbin,
    which excludes /opt/homebrew/bin — so the same build works from a terminal and not from the
    Dock, with nothing on screen connecting the two."""
    said = []

    def boom(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory: 'docker'")

    import gini.setup.images as I
    real = I.subprocess.Popen
    I.subprocess.Popen = boom
    try:
        ok = I.pull_one(XV6, on_progress=lambda *_a: None, why=said.append)
    finally:
        I.subprocess.Popen = real
    assert ok is False
    assert said and "PATH" in said[0] and "terminal" in said[0]


def test_the_reason_is_the_most_specific_line_docker_printed():
    """Docker puts the useful part last, after a preamble that says nothing."""
    from gini.setup.images import _last_line
    assert _last_line("6.7.0: Pulling from x/y\nerror: no space left on device") \
        == "error: no space left on device"
    assert _last_line("") == "", "silence must read as silence, not as an explanation"
    assert len(_last_line("x" * 900)) <= 300         # it lands in a dialog


def test_a_tag_that_fails_after_a_good_pull_is_explained():
    said = []
    d = FakeDocker(tag_sticks=False)
    d.pullable = {XV6: "sha256:new"}
    images.pull_images([XV6], run=d, on_error=lambda ref, text: said.append(text))
    assert said and "does not resolve" in said[0]

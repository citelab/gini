"""First-run bootstrap: work out what this machine still needs, and get it.

Replaces the separate `gini-setup` command. A student who has just run `pip install gini-toolkit`
should be able to type `gbuilder` and have it sort itself out, rather than hitting a second command
they were never told about — the failure mode being an app that opens, looks fine, and then cannot
run anything.

**Architecture is deliberately absent from the image references.** A multi-arch manifest list means
`ghcr.io/…/gini-xv6:6.1.0` resolves to arm64 on Apple Silicon and amd64 on a PC *at pull time*, by
the registry. Baking `-arm64` into a tag here would move that decision into the client, where it
can be wrong — a student on an unusual platform gets a confident pull of the wrong binaries rather
than an honest "no image for your architecture". `arch()` exists for the diagnostics line and for
nothing else, and a test enforces that.

Qt-free on purpose: every decision lives here so it can be tested without a display, and the UI
only has to run `plan()` and then `execute()` on a worker thread.
"""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from ..setup import images, marker, runtime

# What each state means for the user, in one sentence. The UI shows exactly these.
READY = "ready"                  # nothing to do
NEEDS_RUNTIME = "needs_runtime"  # no Docker — we cannot fix this for them
PULL = "pull"                    # published images, just fetch them
BUILD = "build"                  # a source checkout: build locally instead
UPDATE = "update"                # app upgraded past the images


def arch() -> str:
    """This machine's CPU architecture, normalised. **Diagnostics only.**

    Never use this to pick an image tag — see the module docstring. It is here so a failed pull can
    say "no arm64 image published for 6.1.0" instead of leaving someone guessing.
    """
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64"):
        return "amd64"
    return m or "unknown"


def plan(app_version: str | None, *, run=subprocess.run, backend_hint: str | None = None) -> dict:
    """Survey the machine and say what should happen. Never raises, never touches the network."""
    os_name = runtime.detect_os()
    have_docker = runtime.docker_available(run=run)
    backend = images.find_backend(backend_hint)
    done = marker.is_setup_done()
    stale = marker.needs_update(app_version or "")

    if not have_docker:
        state, why = NEEDS_RUNTIME, (
            f"GINI runs each device in a container, and no container runtime was found. "
            f"{runtime.runtime_plan(os_name).get('runtime', 'Docker')} needs to be installed "
            f"first — the app works for building and reading topologies until then, but Run "
            f"will not start anything.")
    elif backend is not None and not done:
        state, why = BUILD, (
            "This is a source checkout, so the container images will be built locally from "
            "backend/ rather than downloaded. It takes a few minutes the first time.")
    elif not done:
        state, why = PULL, (
            "GINI needs its container images before anything can run. They will be downloaded "
            "once and reused.")
    elif stale:
        state, why = UPDATE, (
            f"gBuilder was upgraded to {app_version}, but the images on this machine were set up "
            f"for {marker.setup_version()}. Refreshing them keeps the two in step.")
    else:
        state, why = READY, "Everything needed is already here."

    return {
        "state": state,
        "why": why,
        "os": os_name,
        "arch": arch(),                    # shown, never used to choose an image
        "docker": have_docker,
        "source": str(backend) if backend else "",
        "app_version": app_version or "",
        "image_tag": images.image_tag(app_version),
        "refs": images.image_refs(app_version),
        "runtime_plan": runtime.runtime_plan(os_name),
    }


def execute(p: dict, *, on_step=None, run=subprocess.run) -> dict:
    """Carry out a plan. Returns `{ok, done, failed, message}`.

    `on_step(text)` is called before each image so a UI can show progress. This blocks — the caller
    runs it on a worker thread; a pull is minutes long and freezing the window for it would be a
    worse first impression than the missing images.
    """
    state = p.get("state")
    if state in (READY, NEEDS_RUNTIME):
        return {"ok": state == READY, "done": [], "failed": [], "message": p.get("why", "")}

    say = on_step or (lambda _t: None)

    if state == BUILD:
        backend = Path(p["source"])
        results = []
        for name in images.BUILD_SPECS:
            say(f"Building {name}…")
            results += images.build_images(backend, names=[name], run=run)
    else:
        results = []
        for ref in p["refs"]:
            say(f"Downloading {ref.rsplit('/', 1)[-1]}…")
            results += images.pull_images([ref], run=run)

    done = [n for n, ok in results if ok]
    failed = [n for n, ok in results if not ok]
    if done:
        # Record what we actually got, so a partial success is not remembered as a full one.
        marker.write_marker({"version": p.get("app_version", ""),
                             "tag": p.get("image_tag", ""),
                             "arch": p.get("arch", ""),
                             "images": done})
    return {"ok": not failed, "done": done, "failed": failed,
            "message": _outcome(state, done, failed, p)}


def _outcome(state: str, done: list, failed: list, p: dict) -> str:
    if not failed:
        return (f"{len(done)} image{'' if len(done) == 1 else 's'} ready. GINI can run topologies "
                f"now.")
    if not done:
        verb = "build" if state == BUILD else "download"
        extra = ("" if state == BUILD else
                 f" If this version was never published for {p.get('arch')}, that is the likely "
                 f"reason.")
        return (f"None of the images could be {verb}ed.{extra} You can keep building and reading "
                f"topologies; Run will not start until they are here.")
    return (f"{len(done)} ready, {len(failed)} could not be fetched: "
            f"{', '.join(f.rsplit('/', 1)[-1] for f in failed)}.")

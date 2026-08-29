"""Pull the custom GINI images from the registry, pinned to the app version — or build them
locally from a source checkout (`gini-setup --build`, for source-based installs)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import IMAGES, REGISTRY


def image_tag(version: str | None) -> str:
    """A released version (e.g. 6.1.0) pins images to that tag; a dev/unreleased build uses 'latest'
    (there's no matching published tag yet)."""
    if not version:
        return "latest"
    if "dev" in version or "+" in version or version.startswith("0"):
        return "latest"
    return version


def image_refs(version: str | None) -> list[str]:
    tag = image_tag(version)
    return [f"{REGISTRY}/{name}:{tag}" for name in IMAGES]


def local_name(ref: str) -> str:
    """The plain name the runtime resolves, derived from a registry reference.

    `ghcr.io/gini-toolkit/gini-grouter:6.1.0` -> `gini-grouter:latest`
    """
    return ref.rsplit("/", 1)[-1].split(":", 1)[0] + ":latest"


def missing_locally(refs, run=subprocess.run) -> list[str]:
    """Which of these images are NOT on this machine, by the name the runtime resolves.

    The setup marker records what a pull REPORTED, and that is not the same as what Docker holds.
    Both failures this project has shipped wrote a marker saying the machine was ready over a
    machine that could not start anything — 6.0.0 pulled arm64-only images on an Intel Mac, and
    6.1.0 pulled successfully but left nothing under the names the runtime resolves — and nothing
    ever re-checked, so gBuilder reported READY for ever and the panel never came back.

    A marker is a cache. This is its invalidation: cheap, local, and the same question the
    orchestrator will ask at Run time.

    An unreachable Docker returns [] rather than "everything is missing": that machine is already
    heading for NEEDS_RUNTIME, and guessing here would put a download in front of somebody whose
    engine is simply stopped.
    """
    names = [local_name(r) for r in refs]
    if not names:
        return []
    try:
        r = run(["docker", "image", "inspect", "--format", "{{.Id}}", *names],
                capture_output=True, timeout=30)
        if r.returncode == 0:
            return []                      # one call, and the common case answers here
    except Exception:                      # noqa: BLE001 — cannot ask; claim nothing
        return []
    missing = []
    for ref, name in zip(refs, names):     # only when something IS missing, to say which
        try:
            one = run(["docker", "image", "inspect", "--format", "{{.Id}}", name],
                      capture_output=True, timeout=15)
            if one.returncode != 0:
                missing.append(ref)
        except Exception:                  # noqa: BLE001
            missing.append(ref)
    return missing


def pull_images(refs, run=subprocess.run) -> list[tuple[str, bool]]:
    """Pull each ref AND give it the plain local name; return [(ref, ok)].

    The tag is not cosmetic, and leaving it out made every pip install unable to Run anything.
    `docker pull` leaves an image under its REGISTRY reference, but nothing looks for it there:
    the orchestrator inspects `gini-grouter` and writes `image: gini-grouter` into the compose
    file (services/orchestrator.py), and the compiler writes `gini-xv6:latest` and
    `gini-oszoo:latest` (services/compiler.py). So a pull that reported success left nothing
    under the names anything runs, and the failure surfaced much later and somewhere else, as
    "the gRouter image isn't built yet" — pointing at a `backend/` that a wheel does not contain.

    A source build already produces exactly these names (BUILD_SPECS below, whose comment promises
    "a source build drops in exactly where a registry pull would"). This is the half that makes
    that true.

    A failed tag fails the image on purpose: a pulled-but-unreachable image is worse than an
    honest failure, because setup would write a marker saying this machine is ready.
    """
    out = []
    for ref in refs:
        try:
            r = run(["docker", "pull", ref], timeout=1800)
            ok = r.returncode == 0
            if ok:
                t = run(["docker", "tag", ref, local_name(ref)], timeout=60)
                ok = t.returncode == 0
            out.append((ref, ok))
        except Exception:
            out.append((ref, False))
    return out


# -- source builds (`gini-setup --build`) ---------------------------------------------------------#
# Each image's (build context, dockerfile) relative to the backend/ source tree. The tags are the
# plain local names the orchestrator resolves by default (gini-grouter, gini-pox, …), so a source
# build drops in exactly where a registry pull would.
BUILD_SPECS: dict[str, tuple[str, str]] = {
    # image name      context (rel.)  dockerfile (rel. to context)
    "gini-grouter": (".",       "grouter-build/Dockerfile"),  # context MUST be backend/ (COPY include/, src/)
    "gini-pox":     ("sdn",     "Dockerfile"),
    "gini-oszoo":   ("oszoo",   "Dockerfile"),
    "gini-xv6":     ("xv6",     "Dockerfile"),
}


def find_backend(hint: str | None = None) -> Path | None:
    """Locate the backend/ source tree for a source-based install.

    Tries, in order: an explicit --source path, $GINI_BACKEND, the repo-sibling layout of an
    editable install (src/gini -> frontend-ng -> repo root -> backend/), and ./backend or . from
    the current directory. A directory qualifies if the gRouter Dockerfile is in it."""
    import os
    cands: list[Path] = []
    if hint:
        cands.append(Path(hint).expanduser())
    if os.environ.get("GINI_BACKEND"):
        cands.append(Path(os.environ["GINI_BACKEND"]).expanduser())
    pkg = Path(__file__).resolve()
    if len(pkg.parents) > 4:                     # setup/ -> gini/ -> src/ -> frontend-ng/ -> repo root
        cands.append(pkg.parents[4] / "backend")
    cands.append(Path.cwd() / "backend")
    cands.append(Path.cwd())
    for c in cands:
        if (c / "grouter-build" / "Dockerfile").is_file():
            return c
    return None


def build_images(backend: Path, names=None, run=subprocess.run) -> list[tuple[str, bool]]:
    """Build each image from the backend tree with its orchestrator-expected local tag.
    Returns [(name, ok)]; a failure is captured, not raised, so the rest still build."""
    out = []
    for name, (ctx_rel, dockerfile) in BUILD_SPECS.items():
        if names and name not in names:
            continue
        ctx = backend / ctx_rel
        try:
            r = run(["docker", "build", "-f", str(ctx / dockerfile), "-t", name, str(ctx)],
                    timeout=3600)
            out.append((name, r.returncode == 0))
        except Exception:
            out.append((name, False))
    return out

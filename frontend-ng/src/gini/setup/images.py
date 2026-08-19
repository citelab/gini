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


def pull_images(refs, run=subprocess.run) -> list[tuple[str, bool]]:
    """Pull each ref; return [(ref, ok)]. A failure is captured, not raised (image may be unpublished)."""
    out = []
    for ref in refs:
        try:
            r = run(["docker", "pull", ref], timeout=1800)
            out.append((ref, r.returncode == 0))
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

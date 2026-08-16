"""Pull the custom GINI images from the registry, pinned to the app version."""
from __future__ import annotations

import subprocess

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

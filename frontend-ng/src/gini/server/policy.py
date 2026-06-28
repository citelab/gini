"""Server-side policy — the single point that decides what may actually run.

The student only ever sends a *topology*; the GINI server compiles it with GINI's own
trusted compiler, then this validates + sanitizes the compiled `RuntimeConfig` before it
can touch Docker. A student therefore cannot make the daemon do anything dangerous: no
arbitrary images, no privileged containers, no host bind mounts, no non-Kata runtimes, no
fabric/router/SDN/k8s/serverless (out of scope for a Kata experiment box), and resource
use is capped. Published host ports are stripped — the server proxies consoles, which also
avoids port collisions between students.
"""
from __future__ import annotations

ALLOWED_RUNTIMES = {"", "kata"}        # plain container (runc) or a Kata microVM
# only the cloud-plane experiment subset runs here:
_FORBIDDEN_SECTIONS = ("machines", "routers", "ovs_switches", "controllers", "k8s", "faas")


class PolicyError(Exception):
    """A topology that compiled to something the server refuses to run."""


def _image_base(image: str) -> str:
    return (image or "").split("@", 1)[0]    # drop any digest; keep repo:tag


def enforce(config, allowed_images, max_cpus: float = 2.0):
    """Validate + sanitize a compiled RuntimeConfig *in place*; raise PolicyError on any
    violation. Returns the (sanitized) config on success."""
    for sec in _FORBIDDEN_SECTIONS:
        if getattr(config, sec, None):
            raise PolicyError(
                f"this backend runs only Kata/container service topologies (found {sec})")
    allow = set(allowed_images)
    for s in config.services:
        if _image_base(s.image) not in allow:
            raise PolicyError(f"image not allowed: {s.image}")
        if s.runtime not in ALLOWED_RUNTIMES:
            raise PolicyError(f"runtime not allowed: {s.runtime!r}")
        if s.privileged:
            raise PolicyError("privileged containers are not allowed")
        for v in s.volumes:
            src = v.split(":", 1)[0]
            if not src.startswith("."):           # only project-relative mounts
                raise PolicyError(f"host bind mount not allowed: {v}")
        if s.cpus and s.cpus > max_cpus:
            s.cpus = max_cpus                      # clamp to the per-student cap
        s.ports = []                               # never publish to the host (proxy instead)
    return config


def default_allowed_images() -> set:
    """The curated image set students may run: exactly the images GINI itself uses (the
    managed-service catalog) plus the compute base images. Anything else is rejected."""
    from ..services.cloud_catalog import CATALOG
    base = {_image_base(svc.image) for svc in CATALOG.values()}
    base |= {"ubuntu:22.04", "alpine:latest"}      # Instance / Kata Instance / Container
    return base

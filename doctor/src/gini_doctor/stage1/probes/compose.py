"""``compose`` — which compose PROVIDER answers, and its version.

``podman compose`` is a pass-through to an external provider, and which provider you get is the
biggest behavioural fork between two podman machines: podman-compose 1.0.6 and docker-compose v2
disagree about container naming, ``ps --format json``, and what an exit code means. Two machines
both "having podman compose" tells you nothing.
"""
from __future__ import annotations

import os
import re

from .. import platforms
from . import probe
from ._common import answering_engine, first_line


def provider_from_banner(text: str) -> str:
    if "podman-compose" in text:
        return "podman-compose"
    if re.search(r"[Dd]ocker.?[Cc]ompose", text):
        return "docker-compose"
    return "unknown-or-missing"


@probe("compose", platforms=platforms.ALL, describe="which compose PROVIDER answers, and its version")
def compose(ctx):
    engine = answering_engine(ctx)
    if engine == "podman":
        res = ctx.run(["podman", "compose", "version"])
        banner = " ".join((res.stdout + " " + res.stderr).split())
        ctx.ok("podman.raw", banner[:300] or "(no output)")
        ctx.ok("provider", provider_from_banner(banner))
        m = re.search(r'provider "([^"]+)"', banner)
        if m:
            ctx.ok("provider.path", m.group(1))
        else:
            ctx.absent("provider.path")
    elif engine == "docker":
        # Compose v2 is a plugin of the Docker CLI: there is no provider to choose.
        ctx.ok("provider", "docker-compose-plugin")
        ctx.ok("provider.path", "(built in)")
    else:
        ctx.ok("provider", "no-engine-answering")

    ctx.from_result("podman_compose.version", ctx.run(["podman-compose", "--version"], timeout=10),
                    lambda r: first_line(r.stdout))
    ctx.from_result("docker_compose.version", ctx.run(["docker-compose", "--version"], timeout=10),
                    lambda r: first_line(r.stdout))
    if ctx.which("docker"):
        ctx.from_result("docker.plugin", ctx.run(["docker", "compose", "version"], timeout=10),
                        lambda r: first_line(r.stdout))
    else:
        ctx.absent("docker.plugin")
    if ctx.platform != platforms.WINDOWS:
        for path in ("/usr/libexec/docker/cli-plugins/docker-compose",
                     "/usr/lib/docker/cli-plugins/docker-compose",
                     os.path.join(os.path.expanduser("~"), ".docker", "cli-plugins", "docker-compose")):
            if os.access(path, os.X_OK):
                ctx.ok("plugin.file", path)
                break
        else:
            ctx.absent("plugin.file")
    else:
        ctx.na("plugin.file")

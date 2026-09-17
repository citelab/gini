"""``engine`` — which container engines exist, which one gBuilder will actually use, and whether
it answers. Ported from the legacy ``probe_engine``, on all three platforms.

Both engines are asked for ``info`` as JSON in one call each, rather than one call per field:
fewer commands, and a daemon that stops answering mid-probe cannot leave half the fields filled.

The id-mapping check is Linux-only. On macOS and Windows both engines run inside a VM (Docker
Desktop, ``podman machine``), where the host has no subuid ranges and ``podman unshare`` does
not exist, so those facts are ``n/a`` there, not "missing".
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .. import platforms
from . import probe

# podman info JSON path -> fact name. Legacy names kept so old and new reports line up in S2's
# parity check.
PODMAN_FIELDS = (
    (("host", "security", "rootless"), "podman.rootless"),
    (("host", "networkBackend"), "podman.network.backend"),
    (("host", "cgroupVersion"), "podman.cgroups.version"),
    (("host", "cgroupManager"), "podman.cgroup.manager"),
    (("host", "ociRuntime", "name"), "podman.oci.runtime"),
    (("host", "conmon", "version"), "podman.conmon"),
    (("host", "slirp4netns", "executable"), "podman.slirp4netns"),
    (("store", "graphDriverName"), "podman.storage.driver"),
    (("store", "graphRoot"), "podman.storage.graphroot"),
    (("store", "runRoot"), "podman.storage.runroot"),
)

# docker info JSON key -> fact name. Docker Desktop is how most macOS and Windows students run
# GINI, and its VM's CPU and memory, not the laptop's, are what the fabric gets.
DOCKER_FIELDS = (
    ("ServerVersion", "docker.server.version"),
    ("OperatingSystem", "docker.os"),
    ("OSType", "docker.ostype"),
    ("Architecture", "docker.arch"),
    ("NCPU", "docker.cpus"),
    ("MemTotal", "docker.memory.total_mb"),
    ("Driver", "docker.storage.driver"),
    ("CgroupVersion", "docker.cgroups.version"),
)

IDMAP_KEYS = ("podman.idmap.inuse", "podman.idmap.subuid", "podman.idmap.matches")


def dig(data: Any, path) -> Any:
    for part in path:
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def parse_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def first_version_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def uid_map_outer_start(text: str) -> Optional[str]:
    """``podman unshare cat /proc/self/uid_map`` prints the root mapping first and the subordinate
    range second; the second line's outer start is the range podman's storage was created with."""
    lines = [ln.split() for ln in text.strip().splitlines() if ln.strip()]
    return lines[1][1] if len(lines) >= 2 and len(lines[1]) >= 2 else None


def subuid_start(user: str, path: str = "/etc/subuid") -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.strip().split(":")
                if len(parts) >= 3 and parts[0] == user:
                    return parts[1]
    except OSError:
        return None
    return None


def current_user() -> str:
    try:
        import pwd  # POSIX only; this is only called on Linux
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER", "")


@probe("engine", platforms=platforms.ALL,
       describe="which container engine is installed, which one gBuilder uses, and whether it answers")
def engine(ctx):
    have = {}
    for name in ("podman", "docker"):
        path = ctx.which(name)
        have[name] = path is not None
        if path:
            ctx.ok("%s.path" % name, path)
            ctx.from_result("%s.version" % name, ctx.run([name, "--version"], timeout=10),
                            lambda r: first_version_line(r.stdout))
        else:
            ctx.absent("%s.path" % name)
            ctx.absent("%s.version" % name)

    docker_info = podman_info = None
    if have["docker"]:
        res = ctx.run(["docker", "info", "--format", "{{json .}}"])
        docker_info = parse_json(res.stdout) if res.ok else None
        if docker_info is None:
            ctx.error("docker.info", "%s: %s" % (res.status, res.evidence()))
        else:
            errors = docker_info.get("ServerErrors")
            if errors:
                docker_info = None
                ctx.error("docker.info", "; ".join(str(e) for e in errors))
            else:
                ctx.ok("docker.info", "answering")
                for key, fact in DOCKER_FIELDS:
                    value = docker_info.get(key)
                    if key == "MemTotal" and isinstance(value, int):
                        value = value // (1024 * 1024)
                    if value is None or value == "":
                        ctx.absent(fact)
                    else:
                        ctx.ok(fact, value)
                ctx.ok("docker.rootless", any("rootless" in str(o) for o in
                                              docker_info.get("SecurityOptions") or []))
    if have["podman"]:
        res = ctx.run(["podman", "info", "--format", "json"])
        podman_info = parse_json(res.stdout) if res.ok else None
        if podman_info is None:
            ctx.error("podman.info", "%s: %s" % (res.status, res.evidence()))
        else:
            ctx.ok("podman.info", "answering")
            for path, fact in PODMAN_FIELDS:
                value = dig(podman_info, path)
                if value is None or value == "":
                    ctx.absent(fact)
                else:
                    ctx.ok(fact, value)
            images = ctx.run(["podman", "images", "-q"])
            ctx.from_result("podman.images.count", images,
                            lambda r: len([ln for ln in r.stdout.splitlines() if ln.strip()]))

    # GINI's own rule, replicated: GINI_ENGINE wins; else Docker if it answers; else Podman if it
    # answers; else Docker present-but-silent. Reported so the report says what gBuilder will DO.
    forced = os.environ.get("GINI_ENGINE", "")
    ctx.ok("gini.engine.env", forced or "(unset)")
    if forced:
        effective = "%s (forced by GINI_ENGINE)" % forced
    elif docker_info is not None:
        effective = "docker"
    elif podman_info is not None:
        effective = "podman"
    elif have["docker"]:
        effective = "docker (present but not answering)"
    elif have["podman"]:
        effective = "podman (present but not answering)"
    else:
        effective = "none"
    ctx.ok("gini.engine.effective", effective)

    # Does the id mapping podman is ACTUALLY using match what /etc/subuid grants today? These come
    # apart silently: storage records its mapping when first created, so a range assigned or changed
    # afterwards leaves every visible field looking right until a layer needs a high uid and the
    # pull dies with "insufficient UIDs or GIDs available in user namespace". The storage is stale,
    # not the range; the remedy table (S2) carries the fix.
    if ctx.platform != platforms.LINUX:
        for k in IDMAP_KEYS:
            ctx.na(k)
        return
    if podman_info is None:
        for k in IDMAP_KEYS:
            ctx.absent(k, "podman is not answering")
        return
    unshare = ctx.run(["podman", "unshare", "cat", "/proc/self/uid_map"])
    inuse = uid_map_outer_start(unshare.stdout) if unshare.ok else None
    granted = subuid_start(current_user())
    if inuse:
        ctx.ok("podman.idmap.inuse", inuse)
    else:
        ctx.error("podman.idmap.inuse", "%s: %s" % (unshare.status, unshare.evidence()))
    if granted:
        ctx.ok("podman.idmap.subuid", granted)
    else:
        ctx.absent("podman.idmap.subuid", "no /etc/subuid entry for this user")
    if inuse and granted:
        ctx.ok("podman.idmap.matches", inuse == granted)
    else:
        ctx.absent("podman.idmap.matches", "one side of the comparison is unknown")

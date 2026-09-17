"""``rootless`` — what rootless Podman needs from a Linux account: subordinate id ranges, user
namespaces, a runtime directory, lingering, cgroup delegation, and the helper binaries.

Every one of these fails LATE (at ``up``, not at install), and each is easy to have on one lab
machine and not the next. Linux only: Docker Desktop and ``podman machine`` run their engines in a
VM on macOS and Windows, so these facts are ``n/a`` there.
"""
from __future__ import annotations

import os

from .. import platforms
from . import probe
from ._common import first_line, read_text

KEYS = ("subuid", "subgid", "userns.max", "userns.unprivileged_clone", "xdg.runtime.dir",
        "xdg.runtime.exists", "session.type", "linger", "cgroup2.root.controllers",
        "cgroup2.user.controllers", "tool.fuse-overlayfs", "tool.slirp4netns", "tool.pasta",
        "tool.newuidmap", "tool.crun", "tool.runc")


def subid_range(user: str, path: str):
    text = read_text(path)
    if text is None:
        return None
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 3 and parts[0] == user:
            return "%s:%s" % (parts[1], parts[2])
    return ""


def _user():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, os.getuid()
    except Exception:
        return os.environ.get("USER", ""), None


@probe("rootless", platforms=[platforms.LINUX], na_keys=KEYS,
       describe="subuid/subgid, user namespaces, XDG_RUNTIME_DIR, lingering, cgroup delegation")
def rootless(ctx):
    user, uid = _user()
    for kind in ("subuid", "subgid"):
        rng = subid_range(user, "/etc/%s" % kind)
        if rng is None:
            ctx.absent(kind, "no /etc/%s" % kind)
        elif rng == "":
            ctx.absent(kind, "no entry for this user in /etc/%s" % kind)
        else:
            ctx.ok(kind, rng)

    for name, path in (("userns.max", "/proc/sys/user/max_user_namespaces"),
                       ("userns.unprivileged_clone", "/proc/sys/kernel/unprivileged_userns_clone")):
        text = read_text(path)
        if text is None:
            ctx.absent(name)
        else:
            ctx.ok(name, text.strip())

    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    ctx.ok("xdg.runtime.dir", runtime or "(unset)")
    ctx.ok("xdg.runtime.exists", bool(runtime) and os.path.isdir(runtime))
    ctx.ok("session.type", os.environ.get("XDG_SESSION_TYPE", "") or "(unset)")
    if user:
        ctx.from_result("linger", ctx.run(["loginctl", "show-user", user, "--property=Linger"], timeout=10),
                        lambda r: first_line(r.stdout).replace("Linger=", ""))
    else:
        ctx.absent("linger", "user unknown")

    root = read_text("/sys/fs/cgroup/cgroup.controllers")
    if root is None:
        ctx.absent("cgroup2.root.controllers", "cgroup v1 or unreadable")
        ctx.absent("cgroup2.user.controllers", "cgroup v1 or unreadable")
    else:
        ctx.ok("cgroup2.root.controllers", root.split())
        user_ctl = read_text("/sys/fs/cgroup/user.slice/user-%s.slice/user@%s.service/cgroup.controllers"
                             % (uid, uid)) if uid is not None else None
        if user_ctl is None:
            ctx.absent("cgroup2.user.controllers", "user slice unreadable")
        else:
            ctx.ok("cgroup2.user.controllers", user_ctl.split())

    for tool in ("fuse-overlayfs", "pasta", "newuidmap"):
        path = ctx.which(tool)
        if path:
            ctx.ok("tool.%s" % tool, path)
        else:
            ctx.absent("tool.%s" % tool)
    for tool in ("slirp4netns", "crun", "runc"):
        ctx.from_result("tool.%s" % tool, ctx.run([tool, "--version"], timeout=10),
                        lambda r: first_line(r.stdout))

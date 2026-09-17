"""``gini`` — what GINI itself is installed as: package versions in gBuilder's interpreter, whether
the core imports, the user's GINI settings, and which GINI images are present locally.

For images, the question that matters is the one the runtime asks. GINI resolves plain
``gini-<name>:latest`` (``gini.setup.images.local_name``), and a pull is re-tagged to that name.
Listing tags is not enough: GINI's own ``repair_tag`` documents Docker Desktop 27.4.0 listing
``gini-grouter latest`` while ``docker image inspect gini-grouter`` answers "No such image". So each
image gets ``image.<name>.runtime``: a read-only ``image inspect`` of exactly that name.

Different GINI versions, or one machine on an editable checkout and the rest on PyPI, are worth
ruling out before anyone blames the operating system.
"""
from __future__ import annotations

import json
import os
import re

from .. import platforms
from . import probe
from ._common import answering_engine, first_line, gbuilder_python, read_text

PACKAGES = ("gini-core", "gini-toolkit", "gini-teaching-center")
IMAGES = ("gini-xv6", "gini-grouter", "gini-pox", "gini-oszoo")

# Run inside gBuilder's interpreter. importlib.metadata exists from Python 3.8; direct_url.json
# says whether a distribution is an editable checkout.
METADATA = r"""
import json, sys
try:
    from importlib import metadata as m
except ImportError:
    print(json.dumps({"error": "no importlib.metadata"})); sys.exit(0)
out = {}
for name in sys.argv[1:]:
    try:
        d = m.distribution(name)
        editable = False
        try:
            du = json.loads(d.read_text("direct_url.json") or "{}")
            editable = bool(du.get("dir_info", {}).get("editable"))
        except Exception:
            pass
        out[name] = {"version": d.version, "editable": editable}
    except m.PackageNotFoundError:
        out[name] = None
print(json.dumps(out))
"""
IMPORT_CHECK = "import gini.domain, gini.services.bootstrap; print('ok')"
MAX_TAGS = 12


def _version_key(tag: str):
    """Natural order on the part after ':' so 6.11.2 sorts after 6.11.1 and 6.10.0."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", tag.rsplit(":", 1)[-1])]


def order_tags(tags):
    """Plain local tags first (``gini-xv6:latest``, what a local build produces), then registry tags
    newest first. Capped, so a machine with many versions cannot hide the ones that matter.
    First real macOS parity run (2026-09-17): an alphabetical cut of 6 hid both
    ``gini-xv6:latest`` and the newest 6.11.2 tags."""
    local = sorted(t for t in tags if "/" not in t.split(":", 1)[0])
    remote = sorted((t for t in tags if t not in local), key=_version_key, reverse=True)
    return (local + remote)[:MAX_TAGS]


@probe("gini", platforms=platforms.ALL,
       describe="GINI package versions, whether the core imports, settings, and local GINI images")
def gini(ctx):
    launcher = ctx.which("gbuilder")
    if launcher:
        ctx.ok("gbuilder.path", launcher)
    else:
        ctx.absent("gbuilder.path")

    py, _how = gbuilder_python(ctx)
    if py is None:
        for name in PACKAGES:
            ctx.absent("pkg.%s" % name, "gBuilder's interpreter was not found")
        ctx.absent("import", "gBuilder's interpreter was not found")
    else:
        res = ctx.run([py, "-c", METADATA] + list(PACKAGES), timeout=30)
        data = None
        if res.ok:
            try:
                data = json.loads(first_line(res.stdout))
            except ValueError:
                data = None
        for name in PACKAGES:
            if data is None or "error" in (data or {}):
                ctx.error("pkg.%s" % name, res.evidence() if not res.ok else "unreadable metadata")
            elif data.get(name) is None:
                ctx.absent("pkg.%s" % name)
            else:
                info = data[name]
                ctx.ok("pkg.%s" % name, "%s%s" % (info["version"], " (editable)" if info["editable"] else ""))
        ctx.from_result("import", ctx.run([py, "-c", IMPORT_CHECK], timeout=60),
                        lambda r: first_line(r.stdout))

    home = os.path.join(os.path.expanduser("~"), ".gini")
    ctx.ok("home", os.path.isdir(home))
    config = read_text(os.path.join(home, "config.json"))
    if config is None:
        ctx.absent("config.engine")
    else:
        try:
            engine = json.loads(config).get("engine")
            if engine:
                ctx.ok("config.engine", engine)
            else:
                ctx.absent("config.engine")
        except (ValueError, AttributeError):
            ctx.error("config.engine", "~/.gini/config.json is not valid JSON")

    engine = answering_engine(ctx)
    if engine is None:
        for img in IMAGES:
            ctx.absent("image.%s" % img, "no engine answering")
        return
    ctx.ok("image.engine", engine)
    res = ctx.run([engine, "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
    names = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()] if res.ok else None
    for img in IMAGES:
        if names is None:
            ctx.error("image.%s" % img, res.evidence())
            continue
        tags = [n for n in names if img in n]
        if tags:
            ordered = order_tags(tags)
            ctx.ok("image.%s" % img, ordered)
            ctx.ok("image.%s.count" % img, len(tags))
            pulled = [t for t in ordered if "/" in t.split(":", 1)[0]]
            if pulled:
                ctx.ok("image.%s.newest_pull" % img, pulled[0])
        else:
            ctx.absent("image.%s" % img)
        runtime_name = "%s:latest" % img
        inspect = ctx.run([engine, "image", "inspect", "--format", "{{.Id}}", runtime_name], timeout=30)
        if inspect.ok:
            ctx.ok("image.%s.runtime" % img, "resolves")
        elif runtime_name in (names or []):
            ctx.error("image.%s.runtime" % img, "listed, but %s does not resolve: %s"
                      % (runtime_name, inspect.evidence()))
        else:
            ctx.absent("image.%s.runtime" % img, "no %s, the name the runtime resolves" % runtime_name)

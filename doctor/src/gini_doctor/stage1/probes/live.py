"""``live`` — the round trip that reproduces what students report: run a container, bring up a
compose project, find the container, exec into it both ways GINI can, tear it down.

This is the one group that does more than look, so it asks first (consent). It creates only its
own uniquely named throwaway project from an image already on the machine, never pulls, and
removes everything it made even when a step fails. Registry reachability is the ``registry``
group's job, read-only.

The facts are ordered so the first failure is the real problem:
  run → container.by_label → exec.engine → exec.compose
A machine where the first three pass and only ``exec.compose`` fails is fine for current GINI.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from .. import platforms
from . import probe
from ._common import answering_engine, first_line

CONSENT = """
Runs a short container round trip on this machine's container engine, using an image that is
already present (nothing is downloaded):
  <engine> run --rm <image> true
  <engine> compose -p ginidoctor<pid> up -d        (one service that sleeps)
  <engine> exec / compose exec … sh -c 'echo ok'
  <engine> compose -p ginidoctor<pid> down         (and removes anything left)
Why: it shows whether containers start, whether compose really creates one, and whether GINI's
two ways of running a command inside it work. It removes everything it creates.
"""

PREFERRED = ("busybox", "alpine", "gini-")


def pick_image(names):
    usable = [n for n in names if n and "<none>" not in n]
    for want in PREFERRED:
        for n in usable:
            if want in n:
                return n
    return usable[0] if usable else None


@probe("live", platforms=platforms.ALL, consent=CONSENT,
       describe="start a throwaway container and compose project, exec into it, remove it (asks first)")
def live(ctx):
    engine = answering_engine(ctx)
    if engine is None:
        ctx.ok("skipped", "no engine answering")
        return
    res = ctx.run([engine, "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
    image = pick_image([ln.strip() for ln in res.stdout.splitlines()]) if res.ok else None
    if image is None:
        ctx.ok("skipped", "no local image to exercise (the doctor does not pull)")
        return
    ctx.ok("engine", engine)
    ctx.ok("image", image)
    ctx.from_result("run", ctx.run([engine, "run", "--rm", image, "true"], timeout=120), lambda r: "ok")

    project = "ginidoctor%d" % os.getpid()
    workdir = tempfile.mkdtemp(prefix=project)
    compose_file = os.path.join(workdir, "docker-compose.yml")
    base = [engine, "compose", "-f", compose_file, "-p", project]
    label = "label=com.docker.compose.project=%s" % project
    try:
        with open(compose_file, "w", encoding="utf-8") as fh:
            fh.write("name: %s\nservices:\n  probe:\n    image: %s\n    command: sh -c \"sleep 120\"\n"
                     % (project, image))
        up = ctx.run(base + ["up", "-d"], timeout=180)
        if up.returncode is not None:
            ctx.ok("compose.up", "exit %s" % up.returncode)
        else:
            ctx.error("compose.up", "%s: %s" % (up.status, up.evidence()))

        # THE question: `compose up` exiting 0 is not the same as a container existing.
        ps = ctx.run([engine, "ps", "-q", "--filter", label,
                      "--filter", "label=com.docker.compose.service=probe"], timeout=30)
        cid = first_line(ps.stdout) if ps.ok else ""
        if cid:
            ctx.ok("container.by_label", "found")
        else:
            ctx.absent("container.by_label", ps.evidence() if not ps.ok else "compose created no container")
        names = ctx.run([engine, "ps", "-a", "--filter", label, "--format", "{{.Names}}"], timeout=30)
        if names.ok and first_line(names.stdout):
            ctx.ok("container.name", first_line(names.stdout))
        else:
            ctx.absent("container.name")

        # The two ways GINI runs a command inside a container; they fail independently.
        if cid:
            ctx.from_result("exec.engine", ctx.run([engine, "exec", "-i", cid, "sh", "-c", "echo ok"], timeout=60),
                            lambda r: first_line(r.stdout))
        else:
            ctx.absent("exec.engine", "no container to exec into")
        ctx.from_result("exec.compose", ctx.run(base + ["exec", "-T", "probe", "sh", "-c", "echo ok"], timeout=60),
                        lambda r: first_line(r.stdout))

        # `compose ps -q <svc>` empty while the container exists by label is the old false
        # "did not start" report.
        psq = ctx.run(base + ["ps", "-q", "probe"], timeout=30)
        ctx.ok("compose.ps_q", "returned an id" if psq.ok and first_line(psq.stdout) else "EMPTY")

        down = ctx.run(base + ["down"], timeout=180)
        if down.returncode is not None:
            ctx.ok("compose.down", "exit %s" % down.returncode)
        else:
            ctx.error("compose.down", "%s: %s" % (down.status, down.evidence()))
    finally:
        # Leave nothing behind, even if a step above failed or raised.
        left = ctx.run([engine, "ps", "-aq", "--filter", label], timeout=30)
        ids = [ln.strip() for ln in left.stdout.splitlines() if ln.strip()] if left.ok else []
        ctx.ok("compose.down.leftovers", len(ids))
        if ids:
            ctx.run([engine, "rm", "-f"] + ids, timeout=60)
        shutil.rmtree(workdir, ignore_errors=True)

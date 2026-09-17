"""``xv6`` — the measurement that matches the complaint: how steady is the Machine Lab's feed?

Boots a real ``gini-xv6`` container and times 24 polls of its in-container agent from INSIDE the
container, which removes the host exec and leaves what the student sees. Not in the default
groups: it takes about a minute and starts a container, so it asks first.
"""
from __future__ import annotations

import os
import time

from .. import platforms
from . import probe
from ._common import answering_engine, first_line

IMAGE = "gini-xv6:latest"

CONSENT = """
Starts the gini-xv6 image (already on this machine; nothing is downloaded) as a container named
ginidoctorperf<pid>, waits for its agent, polls it 24 times from inside the container, then
removes the container. Takes about a minute.
Why: the timings show whether the Machine Lab's live feed is steady on this machine.
"""

READY = "import urllib.request as u; u.urlopen('http://127.0.0.1:5000/procs', timeout=5).read()"

FEED = """
import time, urllib.request as u
lat = []
for _ in range(24):
    t0 = time.perf_counter()
    try:
        u.urlopen("http://127.0.0.1:5000/procs", timeout=20).read()
        lat.append((time.perf_counter() - t0) * 1000)
    except Exception:
        lat.append(float("nan"))
    time.sleep(0.25)
good = sorted(x for x in lat if x == x)
if not good:
    print("all polls failed")
else:
    n = len(good)
    p = lambda q: good[min(n - 1, int(q * n))]
    stalls = len([x for x in good if x > 1000])
    print("n=%d min=%.0f med=%.0f p95=%.0f max=%.0f stalls>1s=%d failed=%d"
          % (n, good[0], p(0.5), p(0.95), good[-1], stalls, len(lat) - n))
"""


@probe("xv6", platforms=platforms.ALL, consent=CONSENT,
       describe="boot a real xv6 kernel and measure the Machine Lab's feed (~60s, asks first)")
def xv6(ctx):
    engine = answering_engine(ctx)
    if engine is None:
        ctx.ok("skipped", "no engine answering")
        return
    have = ctx.run([engine, "images", "-q", IMAGE], timeout=30)
    if not (have.ok and first_line(have.stdout)):
        ctx.ok("skipped", "no %s on this machine (the doctor does not pull)" % IMAGE)
        return
    name = "ginidoctorperf%d" % os.getpid()
    started = ctx.run([engine, "run", "-d", "--name", name, IMAGE], timeout=120)
    if not started.ok:
        ctx.error("boot", "could not start %s: %s" % (IMAGE, started.evidence()))
        return
    try:
        t0 = time.monotonic()
        ready = False
        while time.monotonic() - t0 < 40:
            if ctx.run([engine, "exec", name, "python3", "-c", READY], timeout=10).ok:
                ready = True
                break
            time.sleep(1)
        if not ready:
            ctx.error("boot", "agent never answered within 40s")
            return
        ctx.ok("boot.seconds", round(time.monotonic() - t0, 1))
        res = ctx.run([engine, "exec", "-i", name, "python3", "-"], timeout=120, stdin_text=FEED)
        ctx.from_result("poll", res, lambda r: first_line(r.stdout) or "no output")
    finally:
        ctx.run([engine, "rm", "-f", name], timeout=60)

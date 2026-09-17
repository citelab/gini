"""``perf`` — why the Machine Lab's feed stutters on some machines and not others.

xv6 runs under QEMU as a RISC-V guest on an x86 or ARM host: pure software emulation, one host
core flat out. So the lab's live feeds are only as steady as the CPU time that container gets, and
"the processor went to sleep" is close to literally what a powersave governor, a thermal cap or a
busy host does to it. None of these numbers mean much alone; each exists to be COMPARED with the
same number from a machine where the feed is smooth.
"""
from __future__ import annotations

import os
import platform as _pyplatform
import time

from .. import platforms
from . import probe
from ._common import first_line, read_text

LINUX_KEYS = ("cpu.governor", "cpu.freq.cur_khz", "cpu.freq.max_khz", "cpu.freq.limit_khz",
              "cpu.throttle.count", "cpu.temp_milli", "cgroup.user.cpu_max", "cgroup.root.cpu_max")


def bench(runs: int = 5, loops: int = 2000000):
    """A fixed amount of arithmetic, five times. The MEDIAN says how fast; the SPREAD says whether
    it stays that fast — and the spread is what matches a feed that is fine and then is not."""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        x = 0
        for i in range(loops):
            x += i
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    median = times[len(times) // 2]
    return round(median), round((times[-1] - times[0]) / median * 100) if median else 0


@probe("perf", platforms=platforms.ALL,
       describe="CPU governor, throttling, cgroup caps, load, and a short timing benchmark")
def perf(ctx):
    ctx.ok("cpu.cores", os.cpu_count())
    try:
        ctx.ok("loadavg", [round(x, 2) for x in os.getloadavg()])
    except (AttributeError, OSError):
        ctx.na("loadavg")

    if ctx.platform == platforms.LINUX:
        model = None
        for line in (read_text("/proc/cpuinfo", 256 * 1024) or "").splitlines():
            if line.lower().startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
        if model:
            ctx.ok("cpu.model", model)
        else:
            ctx.absent("cpu.model")
        base = "/sys/devices/system/cpu/cpu0/cpufreq"
        governor = read_text(base + "/scaling_governor")
        if governor is None:
            for k in ("cpu.governor", "cpu.freq.cur_khz", "cpu.freq.max_khz", "cpu.freq.limit_khz"):
                ctx.absent(k, "no cpufreq on this machine")
        else:
            ctx.ok("cpu.governor", governor.strip())
            for key, name in (("cpu.freq.cur_khz", "scaling_cur_freq"), ("cpu.freq.max_khz", "cpuinfo_max_freq"),
                              ("cpu.freq.limit_khz", "scaling_max_freq")):
                text = read_text("%s/%s" % (base, name))
                if text and text.strip().isdigit():
                    ctx.ok(key, int(text.strip()))
                else:
                    ctx.absent(key)
        for key, path in (("cpu.throttle.count", "/sys/devices/system/cpu/cpu0/thermal_throttle/core_throttle_count"),
                          ("cpu.temp_milli", "/sys/class/thermal/thermal_zone0/temp")):
            text = read_text(path)
            if text and text.strip().lstrip("-").isdigit():
                ctx.ok(key, int(text.strip()))
            else:
                ctx.absent(key)
        uid = os.getuid() if hasattr(os, "getuid") else None
        for key, path in (("cgroup.user.cpu_max", "/sys/fs/cgroup/user.slice/user-%s.slice/cpu.max" % uid),
                          ("cgroup.root.cpu_max", "/sys/fs/cgroup/cpu.max")):
            text = read_text(path)
            if text is None:
                ctx.absent(key)
            else:
                ctx.ok(key, text.strip())
    else:
        if ctx.platform == platforms.MACOS:
            ctx.from_result("cpu.model", ctx.run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5),
                            lambda r: first_line(r.stdout))
        else:
            model = os.environ.get("PROCESSOR_IDENTIFIER") or _pyplatform.processor()
            if model:
                ctx.ok("cpu.model", model)
            else:
                ctx.absent("cpu.model")
        for k in LINUX_KEYS:
            ctx.na(k)

    median, spread = bench()
    ctx.ok("host.cpu.median_ms", median)
    ctx.ok("host.cpu.spread_pct", spread)

"""``system`` — identify the machine. Always runs: a report that cannot be identified cannot be
compared against anything.

Everything here uses the standard library first and a command only where the platform offers
nothing better, so the group works on a machine whose PATH is broken.
"""
from __future__ import annotations

import ctypes
import os
import platform as _pyplatform
import shutil
import sys

from .. import platforms
from . import probe


def _os_release(path: str = "/etc/os-release") -> dict:
    data = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


def _meminfo_mb(path: str = "/proc/meminfo"):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _windows_memory_mb():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
        return None
    return int(stat.ullTotalPhys // (1024 * 1024))


@probe("system", platforms=platforms.ALL,
       describe="host identity: OS, kernel, CPU, memory, disk, the Python running the doctor")
def system(ctx):
    ctx.ok("os.family", ctx.platform)
    ctx.ok("arch", _pyplatform.machine() or "unknown")
    ctx.ok("kernel", _pyplatform.release() or "unknown")
    ctx.ok("cpus", os.cpu_count())

    if ctx.platform == platforms.LINUX:
        rel = _os_release()
        if rel:
            ctx.ok("os.id", rel.get("ID", "unknown"))
            ctx.ok("os.version", rel.get("VERSION_ID", "unknown"))
            ctx.ok("os.name", rel.get("PRETTY_NAME", "unknown"))
        else:
            ctx.absent("os.name", "no /etc/os-release")
        mem = _meminfo_mb()
        if mem is not None:
            ctx.ok("memory.total_mb", mem)
        else:
            ctx.error("memory.total_mb", "/proc/meminfo unreadable")
        ctx.ok("wsl", "microsoft" in _pyplatform.release().lower())

    elif ctx.platform == platforms.MACOS:
        ver = _pyplatform.mac_ver()[0]
        ctx.ok("os.id", "macos")
        ctx.ok("os.version", ver or "unknown")
        ctx.ok("os.name", "macOS %s" % ver if ver else "macOS")
        res = ctx.run(["sysctl", "-n", "hw.memsize"], timeout=5)
        ctx.from_result("memory.total_mb", res,
                        lambda r: int(r.text()) // (1024 * 1024) if r.text().isdigit() else r.text())
        ctx.na("wsl")

    else:  # windows
        rel, ver = _pyplatform.release(), _pyplatform.version()
        ctx.ok("os.id", "windows")
        ctx.ok("os.version", ver or "unknown")
        ctx.ok("os.name", ("Windows %s" % rel).strip())
        try:
            mem = _windows_memory_mb()
        except Exception as e:
            mem = None
            ctx.error("memory.total_mb", str(e))
        if mem is not None:
            ctx.ok("memory.total_mb", mem)
        ctx.na("wsl")

    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        ctx.ok("disk.home.free_gb", round(usage.free / (1024 ** 3), 1))
    except OSError as e:
        ctx.error("disk.home.free_gb", str(e))

    ctx.ok("doctor.python.version", "%d.%d.%d" % sys.version_info[:3])
    ctx.ok("doctor.python.executable", sys.executable or "unknown")

"""parity — does the Python doctor see what the shell doctor saw, on the same machine?

S3 deletes the shell engine, and that is only safe once both engines agree on real machines. Run
both on one machine and give this their two reports:

    sh doctor/src/gini_doctor/gini-doctor.sh --report > legacy.txt
    python -m gini_doctor.stage1 run --yes --out new.json
    python -m gini_doctor.stage1 parity legacy.txt new.json

The two reports name and spell facts differently (``yes`` / ``NO — …`` against true/false,
``Linger=yes`` against ``yes``, ``6.14.0 @ /path`` against ``6.14.0``), so each legacy key maps to
its new key with a rule for comparing values. Some legacy facts are gone on purpose, and are
listed as such rather than as failures: the username and uid (privacy), credential-file sizes
(never read now), the real pull (now a read-only manifest check), PATH's python3 (Stage 0 records
every interpreter instead).
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from .compare import render
from .report import ABSENT, ERROR, NA, OK, Report

# (legacy key regex, new key template, comparison kind). First match wins. In a template, {0} is
# the whole legacy key and {1} its first captured group.
MAP: List[Tuple[str, str, str]] = [
    (r"kernel", "system.kernel", "suffix"),
    (r"arch", "system.arch", "text"),
    (r"os\.id", "system.os.id", "os_id"),
    (r"os\.version", "system.os.version", "os_version"),
    (r"os\.pretty", "system.os.name", "text"),
    (r"cpus", "system.cpus", "int"),
    (r"mem\.total\.kb", "system.memory.total_mb", "kb_to_mb"),
    (r"(podman|docker)\.(path|version)", "engine.{0}", "text"),
    (r"gini\.engine\.(env|effective)", "engine.{0}", "text"),
    (r"podman\.rootless", "engine.podman.rootless", "bool"),
    (r"podman\.(network\.backend|cgroups\.version|cgroup\.manager|oci\.runtime|conmon|slirp4netns"
     r"|storage\.driver|storage\.graphroot|storage\.runroot|idmap\.inuse|idmap\.subuid)", "engine.{0}", "text"),
    (r"podman\.images\.count", "engine.podman.images.count", "int"),
    (r"podman\.info\.ok", "engine.podman.info", "answering"),
    (r"podman\.idmap\.matches", "engine.podman.idmap.matches", "bool"),
    (r"compose\.(provider|provider\.path|podman_compose\.version|docker_compose\.version|docker\.plugin)",
     "{0}", "text"),
    (r"(subuid|subgid)", "rootless.{0}", "subid"),
    (r"(userns\.max|userns\.unprivileged_clone|xdg\.runtime\.dir|session\.type)", "rootless.{0}", "text"),
    (r"xdg\.runtime\.exists", "rootless.xdg.runtime.exists", "bool"),
    (r"linger", "rootless.linger", "linger"),
    (r"(cgroup2\.root\.controllers|cgroup2\.user\.controllers)", "rootless.{0}", "words"),
    (r"(tool\.[\w-]+)", "rootless.{0}", "tool"),
    (r"reg\.etc\.containers\.registries\.conf\.unqualified",
     "registry.conf.system.unqualified-search-registries", "text"),
    (r"reg\.etc\.containers\.registries\.conf\.shortname", "registry.conf.system.short-name-mode", "text"),
    (r"(containers\.conf|storage\.conf)", "registry.conf.{0}", "present"),
    (r"gbuilder\.python", "qt.gbuilder.python", "via_gbuilder"),
    (r"gbuilder\.python\.version", "qt.gbuilder.python.version", "via_gbuilder"),
    (r"(pyside6|pyside6\.offscreen)", "qt.{0}", "via_gbuilder"),
    (r"(lib\.[\w-]+)", "qt.{0}", "int"),
    (r"display", "qt.display", "text"),
    (r"wayland", "qt.wayland", "text"),
    (r"qt\.qpa", "qt.qpa", "text"),
    (r"gbuilder\.path", "gini.gbuilder.path", "text"),
    (r"(pkg\.gini-[\w-]+)", "gini.{0}", "pkg"),
    (r"gini\.import", "gini.import", "via_gbuilder"),
    (r"gini\.home", "gini.home", "present"),
    (r"gini\.config\.engine", "gini.config.engine", "config_engine"),
    (r"image\.(gini-[\w-]+)", "gini.{0}", "image"),
    (r"live\.(image|run|container\.name|exec\.engine|exec\.compose|compose\.ps_q|compose\.up|compose\.down)",
     "live.{0}", "text"),
    (r"live\.container\.by_label", "live.container.by_label", "found"),
    (r"live\.compose\.down\.leftovers", "live.compose.down.leftovers", "int"),
    (r"perf\.(loadavg)", "perf.loadavg", "skip_noisy"),
    (r"perf\.cpu\.(model|cores|governor)", "{0}", "text"),
    (r"perf\.cpu\.freq\.(cur|max|limit)\.khz", "perf.cpu.freq.{1}_khz", "skip_noisy"),
    (r"perf\.cpu\.throttle\.count", "perf.cpu.throttle.count", "int"),
    (r"perf\.cpu\.temp\.milli", "perf.cpu.temp_milli", "skip_noisy"),
    (r"perf\.cgroup\.(user|root)\.cpu_max", "{0}", "text"),
    (r"perf\.host\.cpu", "perf.host.cpu.median_ms", "skip_noisy"),
    (r"perf\.xv6\.boot\.s", "xv6.boot.seconds", "skip_noisy"),
    (r"perf\.xv6\.poll", "xv6.poll", "skip_noisy"),
]

# Gone on purpose; reported as such, never as a disagreement.
DROPPED: List[Tuple[str, str]] = [
    (r"host|doctor\.version|doctor\.groups", "in the report header"),
    (r"user|uid", "not collected: usernames do not leave the machine"),
    (r"disk\.home\.avail", "now system.disk.home.free_gb, which always differs"),
    (r"auth\..*", "not collected: credential files are never read"),
    (r"live\.pull.*", "replaced by the read-only registry.anonymous / registry.engine checks"),
    (r"reg\..*\.registries\.conf(\..*)?|reg\.\..*", "registries.conf presence is registry.conf.system/user"),
    (r"python3\..*|pip\.version", "Stage 0 records every interpreter as stage0.python.*"),
    (r"compose\.podman\.raw|compose\.plugin\..*", "raw banner and plugin paths are kept differently"),
    (r"image\.gini-[\w-]+\.local", "folded into gini.image.*"),
    (r"live\.skipped|perf\.xv6", "run-state, not a machine fact"),
]

LEGACY_ABSENT = re.compile(r"^(absent( @ -)?|\(empty\)|\(unset\)|MISSING.*|n/a.*|unknown|\?"
                           r"|no /etc/os-release|cgroup v1 or unreadable|unreadable)$")
# The legacy doctor printed real home paths; Stage 1 reduces them to ~ on purpose.
LEGACY_HOME = re.compile(r"(?:/Users|/home)/[^/\s]+")


class Row(NamedTuple):
    legacy_key: str
    new_key: str
    legacy: str
    new: str


class Parity(NamedTuple):
    agree: List[Row]
    disagree: List[Row]
    missing_new: List[Row]
    unmapped: List[str]
    dropped: List[Tuple[str, str]]
    skipped: List[Row]
    explained: List[Row]
    improved: List[Row]     # the legacy doctor could not read this here; Stage 1 can, or knows it is n/a


# Where gBuilder is not installed, the legacy doctor fell back to PATH's python3 and reported that
# python's modules ("No module named PySide6") as if they were gBuilder's: the false alarm its own
# comments describe. The new doctor reports the interpreter as not found instead. Legacy facts read
# through that interpreter are "explained", not disagreements, on such a machine.
NO_GBUILDER = "the legacy doctor probed PATH's python3; gBuilder's interpreter is not installed here"


def parse_legacy(text: str) -> Dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            out[k.strip()] = v.strip()
    return out


def _new_key(legacy_key: str) -> Optional[Tuple[str, str]]:
    for pattern, template, kind in MAP:
        m = re.fullmatch(pattern, legacy_key)
        if m:
            return template.format(legacy_key, *m.groups()), kind
    return None


def _dropped(legacy_key: str) -> Optional[str]:
    for pattern, why in DROPPED:
        if re.fullmatch(pattern, legacy_key):
            return why
    return None


def _truthy(text: str) -> Optional[bool]:
    t = text.strip().lower()
    if t in ("yes", "true", "1", "present"):
        return True
    if t in ("no", "false", "0", "absent") or t.startswith("no "):
        return False
    if text.strip().startswith("NO"):
        return False
    return None


def agrees(kind: str, legacy: str, fact: Optional[dict], report: Optional[Report] = None) -> bool:
    status = (fact or {}).get("status")
    value = (fact or {}).get("value")
    shown = render(fact) if fact else ""
    legacy = LEGACY_HOME.sub("~", legacy)
    legacy_absent = bool(LEGACY_ABSENT.match(legacy)) or legacy.startswith("ERROR(")
    if fact is not None and status != OK and legacy == fact.get("detail"):
        return True
    if status in (ABSENT, NA, ERROR) or fact is None:
        if kind == "present" and _truthy(legacy) is False:
            return True
        return legacy_absent or (status == ERROR and ("ERROR" in legacy or "FAILED" in legacy or "NO" in legacy))
    if kind in ("bool", "present"):
        return _truthy(legacy) is (value if isinstance(value, bool) else _truthy(shown))
    if kind == "int":
        digits = re.sub(r"[^\d-]", "", legacy)
        return digits != "" and str(value) == str(int(digits))
    if kind == "kb_to_mb":
        return legacy.isdigit() and abs(int(legacy) // 1024 - int(value)) <= 1
    if kind == "suffix":
        return legacy.endswith(str(value))
    if kind == "subid":
        return legacy.split(":", 1)[-1] == str(value)
    if kind == "linger":
        return legacy.replace("Linger=", "").strip() == str(value)
    if kind == "words":
        return legacy.split() == (value if isinstance(value, list) else str(value).split())
    if kind == "tool":
        return legacy.startswith(shown) or shown.startswith(legacy.split(" (")[0])
    if kind == "pkg":
        return legacy.split(" @ ", 1)[0] == str(value).split(" ", 1)[0]
    if kind == "answering":
        return _truthy(legacy) is True
    if kind == "os_id":
        return legacy.lower() == str(value).lower() or (legacy == "Darwin" and value == "macos")
    if kind == "os_version":
        # On macOS the legacy doctor fell back to `uname -r`, the Darwin kernel version.
        kernel = report.value("system.kernel") if report is not None else None
        return legacy == str(value) or (report is not None and report.platform == "macos" and legacy == kernel)
    if kind == "via_gbuilder":
        return legacy == shown
    if kind == "config_engine":
        return str(value) in legacy
    if kind == "found":
        return legacy.startswith("found") == (value == "found")
    if kind == "image":
        # Legacy listed tags space-separated and cut the line at 120 characters, so its last tag may
        # be partial; every complete legacy tag must be in the new list (or beyond its cap, counted).
        tags = legacy.split()
        if len(legacy) >= 120 and tags:
            tags = tags[:-1]
        new_tags = value if isinstance(value, list) else [value]
        return all(t in new_tags for t in tags) or len(new_tags) >= 12
    return legacy == shown


def parity(legacy: Dict[str, str], report: Report) -> Parity:
    agree, disagree, missing, unmapped, dropped, skipped, explained, improved = [], [], [], [], [], [], [], []
    no_gbuilder = (report.facts.get("qt.gbuilder.python") or {}).get("status") == ABSENT
    for key in sorted(legacy):
        value = legacy[key]
        why = _dropped(key)
        mapped = _new_key(key)
        if mapped is None:
            if why:
                dropped.append((key, why))
            else:
                unmapped.append(key)
            continue
        new_key, kind = mapped
        fact = report.facts.get(new_key)
        row = Row(key, new_key, value, render(fact) if fact else "(not collected)")
        if kind == "skip_noisy":
            skipped.append(row)
        elif no_gbuilder and (kind == "via_gbuilder" or key.startswith("pkg.")):
            explained.append(row)
        elif fact is None:
            missing.append(row)
        elif agrees(kind, value, fact, report):
            agree.append(row)
        elif fact.get("status") == NA or (fact.get("status") == OK and LEGACY_ABSENT.match(value)):
            # The legacy doctor ran a Linux probe on another platform (and got "?" or a Linux-shaped
            # non-answer), or could not read something Stage 1 reads. Not a regression.
            improved.append(row)
        else:
            disagree.append(row)
    return Parity(agree, disagree, missing, unmapped, dropped, skipped, explained, improved)


def format_text(p: Parity) -> str:
    out = ["", "parity: %d agree, %d disagree, %d improved, %d explained, %d not collected by the new doctor, %d unmapped"
           % (len(p.agree), len(p.disagree), len(p.improved), len(p.explained), len(p.missing_new),
              len(p.unmapped)), ""]
    if p.disagree:
        out.append("DISAGREE (these block S3 until explained):")
        for r in p.disagree:
            out.append("  %s -> %s\n      legacy: %s\n      new:    %s" % (r.legacy_key, r.new_key, r.legacy, r.new))
        out.append("")
    if p.missing_new:
        out.append("not collected by the new doctor (a group not run, or a gap):")
        for r in p.missing_new:
            out.append("  %s -> %s   (legacy: %s)" % (r.legacy_key, r.new_key, r.legacy))
        out.append("")
    if p.unmapped:
        out.append("legacy keys with no mapping (add one to parity.MAP or DROPPED):")
        out.extend("  " + k for k in p.unmapped)
        out.append("")
    if p.improved:
        out.append("improved (the legacy doctor could not read these on this platform):")
        out.extend("  %s   legacy: %s   new: %s" % (r.legacy_key, r.legacy, r.new) for r in p.improved)
        out.append("")
    if p.explained:
        out.append("explained (%s):" % NO_GBUILDER)
        out.extend("  %s   (legacy: %s)" % (r.legacy_key, r.legacy) for r in p.explained)
        out.append("")
    if p.dropped:
        out.append("dropped on purpose:")
        seen = set()
        for key, why in p.dropped:
            if why not in seen:
                seen.add(why)
                out.append("  %s ... %s" % (key, why))
        out.append("")
    if p.skipped:
        out.append("not compared (changes from run to run): %s" % ", ".join(r.legacy_key for r in p.skipped))
        out.append("")
    return "\n".join(out)

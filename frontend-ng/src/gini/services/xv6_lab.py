"""The student's own kernel files: where they live on the host, and how they reach the tree.

The shadow labs gave students one directory (`kernel/shadows/`) bind-mounted into the kernel
source, which works because every file they may edit lives in that one directory. A syscall lab
does not have that shape: it edits files spread across `kernel/` and `user/`, and mounting those
wholesale would hand over `trap.c`, every `.o`, and the build itself.

So: mount ONE flat folder, and SYMLINK the tree at the files inside it.

Why a symlink and not a per-file bind mount — this is the whole reason the design works. A
single-file bind mount binds an INODE, and editors save by writing a new file and renaming it
over the old one, which leaves the mount pointing at an inode nobody can see any more. The same
fact is why `kernel/shadows/` is mounted as a directory (see `services/compiler.py`). A symlink
resolves by PATH, so a rename on the other side is invisible to it.

The split of work here is deliberate. This module owns the HOST side — the folder, the seeds, and
reading files back for the tracker — and is testable without a container. The in-container half
(copying pristine files, making the links, registering user programs in the build) is a script
this module generates and somebody else runs, for the same reason `_populate_overlay_hosts`
builds its script on the GUI thread and hands over only finished strings.
"""
from __future__ import annotations

import hashlib
import os
import shlex
from pathlib import Path

#: Where the tree's files are linked from, inside the container.
MOUNT = "/opt/xv6-lab"
#: The image's untouched copy of each linked file, kept INSIDE the mount — so it lands on the
#: host, where "have I changed this?" and Revert are a file comparison and a file copy rather than
#: an exec into a container that may not be running. A student who has stopped their machine can
#: still see what they changed and put it back.
PRISTINE = "/opt/xv6-lab/.pristine"
PRISTINE_DIR = ".pristine"

#: Insert one `$U/_<prog>` row into the Makefile's UPROGS list, after `UPROGS=\`.
_UPROG_AWK = r'{print} /^UPROGS=\\$/{print "\t$U/_" p "\\"}'

#: Per file. A kernel file is a few thousand lines; anything past this is not source, and a
#: submission is uploaded over a student's own connection.
MAX_SOURCE_BYTES = 512 * 1024


def _gini_home() -> Path:
    # Same rule as app.paths.gini_home, replicated so this service avoids an `app` import cycle —
    # exactly as services/compiler.py and services/xv6_shadows.py do, for the same reason.
    return Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()


def sane_name(machine_name: str) -> str:
    return "".join(c if (c.isalnum() or c in "_.-") else "-" for c in str(machine_name or ""))


def lab_dir(machine_name: str, folder: str = "xv6-lab") -> Path:
    """Where one machine's editable kernel files live on the host.

    ONE definition of this path, like `xv6_shadows.shadow_dir`: the compiler writes the mount from
    it, the face lists it, and a submission gathers it. Two copies of a path rule is one copy too
    many — if they disagreed, GINI would show an empty folder while the student's work sat
    somewhere else, and nothing would look wrong.
    """
    return _gini_home() / sane_name(folder or "xv6-lab") / sane_name(machine_name)


def active_spec():
    """The assignment this machine is doing, or None.

    For now: `GINI_LAB` names one, otherwise the single shipped pack. When missions come back from
    the Teaching Center this is where the armed activity will be read instead — the rest of the
    module already takes a spec rather than looking one up, so only this function changes.
    """
    try:
        from ..domain import lab_spec as _ls
    except Exception:                              # noqa: BLE001 — a lab must never block a Run
        return None
    try:
        wanted = os.environ.get("GINI_LAB", "").strip()
        if wanted:
            return _ls.get(wanted)
        packs = _ls.catalog()
        return packs[0] if len(packs) == 1 else None
    except Exception:                              # noqa: BLE001
        return None


def seed_host_files(spec, machine_name: str) -> list[str]:
    """Write the files the assignment CARRIES, if they are not there yet. Returns what was written.

    Only files with `seed` text: a header xv6 does not have, and the starter program. Everything
    else is copied from the image's own copy by the link script, because the whole point is that
    the student starts from the real kernel rather than from something we transcribed.

    Never overwrites. A student who deleted a line is not asking for their file back — that is
    what Revert is for, and doing it silently on every Run would eat their work.
    """
    d = lab_dir(machine_name, getattr(spec, "machine_folder", "xv6-lab"))
    d.mkdir(parents=True, exist_ok=True)
    (d / PRISTINE_DIR).mkdir(exist_ok=True)
    written = []
    for f in getattr(spec, "files", ()):
        if not f.seed:
            continue
        # The seed IS the pristine copy for these. Without this, the two files the assignment
        # carries — the header xv6 lacks and the starter program — are the only ones a student
        # cannot Revert, which is backwards: the starter program is the likeliest thing they
        # break while working out what the call should print.
        orig = d / PRISTINE_DIR / f.name
        if not orig.exists():
            orig.write_text(f.seed, encoding="utf-8")
        p = d / f.name
        if p.exists():
            continue
        p.write_text(f.seed, encoding="utf-8")
        written.append(f.name)
    return written


def read_file(machine_name: str, name: str, folder: str = "xv6-lab") -> str | None:
    """One of the student's files, or None. The reader `lab_spec.evaluate` wants."""
    p = lab_dir(machine_name, folder) / name
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def reader_for(spec, machine_name: str):
    folder = getattr(spec, "machine_folder", "xv6-lab")
    return lambda name: read_file(machine_name, name, folder)


def pristine_reader_for(spec, machine_name: str):
    """The image's untouched copy of each file — what a `kind: edited` check compares against."""
    folder = getattr(spec, "machine_folder", "xv6-lab")

    def read(name):
        try:
            return pristine_path(machine_name, name, folder).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return None
    return read


def digest(text: str) -> str:
    """What the chain records and a marker checks. Bytes, UTF-8, no normalisation — the hash is a
    statement about the file as it sits on disk, not about a cleaned-up version of it."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def hashes_for(spec, machine_name: str) -> dict:
    """`{filename: {"sha256", "lines"}}` — what a `build` entry records and a submission carries.

    The lab's OWN file list, not a fixed tuple. `xv6_shadows.SHADOW_FILES` is three names, so an
    OS submission for any other assignment would travel with the wrong files or with none.
    """
    out: dict[str, dict] = {}
    folder = getattr(spec, "machine_folder", "xv6-lab")
    for f in getattr(spec, "files", ()):
        text = read_file(machine_name, f.name, folder)
        if text is None:
            continue
        out[f.name] = {"sha256": digest(text), "lines": text.count("\n") + 1,
                       "bytes": len(text.encode("utf-8"))}
    return out


def link_script(spec) -> str:
    """The shell that runs INSIDE the container, once per Run, to attach the lab to the tree.

    Idempotent by construction, because it runs on every Run and the second run must be a no-op:

    * `[ -f $MOUNT/x ]` — seed from the image only when the student has nothing yet.
    * `[ -L kernel/x ]` — and this one is the safety of the whole scheme. On a second pass the
      tree file is ALREADY a symlink, and copying *that* aside as the pristine copy would replace
      the only good original with a link to the student's edited file. Revert would then restore
      their own broken code, which is the one thing Revert must never do.

    Registering user programs here rather than handing over the Makefile is deliberate: the build
    system is not the lab, there is no pedagogy in `UPROGS`, and a broken line-continuation
    produces an error about nothing the student was thinking about.
    """
    lines = [
        "set -e",
        "cd /opt/xv6-riscv",
        f"mkdir -p {PRISTINE} {MOUNT}",
    ]
    for f in getattr(spec, "files", ()):
        name, tree = shlex.quote(f.name), shlex.quote(f.tree)
        # Written as nested `if` blocks rather than `[ -f x ] && cp x y`: under `set -e` a false
        # test at the end of an && chain is a non-zero status, and the script would exit there —
        # silently, on the second Run, having linked only the files before it.
        lines += [
            f"if [ ! -f {MOUNT}/{name} ]; then",
            f"  if [ -f {tree} ]; then cp {tree} {MOUNT}/{name}; fi",
            "fi",
            f"if [ ! -L {tree} ] && [ -f {tree} ] && [ ! -f {PRISTINE}/{name} ]; then",
            f"  cp {tree} {PRISTINE}/{name}",
            "fi",
            f"if [ ! -L {tree} ]; then ln -sf {MOUNT}/{name} {tree}; fi",
        ]
    for prog in getattr(spec, "uprogs", lambda: ())():
        # `awk -v` rather than splicing the name into the awk program: unquoted in awk source
        # it would be read as a VARIABLE, expand to empty, and register `$U/_` for every lab.
        # The Makefile continues lines with a trailing backslash, so the inserted row needs
        # one too — `"\\"` in awk is a single backslash.
        lines += [
            f"if ! grep -q '_{prog}' Makefile; then",
            "  awk -v p=" + shlex.quote(prog) + " " + shlex.quote(_UPROG_AWK) +
            " Makefile > /tmp/gini_mk && mv /tmp/gini_mk Makefile",
            "fi",
        ]
    lines.append("echo gini-lab-linked")
    return "\n".join(lines)


def pristine_path(machine_name: str, name: str, folder: str = "xv6-lab") -> Path:
    return lab_dir(machine_name, folder) / PRISTINE_DIR / name


def state_of(spec, machine_name: str, name: str) -> str:
    """`"edited"` | `"untouched"` | `"missing"` | `"unknown"` — what the file list shows.

    "unknown" is honest rather than tidy: before the first Run there is no pristine copy, so
    whether the file differs from the image's cannot be answered, and guessing "untouched" would
    show a student a green tick for work they have not started.
    """
    folder = getattr(spec, "machine_folder", "xv6-lab")
    mine = read_file(machine_name, name, folder)
    if mine is None:
        return "missing"
    orig = pristine_path(machine_name, name, folder)
    try:
        return "edited" if mine != orig.read_text(encoding="utf-8", errors="replace") else "untouched"
    except OSError:
        return "unknown"


def revert(spec, machine_name: str, name: str) -> tuple[bool, str]:
    """Put ONE file back to the image's copy — a host-side copy, so it works with the machine down.

    Per file, because throwing away a broken kalloc.c should not cost a student the syscall.c they
    finally got working. The kernel does not change until they press Load, which is the same rule
    as every other edit.
    """
    folder = getattr(spec, "machine_folder", "xv6-lab")
    orig = pristine_path(machine_name, name, folder)
    if not orig.is_file():
        return False, f"no original of {name} yet — press Run once so GINI can take a copy"
    try:
        (lab_dir(machine_name, folder) / name).write_text(
            orig.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except OSError as e:
        return False, f"could not revert {name}: {e}"
    return True, f"{name} put back — press Load to build it"

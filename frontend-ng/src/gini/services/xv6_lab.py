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

**One assignment is armed per MACHINE**, and that is forced by the kernel rather than chosen
here: two syscall assignments both edit `syscall.h`, `syscall.c`, `sysproc.c`, `user.h` and
`usys.pl`, so two of them wired into one tree collide in all five. Across machines there is no
collision and none is invented — a student may arm A-Lab 01 on M1 and A-Lab 02 on M2 and work
on both. See `docs/design/user-code-lab.md`.

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
import shutil
from pathlib import Path

#: Where one machine's lab directory is mounted inside the container.
#:
#: The mount is the PARENT of the armed assignment's folder, not the folder itself, and that is
#: load-bearing. A bind mount's host side is fixed when the compose file is written, so mounting
#: the assignment's own folder meant arming a different one could not take effect until the next
#: Run. Mounting the parent makes the container path constant and leaves the choice of
#: subdirectory to the link script — so arming becomes one exec, on a machine that is already up.
MOUNT = "/opt/xv6-lab"
#: Names the assignment armed on this machine. One line, in the mount root.
ARMED = ".armed"
#: The image's untouched copy of each linked file, kept in the mount root — so it lands on the
#: host, where "have I changed this?" and Revert are a file comparison and a file copy rather
#: than an exec into a container that may not be running. A student who has stopped their machine
#: can still see what they changed and put it back.
#:
#: One per MACHINE, shared by every assignment on it, because the image's copy of `syscall.h` is
#: the image's copy of `syscall.h` — it does not depend on what is armed. Per assignment it also
#: would not work: after switching, the tree's file is a symlink into the assignment just left,
#: so there is no original left to copy from and the new folder would start empty.
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


def machine_root(machine_name: str) -> Path:
    """Everything one machine's assignments own — and the directory that is MOUNTED.

    One subdirectory per assignment the machine has ever armed, plus `.armed` naming the current
    one. Work is never deleted by switching; it is left in its own folder.
    """
    return _gini_home() / "xv6-lab" / sane_name(machine_name)


def lab_dir(machine_name: str, spec_id: str) -> Path:
    """Where ONE assignment's editable kernel files live on the host, for one machine.

    ONE definition of this path, like `xv6_shadows.shadow_dir`: the compiler writes the mount from
    it, the face lists it, and a submission gathers it. Two copies of a path rule is one copy too
    many — if they disagreed, GINI would show an empty folder while the student's work sat
    somewhere else, and nothing would look wrong.
    """
    return machine_root(machine_name) / sane_name(spec_id)


def pristine_path(machine_name: str, name: str) -> Path:
    return machine_root(machine_name) / PRISTINE_DIR / name


# --------------------------------------------------------------------------- #
# arming
# --------------------------------------------------------------------------- #

def armed_id(machine_name: str) -> str:
    """The assignment armed on this machine, or `""`.

    A file beside the work, deliberately, rather than a key in `Settings` or a property on the
    device. Not Settings, because Settings is per-installation and arming is per-machine. Not the
    device, because that would put it in the `.gini` file — and the reason is the one the code
    already gives about `claimed_boards`: a topology a student downloads from a classmate, or
    from the course page, must not silently re-arm their assignment.
    """
    try:
        return (machine_root(machine_name) / ARMED).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def arm(machine_name: str, spec_id: str) -> bool:
    """Point this machine at an assignment. Returns whether it stuck.

    Nothing is copied, moved or deleted here — the previous assignment's folder stays exactly as
    it was. All that changes is which subdirectory the next link script will wire into the tree.
    """
    root = machine_root(machine_name)
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / ARMED).write_text(f"{sane_name(spec_id)}\n", encoding="utf-8")
    except OSError:
        return False
    return True


def active_spec(machine_name: str = ""):
    """The assignment armed on this machine, or None.

    `GINI_LAB` still overrides, because a developer testing a pack that is not shipped yet should
    not have to arm it through the UI first.

    The rule this replaced was "the single shipped pack, if there is exactly one" — which
    returned None the moment a SECOND assignment shipped, and None makes the Machine Lab drop the
    User Code face entirely. Adding an assignment made every assignment disappear, silently, with
    the YAML sitting right there on disk. The only way back was an environment variable.
    """
    try:
        from ..domain import lab_spec as _ls
    except Exception:                              # noqa: BLE001 — a lab must never block a Run
        return None
    try:
        wanted = os.environ.get("GINI_LAB", "").strip() or armed_id(machine_name)
        return _ls.get(wanted) if wanted else None
    except Exception:                              # noqa: BLE001
        return None


def ensure_layout(machine_name: str) -> str:
    """Move a pre-hub folder down into its assignment's subdirectory. Returns the id, or `""`.

    Before the hub there was one assignment and its files sat directly in the machine's
    directory, because that directory WAS the mount. The mount is now the parent, so those files
    need to move down one level into a folder named for the assignment they belong to.

    Moved rather than copied: two copies of a student's work that drift apart is a worse outcome
    than either copy alone, and the one they can see would be the one they are not editing.

    Refuses to guess. If the loose files are there and more than one assignment is shipped, there
    is no way to know which one they belong to, so they are left alone — filing a student's work
    under the wrong assignment is not recoverable by them.
    """
    root = machine_root(machine_name)
    try:
        # The tell is a student's file sitting loose in the mount root. In the new layout the
        # root holds only directories — one per assignment, plus `.pristine` — and `.armed`.
        loose = [p for p in root.iterdir() if p.is_file() and p.name != ARMED]
    except OSError:
        return ""                       # nothing here yet
    if not loose:
        return ""
    try:
        from ..domain import lab_spec as _ls
        packs = _ls.catalog()
    except Exception:                              # noqa: BLE001
        return ""
    if len(packs) != 1:
        return ""
    spec_id = sane_name(packs[0].id)
    dest = root / spec_id
    try:
        if dest.exists() and any(dest.iterdir()):
            return ""                   # occupied — never write over an assignment's folder
        dest.mkdir(parents=True, exist_ok=True)
        for child in loose:
            shutil.move(str(child), str(dest / child.name))
    except OSError:
        return ""
    # They were working on it, so it stays armed — a migration should be invisible.
    arm(machine_name, spec_id)
    return spec_id


# --------------------------------------------------------------------------- #
# container-side paths
# --------------------------------------------------------------------------- #

def mount_dir(spec) -> str:
    """Where the armed assignment's files sit INSIDE the container."""
    return f"{MOUNT}/{sane_name(getattr(spec, 'id', '') or 'lab')}"


def pristine_mount() -> str:
    return f"{MOUNT}/{PRISTINE_DIR}"


# --------------------------------------------------------------------------- #
# the host side of one assignment
# --------------------------------------------------------------------------- #

def seed_host_files(spec, machine_name: str) -> list[str]:
    """Write the files the assignment CARRIES, if they are not there yet. Returns what was written.

    Only files with `seed` text: a header xv6 does not have, and the starter program. Everything
    else is copied from the image's own copy by the link script, because the whole point is that
    the student starts from the real kernel rather than from something we transcribed.

    Never overwrites. A student who deleted a line is not asking for their file back — that is
    what Revert is for, and doing it silently on every Run would eat their work.
    """
    d = lab_dir(machine_name, getattr(spec, "id", ""))
    d.mkdir(parents=True, exist_ok=True)
    (machine_root(machine_name) / PRISTINE_DIR).mkdir(parents=True, exist_ok=True)
    written = []
    for f in getattr(spec, "files", ()):
        if not f.seed:
            continue
        # The seed IS the pristine copy for these. Without this, the two files the assignment
        # carries — the header xv6 lacks and the starter program — are the only ones a student
        # cannot Revert, which is backwards: the starter program is the likeliest thing they
        # break while working out what the call should print.
        orig = pristine_path(machine_name, f.name)
        if not orig.exists():
            orig.write_text(f.seed, encoding="utf-8")
        p = d / f.name
        if p.exists():
            continue
        p.write_text(f.seed, encoding="utf-8")
        written.append(f.name)
    return written


def read_file(machine_name: str, name: str, spec_id: str) -> str | None:
    """One of the student's files, or None. The reader `lab_spec.evaluate` wants."""
    p = lab_dir(machine_name, spec_id) / name
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def reader_for(spec, machine_name: str):
    spec_id = getattr(spec, "id", "")
    return lambda name: read_file(machine_name, name, spec_id)


def pristine_reader_for(spec, machine_name: str):
    """The image's untouched copy of each file — what a `kind: edited` check compares against."""
    def read(name):
        try:
            return pristine_path(machine_name, name).read_text(
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
    spec_id = getattr(spec, "id", "")
    for f in getattr(spec, "files", ()):
        text = read_file(machine_name, f.name, spec_id)
        if text is None:
            continue
        out[f.name] = {"sha256": digest(text), "lines": text.count("\n") + 1,
                       "bytes": len(text.encode("utf-8"))}
    return out


def run_test(provider, prog: str, args: str = "") -> tuple[bool, list[str]]:
    """Run an assignment's test and return `(ok, what it printed)` — cleaned, capped, recordable.

    The cleaning and both caps are **`console_tap`'s, imported rather than reimplemented**. That
    module has been turning a terminal's bytes into "they ran this, and it printed that" since
    the C-Labs and T-Labs shipped, and it is tested and relied on. A second copy of its regexes
    is exactly how this path first shipped with output that kept ANSI escapes and had no line cap
    at all — the escapes then broke the echo-stripping the whole bracket exists to get right.

    **Known duplication, deliberately left.** The SPLITTING half — telling the shell's echo and
    the trailing prompt from the program's own output — still lives in `domain.xv6.run_output`
    rather than in `ConsoleTap`, because ConsoleTap is keystroke-driven: it watches what a
    student types into a pty and closes a record when the next command starts. This path has no
    keystrokes at all. GINI issues the command over HTTP and waits for the prompt, so the two
    halves cannot share a producer without reworking ConsoleTap to accept a command it was told
    about rather than one it watched being typed — a change to code three lab families depend on,
    for no behaviour a student would notice. Written up in `docs/design/user-code-lab.md` under
    "Known duplication" for the next re-engineering instead.

    One deliberate difference from ConsoleTap: interior blank lines are KEPT. It drops them,
    which is right for a terminal transcript and wrong here — a blank line a student's program
    printed between two sections is theirs, and a marker should see the output as it appeared.
    """
    from ..domain.xv6 import run_output
    from .console_tap import MAX_LINE, MAX_LINES, clean
    if provider is None or not prog or not hasattr(provider, "run_and_capture"):
        return False, []
    ok, raw = provider.run_and_capture(prog, args)
    if not ok:
        return False, []
    cmd = (prog + (" " + args if args else "")).strip()
    lines = run_output(clean(str(raw or "").encode("utf-8", "replace")), cmd)
    kept = [ln[:MAX_LINE] for ln in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        # Said, not hidden — the same wording ConsoleTap uses, for the same reason: a marker
        # reading five lines should know there were forty.
        kept.append(f"… {len(lines) - MAX_LINES} more line(s)")
    return True, kept


#: What the grading run uses to make something happen between its two readings. `alloc N` grows
#: the heap N pages, TOUCHING each one so it really faults in, and then spins — so it moves the
#: free list and stays runnable, which is what the delta metrics and the load average need.
#: Sixteen pages is comfortably past `lab_grade.MIN_ALLOC_PAGES` and costs about eight seconds,
#: because `alloc` pauses a tick per page.
STIR_PROG, STIR_PAGES = "alloc", 16
#: How long to wait for the free list to actually move before giving up and reading anyway. A
#: metric that could not be measured says so; it never guesses.
STIR_WAIT_S = 22.0


def take_reading(spec, machine_name: str, provider, vm=None):
    """One moment, seen twice: run the assignment's test, and read GINI's own view beside it.

    Both halves are best-effort and independently so. A test that would not run still leaves
    GINI's numbers worth having, and a machine that stopped answering still leaves what the
    program printed — `lab_grade` reports whichever half is missing rather than failing the
    student for it.
    """
    from ..domain.lab_grade import Reading, parse_report
    ok, lines = run_test(provider, getattr(spec, "test_prog", ""))
    free_pages = procs = runnable = sleeping = ticks = None
    try:
        snap = provider.snapshot()
        ps = [p for p in (getattr(snap, "procs", None) or [])]
        procs = len(ps)
        runnable = sum(1 for p in ps if getattr(p, "state", "") == "runnable")
        sleeping = sum(1 for p in ps if getattr(p, "state", "") == "sleeping")
        ticks = getattr(snap, "ticks", None)
    except Exception:                              # noqa: BLE001 — half a reading is still a reading
        pass
    try:
        if vm is not None:
            free_pages = getattr(vm.snapshot(), "free_pages", None)
    except Exception:                              # noqa: BLE001
        pass
    return Reading(report=parse_report(lines) if ok else {}, free_pages=free_pages,
                   procs=procs, runnable=runnable, sleeping=sleeping, ticks=ticks)


def _free_pages(vm):
    try:
        return None if vm is None else getattr(vm.snapshot(), "free_pages", None)
    except Exception:                              # noqa: BLE001
        return None


def _stir(provider, vm, say=None) -> int | None:
    """Make something happen. Returns the pid holding the memory, so it can be let go afterwards.

    WAITS FOR THE EFFECT rather than sleeping a fixed time: `alloc` pauses a tick per page and a
    tick is not a fixed length on a loaded machine. It gives up at `STIR_WAIT_S` and lets the
    metrics report "nothing was allocated" — which is the honest outcome and not a failure.
    """
    import time

    from ..domain.lab_grade import MIN_ALLOC_PAGES
    before = _free_pages(vm)
    if not provider.run(STIR_PROG, str(STIR_PAGES)):
        return None
    deadline = time.monotonic() + STIR_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.6)
        now = _free_pages(vm)
        if before is None or now is None:
            continue
        if before - now >= MIN_ALLOC_PAGES:
            break
    if say:
        say(f"{STIR_PROG} allocated "
            f"{'?' if before is None else int(before - (_free_pages(vm) or before))} pages")
    try:
        for p in (getattr(provider.snapshot(), "procs", None) or []):
            if getattr(p, "name", "") == STIR_PROG:
                return int(getattr(p, "pid", 0)) or None
    except Exception:                              # noqa: BLE001
        pass
    return None


def grade_now(spec, machine_name: str, provider, vm=None, say=None):
    """Measure the assignment's declared metrics against a running kernel. Returns the results.

    TWO readings with real work between them, because half the metrics are about MOVEMENT: that
    `freemem` falls by what was allocated, that `uptime` advances with the kernel's own tick, that
    the load average rises when something becomes runnable. A single reading cannot see any of
    that, and a constant would satisfy a comparison of one number with itself.

    The workload is let go afterwards — `alloc` spins forever by design, and leaving it running
    would quietly skew every later reading the student takes.
    """
    from ..domain import lab_grade as _g
    if spec is None or not getattr(spec, "grade", ()):
        return ()
    if say:
        say("reading what your program says…")
    before = take_reading(spec, machine_name, provider, vm)
    pid = None
    try:
        if say:
            say(f"running {STIR_PROG} so there is something to measure…")
        pid = _stir(provider, vm, say)
        if say:
            say("reading again…")
        after = take_reading(spec, machine_name, provider, vm)
    finally:
        if pid:
            try:
                provider.kill(pid)
            except Exception:                      # noqa: BLE001 — best effort, never fatal
                pass
    return _g.grade(spec, before, after, syscall_names(spec, machine_name))


def syscall_names(spec, machine_name: str) -> dict:
    """`{number: name}` for the system calls this machine's assignment has wired.

    Host-side, from the file the student edits, so it is right the moment they save — no rebuild
    and no running kernel needed. Empty when nothing is armed or they have not got there yet,
    which leaves the Syscall Lab on the stock names it has always used.
    """
    from ..domain.xv6 import parse_syscall_defines
    for f in getattr(spec, "files", ()):
        if str(getattr(f, "tree", "")).endswith("kernel/syscall.h"):
            return parse_syscall_defines(read_file(machine_name, f.name,
                                                   getattr(spec, "id", "")) or "")
    return {}


def progress_for(spec, machine_name: str) -> dict:
    """How far the wiring checklist has got on this machine — what a hub tile shows.

    Here rather than in the panel because the hub asks it for every assignment at once and the
    panel asks it for one; one answer, two readers. Pure file reads, so it is cheap enough to
    call on open and needs no poll.
    """
    from ..domain import lab_spec as _ls
    res = _ls.evaluate(spec, reader_for(spec, machine_name),
                       pristine_reader_for(spec, machine_name))
    return _ls.progress(res)


def collect(machine_names) -> dict:
    """`{"<machine>/<file>": {sha256, lines, bytes, text}}` — what a submission carries.

    The assignment's OWN file list. `xv6_shadows.collect` gathers a fixed three, which are the
    deliverable of a shadow lab and of nothing else: an A-Lab submission going through that path
    hands a marker three files the student never opened and none of the ten they did. That is the
    exact failure `xv6_shadows`'s own docstring was written about — "an OS submission did not
    contain the assignment" — arriving a second time by a different door.

    Every file the student owns, edited or not. An untouched `kalloc.c` is evidence too: it says
    Part B was not attempted, which a marker wants to know and cannot infer from an absence.

    Resolved PER MACHINE, because arming is per machine: a topology with M1 on A-Lab 01 and M2 on
    A-Lab 02 hands in both, each attributed to the machine that carried it. Keys are
    `<machine>/<file>`, so nothing collides.

    Scoped to the machines passed in, because lab folders outlive the topology that made them — a
    student who has done three assignments has three folders, and gathering the lot would put one
    lab's work into another lab's submission.
    """
    out: dict = {}
    for machine in machine_names or []:
        spec = active_spec(machine)
        if spec is None:
            continue
        d = lab_dir(machine, getattr(spec, "id", ""))
        for f in getattr(spec, "files", ()):
            path = d / f.name
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rec = {"sha256": digest(text), "lines": len(text.splitlines()), "bytes": int(size)}
            if size <= MAX_SOURCE_BYTES:
                rec["text"] = text
            else:
                rec["omitted"] = "too large to submit"
            out[f"{sane_name(machine)}/{f.name}"] = rec
    return out


def link_script(spec) -> str:
    """The shell that runs INSIDE the container to attach one assignment to the tree.

    Run once per Run, and again whenever the student arms a different assignment — which is the
    whole reason the mount is the parent directory. Re-arming is this script with a different
    `spec`, not a new container.

    Idempotent by construction, because it runs repeatedly and every pass after the first must be
    a no-op:

    * `[ -f $LAB/x ]` — seed from the image only when the student has nothing yet.
    * `[ -L kernel/x ]` — and this one is the safety of the whole scheme. On a second pass the
      tree file is ALREADY a symlink, and copying *that* aside as the pristine copy would replace
      the only good original with a link to the student's edited file. Revert would then restore
      their own broken code, which is the one thing Revert must never do.

    Note `ln -sf` is run unconditionally for a file that is already a link. It has to be: after
    switching assignments the tree's link points into the PREVIOUS assignment's folder, and a
    `[ -L ]` guard around the link would see a symlink, call it done, and leave the machine
    building the lab the student just left.

    Registering user programs here rather than handing over the Makefile is deliberate: the build
    system is not the lab, there is no pedagogy in `UPROGS`, and a broken line-continuation
    produces an error about nothing the student was thinking about.
    """
    lab, pristine = mount_dir(spec), pristine_mount()
    lines = [
        "set -e",
        "cd /opt/xv6-riscv",
        f"mkdir -p {shlex.quote(pristine)} {shlex.quote(lab)}",
    ]
    for f in getattr(spec, "files", ()):
        mine = shlex.quote(f"{lab}/{f.name}")
        orig = shlex.quote(f"{pristine}/{f.name}")
        tree = shlex.quote(f.tree)
        # Written as nested `if` blocks rather than `[ -f x ] && cp x y`: under `set -e` a false
        # test at the end of an && chain is a non-zero status, and the script would exit there —
        # silently, on the second Run, having linked only the files before it.
        # Order matters: capture the original FIRST, then seed the student's copy from it.
        # Seeding from the tree instead would copy a symlink into the assignment they just
        # left, so switching assignments would carry the old one's edits into the new one.
        lines += [
            f"if [ ! -L {tree} ] && [ -f {tree} ] && [ ! -f {orig} ]; then",
            f"  cp {tree} {orig}",
            "fi",
            f"if [ ! -f {mine} ] && [ -f {orig} ]; then cp {orig} {mine}; fi",
            f"ln -sf {mine} {tree}",
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


def state_of(spec, machine_name: str, name: str) -> str:
    """`"edited"` | `"untouched"` | `"missing"` | `"unknown"` — what the file list shows.

    "unknown" is honest rather than tidy: before the first Run there is no pristine copy, so
    whether the file differs from the image's cannot be answered, and guessing "untouched" would
    show a student a green tick for work they have not started.
    """
    spec_id = getattr(spec, "id", "")
    mine = read_file(machine_name, name, spec_id)
    if mine is None:
        return "missing"
    orig = pristine_path(machine_name, name)
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
    spec_id = getattr(spec, "id", "")
    orig = pristine_path(machine_name, name)
    if not orig.is_file():
        return False, f"no original of {name} yet — press Run once so GINI can take a copy"
    try:
        (lab_dir(machine_name, spec_id) / name).write_text(
            orig.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except OSError as e:
        return False, f"could not revert {name}: {e}"
    return True, f"{name} put back — press Load to build it"

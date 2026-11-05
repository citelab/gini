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
#: Drops a `\t$U/_name\` row whose user/name.c is gone (see link_script). `substr` from 6 skips
#: the tab, `$U/_`; the length leaves off the trailing backslash.
_UPROG_PRUNE_AWK = (r'/^\t\$U\/_[A-Za-z0-9_]+\\$/{n=substr($0,6,length($0)-6);'
                    r' if(system("test -e user/" n ".c")!=0) next} {print}')

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


def pristine_path(machine_name: str, name: str, spec_id: str) -> Path:
    """The image's copy of one of an assignment's files, kept beside the student's for Revert and
    for the `edited` badge.

    KEYED BY ASSIGNMENT, `.pristine/<spec_id>/<name>`. It was keyed by machine alone, which held
    while only one assignment shipped: A-Lab 01 and A-Lab 02 both own syscall.c, and a shared copy
    taken under A-Lab 01 was the image of THAT day. A-Lab 02's student was then seeded, and
    reverted, to a syscall.c without the hooks the newer image's kernel relies on -- a starting
    kernel that did not build. `spec_id` is required, not defaulted, so a caller that forgot it
    fails loudly instead of reading another assignment's file."""
    return machine_root(machine_name) / PRISTINE_DIR / sane_name(spec_id) / name


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
    moved = _move_loose(machine_name)
    _namespace_pristine(machine_name)          # after the move: it files copies by their owner
    return moved


def _move_loose(machine_name: str) -> str:
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


def _namespace_pristine(machine_name: str) -> None:
    """Move pristine copies from the old flat `.pristine/<name>` into `.pristine/<spec_id>/`.

    A flat copy belongs to whichever assignment seeded a student file of the same name from it,
    so it is copied to every assignment folder holding that file, then removed. One that no
    assignment's folder holds is left where it is: deleting it would gain nothing, and guessing
    an owner could file one lab's original under another."""
    base = machine_root(machine_name) / PRISTINE_DIR
    try:
        flat = [p for p in base.iterdir() if p.is_file()]
    except OSError:
        return
    if not flat:
        return
    try:
        from ..domain import lab_spec as _ls
        specs = _ls.catalog()
    except Exception:                              # noqa: BLE001 — a lab must never block a Run
        return
    for p in flat:
        owners = [sane_name(sp.id) for sp in specs
                  if sp.file(p.name) and (lab_dir(machine_name, sp.id) / p.name).is_file()]
        try:
            for sid in owners:
                dest = base / sid / p.name
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
            if owners:
                p.unlink()
        except OSError:
            continue


# --------------------------------------------------------------------------- #
# container-side paths
# --------------------------------------------------------------------------- #

def mount_dir(spec) -> str:
    """Where the armed assignment's files sit INSIDE the container."""
    return f"{MOUNT}/{sane_name(getattr(spec, 'id', '') or 'lab')}"


def pristine_mount(spec) -> str:
    """Where the armed assignment's pristine copies sit INSIDE the container."""
    return f"{MOUNT}/{PRISTINE_DIR}/{sane_name(getattr(spec, 'id', '') or 'lab')}"


#: Where a new image keeps an untouched copy of the patched tree's sources. The link script takes
#: pristine copies from here when it exists: the tree itself is only trustworthy on the first pass
#: in a fresh container, because after that its files are symlinks into some assignment's folder.
IMAGE_PRISTINE = "/opt/xv6-pristine"


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
    (machine_root(machine_name) / PRISTINE_DIR / sane_name(getattr(spec, "id", ""))).mkdir(
        parents=True, exist_ok=True)
    written = []
    for f in getattr(spec, "files", ()):
        if not f.seed:
            continue
        # The seed IS the pristine copy for these. Without this, the two files the assignment
        # carries — the header xv6 lacks and the starter program — are the only ones a student
        # cannot Revert, which is backwards: the starter program is the likeliest thing they
        # break while working out what the call should print.
        orig = pristine_path(machine_name, f.name, getattr(spec, "id", ""))
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
            return pristine_path(machine_name, name, getattr(spec, "id", "")).read_text(
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


def run_test(provider, prog: str, args: str = "",
             timeout: float = 12.0) -> tuple[bool, list[str]]:
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
    # An assignment's test can carry its arguments (`sharetest 10 5 1`). The agent takes the
    # program and its arguments separately and filters the name to [A-Za-z0-9_], so sent whole
    # it arrived as `sharetest1051`.
    if " " in prog.strip():
        prog, _, extra = prog.strip().partition(" ")
        args = (extra + (" " + args if args else "")).strip()
    ok, raw = provider.run_and_capture(prog, args, timeout=timeout)
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
    ok, lines = run_test(provider, getattr(spec, "test_prog", ""),
                         timeout=getattr(spec, "test_timeout", 12.0))
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


def _table(provider) -> tuple:
    """GINI's own process table, as lab_grade.ProcRow rows. Empty when the machine did not answer."""
    from ..domain.lab_grade import ProcRow
    try:
        procs = getattr(provider.snapshot(), "procs", None) or []
    except Exception:                              # noqa: BLE001 — no table is still an answer
        return ()
    return tuple(ProcRow(pid=int(p.pid), parent=getattr(p, "parent", None), name=str(p.name),
                         tickets=getattr(p, "tickets", None), rtime=getattr(p, "rtime", None))
                 for p in procs)


def run_workload(provider, run, say=None):
    """Perform one LabRun: switch policy, run its command in the foreground, and read GINI's
    process table `sample_at` seconds in -- while the program's children are still alive, which
    is the only time their tickets and run time can be seen. Returns a lab_grade.Reading.

    This is run_and_capture's bracket with one addition, the mid-run read, and so it cannot simply
    call it: that waits for the prompt, by which time sharetest has killed its children. The
    command is GINI's, from the lab's YAML, never the student's.
    """
    import time

    from ..domain.lab_grade import Reading, parse_report
    from ..domain.xv6 import run_finished, run_output
    from .console_tap import MAX_LINE, MAX_LINES, clean
    if run.policy is not None:
        ok, _ = provider.run_and_capture("policy", str(run.policy), timeout=8.0)
        if not ok:
            return Reading()
    prog, _, args = run.command.strip().partition(" ")
    _, cursor = provider.console_since(0)
    if not provider.run(prog, args, foreground=True):
        return Reading()
    if say:
        say(f"running {run.command} under policy {run.policy}… (about "
            f"{int(run.sample_at) + 8} s)")
    seen, table = "", ()
    start = time.monotonic()
    while time.monotonic() - start < run.timeout:
        time.sleep(0.5)
        if not table and time.monotonic() - start >= run.sample_at:
            table = _table(provider)
        try:
            chunk, cursor = provider.console_since(cursor)
        except Exception:                          # noqa: BLE001 — a dropped read is not a failure
            break
        seen += chunk or ""
        if run_finished(seen):
            break
    printed = run_output(clean(seen.encode("utf-8", "replace")), run.command)
    lines = [ln[:MAX_LINE] for ln in printed[:MAX_LINES]]
    kept = list(lines)
    if len(printed) > MAX_LINES:
        kept.append(f"… {len(printed) - MAX_LINES} more line(s)")     # said, as run_test says it
    return Reading(report=parse_report(lines), table=table, lines=tuple(kept))


def grade_runs(spec, provider, say=None) -> dict:
    """Every LabRun the assignment declares, in order: {run name: Reading}."""
    out = {}
    for run in getattr(spec, "runs", ()) or ():
        try:
            out[run.name] = run_workload(provider, run, say)
        except Exception as e:                     # noqa: BLE001 — one run must not lose the rest
            if say:
                say(f"{run.name}: could not run ({type(e).__name__}: {e})")
    return out


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
    if provider is None:
        # The machine is not running, or is showing the demo feed. Grade anyway: `syscall_named`
        # is read from their own syscall.h and needs no kernel, and the rest come back "not
        # measured" with a reason. That is worth recording — it tells a marker the metrics were
        # attempted and the machine was down, which is a different submission from one where
        # nobody ever checked.
        return _g.grade(spec, _g.Reading(), None, syscall_names(spec, machine_name))
    if getattr(spec, "runs", ()):
        # A lab that declares its workloads (A-Lab 02) is measured by running them; the stir and
        # the two sysinfo-shaped readings mean nothing to it.
        return _g.grade(spec, _g.Reading(), None, syscall_names(spec, machine_name),
                        runs=grade_runs(spec, provider, say))
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


#: How much of a stage's output is kept: judged in full, recorded in full up to this bound.
#:
#: NOT console_tap's MAX_LINES. That cap (10) is for a test program's report in the proof, and
#: applied here it cut `buddytest` off before its `overlaps: 0` line -- so stage 0 failed every
#: kernel, the model answer included, with "did not print 'overlaps: 0'". Found end to end
#: against the real agent; the fake machine in the tests printed too little to hit it. A stage's
#: verdict is read from its WHOLE output (`megatest leaks` prints every other test first), so the
#: bound is only a guard against a runaway printk loop.
MAX_STAGE_LINES = 200

#: After a `panic` appears, how long to keep reading before calling the stage over. The kernel
#: prints the panic and stops; the second is for the lines it was still flushing.
PANIC_GRACE_S = 1.5


def run_stage(provider, stage, command: str, say=None) -> tuple:
    """Run one staged check's command in the foreground and return `(lines, returned, note)`.

    run_workload's bracket without its mid-run read: the cursor is taken BEFORE the command is
    typed, and the prompt coming back closes it. Two differences, both about a kernel that dies:
    a `panic` ends the wait after a short grace rather than at the timeout -- a crashed kernel
    will not print a prompt, and waiting two minutes to be told so helps nobody -- and a command
    the agent would not start comes back with a `note` saying why, instead of a misleading
    "timed out".
    """
    import time

    from ..domain.xv6 import run_finished, run_output
    from .console_tap import MAX_LINE, clean
    prog, _, args = command.strip().partition(" ")
    try:
        _, cursor = provider.console_since(0)
    except Exception as e:                         # noqa: BLE001 — the machine is not answering
        return [], False, f"the machine did not answer: {e}"
    if not provider.run(prog, args, foreground=True):
        why = getattr(provider, "last_run_error", "") or f"could not start {prog}"
        return [], False, f"GINI could not start it: {why}"
    seen, returned, panic_at = "", False, None
    start = time.monotonic()
    while time.monotonic() - start < float(stage.timeout):
        time.sleep(0.4)
        try:
            chunk, cursor = provider.console_since(cursor)
        except Exception:                          # noqa: BLE001 — a dropped read is not a failure
            continue
        seen += chunk or ""
        if run_finished(seen):
            returned = True
            break
        if panic_at is None and "panic" in seen:
            panic_at = time.monotonic()
        if panic_at is not None and time.monotonic() - panic_at >= PANIC_GRACE_S:
            break
    printed = run_output(clean(seen.encode("utf-8", "replace")), command)
    lines = [ln[:MAX_LINE] for ln in printed[:MAX_STAGE_LINES]]
    if len(printed) > MAX_STAGE_LINES:
        lines.append(f"… {len(printed) - MAX_STAGE_LINES} more line(s)")   # said, as run_test does
    return lines, returned, ""


def kernel_dead(provider) -> tuple[str, str]:
    """`(panic line, when)` if the kernel is already dead -- it panicked and no prompt came after --
    else `("", "")`. `when` is "boot" or "an earlier command".

    Asked once, before the first stage. A kernel that panicked cannot run stage 0, and typing
    `buddytest` at it would only wait out the timeout to learn what the console already says. Read
    from the console GINI already keeps, so it costs nothing on a healthy machine. "When" is there
    because the two cases tell the student different things: a panic at boot is in their
    buddy_init(), one after a prompt is the previous Check's crash, still on screen because nobody
    has pressed Load since.
    """
    try:
        text = str(provider.console() or "")[-4000:]
    except Exception:                              # noqa: BLE001 — unknown is not dead
        return "", ""
    from .console_tap import clean
    lines = clean(text.encode("utf-8", "replace")).replace("\r", "").split("\n")
    last_panic = max((i for i, ln in enumerate(lines) if "panic" in ln), default=-1)
    if last_panic < 0:
        return "", ""

    def prompt(ln):
        return ln.lstrip().startswith("$")
    if any(prompt(ln) for ln in lines[last_panic + 1:]):
        return "", ""
    booted_at = max((i for i, ln in enumerate(lines[:last_panic]) if "kernel is booting" in ln),
                    default=-1)
    shell = any(prompt(ln) for ln in lines[max(booted_at, 0):last_panic])
    return lines[last_panic].strip(), ("an earlier command" if shell else "boot")


#: How long, after a Load, to watch the console for the new kernel to reach a shell or to panic.
BOOT_WAIT_S = 20.0


def watch_boot(provider, cursor: int, timeout: float = BOOT_WAIT_S) -> tuple[str, str]:
    """After a Load: `("booted", "")` once the shell's prompt appears, `("panic", line)` if the
    kernel panics first, `("", "")` if neither happens within `timeout`.

    The agent's Load returns the moment QEMU restarts, so a kernel that BUILDS and then dies at
    boot came back as "Loaded — the machine is running your kernel". In A-Lab 03 that is the
    commonest failure there is -- the allocator runs before anything else, and a mistake in it
    stops the machine with `panic: kvmmap` -- and the handout tells the student to read the panic.
    So the panel says it, and points at the console. `cursor` is the console position taken
    BEFORE the Load, which makes everything after it this boot's output.
    """
    import time

    from .console_tap import clean
    seen, deadline = "", time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            chunk, cursor = provider.console_since(cursor)
        except Exception:                          # noqa: BLE001 — unknown, not dead
            return "", ""
        seen += chunk or ""
        text = clean(seen.encode("utf-8", "replace"))
        lines = text.split("\n")
        panic = next((ln.strip() for ln in lines if "panic" in ln), "")
        if panic:
            return "panic", panic
        if any(ln.lstrip().startswith("$") for ln in lines):
            return "booted", ""
    return "", ""


def check_stages(spec, provider, part: str = "all", seed: int | None = None, say=None):
    """Run the staged checks of `part` ("A", "B", "C" or "all") against the running kernel.
    Returns a `lab_stages.StageCheck`; record it with `record_grades`.

    One fresh seed per Check, shared by every stage in it and recorded with the results, so the
    student can type any stage's command with that seed and see the same run.
    """
    from ..domain import lab_stages as _st
    seed = _st.new_seed() if seed is None else int(seed)
    if provider is None:
        # The machine is not running (or is the demo feed). Nothing can be run, and every stage
        # says so: recorded, because "the machine was down at hand-in" is evidence too.
        return _st.not_run(_st.plan(spec, part), seed, part or "all",
                           "the machine was not running")

    def execute(stage, command):
        return run_stage(provider, stage, command, say)

    dead, when = kernel_dead(provider)
    return _st.run(_st.plan(spec, part), execute, seed, part or "all", dead=dead, say=say,
                   dead_in=when or "boot")


def record_grades(recorder, spec, results, device: str = "") -> None:
    """Put a grading run into the proof chain — one entry per metric, including the unmeasurable.

    ONE place, called from the panel's "Check my work" and from the hand-in. Two copies of this
    would drift, and the way they would drift is in what gets recorded, which is the one thing a
    marker cannot go back and re-derive.

    A metric that could not be measured is recorded too: "there was nothing to compare" is
    evidence about the attempt, and a chain holding only the verdicts it managed to reach reads as
    if the rest had passed.
    """
    from ..ui.lab_record import record
    title = getattr(spec, "title", "lab")
    if hasattr(results, "seed") and hasattr(results, "results"):
        _record_stages(record, recorder, title, results, device)
        return
    # What each workload PRINTED, first — one `spawn` per run, marked with the run's name, so
    # the metrics after it can be checked against the transcript they were computed from.
    # A run that produced no reading at all still goes in, empty: it was attempted.
    readings = getattr(results, "runs", None) or {}
    for run in getattr(spec, "runs", ()) or ():
        if run.name not in readings:
            continue
        what = run.command + (f" (policy {run.policy})" if run.policy is not None else "")
        record(recorder, "note_spawn", device, what, "launch", None,
               list(getattr(readings[run.name], "lines", ()) or ()), False, run=run.name)
    for r in results or ():
        record(recorder, "note_measure", f"{title} · {r.id}",
               {"ok": bool(r.ok), "pending": r.ok is None,
                "measurement": dict(getattr(r, "detail", None) or {}),
                "summary": f"{r.describe} — {r.summary}" if r.summary else r.describe})


def _record_stages(record, recorder, title: str, check, device: str) -> None:
    """A staged Check in the chain, exactly as A-Lab 02's runs go in: per stage, a `spawn` with
    what the command printed (the command with its seed filled in), then a `measure` with the
    verdict. A stage that was not run has nothing to show and gets no spawn -- but it IS recorded,
    as pending with its reason, because "not run, needs A2" is a different submission from one
    where nobody ever tried."""
    from ..domain.lab_stages import NOT_RUN, OK
    for r in check:
        if r.verdict != NOT_RUN:
            record(recorder, "note_spawn", device, r.command, "launch", None, list(r.lines),
                   False, run=f"stage {r.id}")
        summary = (f"not run — {r.summary}" if r.verdict == NOT_RUN
                   else (r.summary or "ok") if r.verdict != OK else "ok")
        record(recorder, "note_measure", f"{title} · stage {r.id}",
               {"ok": r.verdict == OK, "pending": r.verdict == NOT_RUN,
                "measurement": {"stage": r.id, "seed": int(check.seed), "summary": summary},
                "summary": summary})


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
    lab, pristine = mount_dir(spec), pristine_mount(spec)
    lines = [
        "set -e",
        "cd /opt/xv6-riscv",
        f"mkdir -p {shlex.quote(pristine)} {shlex.quote(lab)}",
    ]
    # Let go of the assignment the student left before attaching this one. Linking only THIS
    # assignment's files left every file the previous one owned and this one does not still
    # linked into the old folder: after A-Lab 01 -> A-Lab 02, the machine built the student's
    # A-Lab 01 trap.c (which calls load_sample) against A-Lab 02's untouched defs.h (which does
    # not declare it), and Load failed in a file the new lab never mentions. Each such link goes
    # back to the image's copy. A file the image has no copy of never existed there -- the old
    # assignment seeded it (sysinfo.h, sysinfotest.c) -- so its link is simply removed. Only an
    # image too old to keep copies falls back to the old assignment's pristine folder, which
    # cannot tell the two apart: it holds a copy of every file, seeded ones included. Links into this
    # assignment's folder are left: `ln -sf` below remakes them.
    lines += [
        f"if find kernel user -type l -lname '{MOUNT}/*' ! -lname '{lab}/*' | grep -q .; then",
        "  switched=1",
        "else",
        "  switched=",
        "fi",
        "find kernel user -type l | while read -r t; do",
        '  dest=$(readlink "$t")',
        '  case "$dest" in',
        f"    {lab}/*) ;;",
        f"    {MOUNT}/*)",
        f'      img={IMAGE_PRISTINE}/"$t"; old={MOUNT}/{PRISTINE_DIR}/"${{dest#{MOUNT}/}}"',
        '      rm -f "$t"',
        '      if [ -f "$img" ]; then cp "$img" "$t";',
        f'      elif [ ! -d {IMAGE_PRISTINE} ] && [ -f "$old" ]; then cp "$old" "$t"; fi ;;',
        "  esac",
        "done",
        # And the old assignment's test program leaves UPROGS with its source: left registered,
        # `make` builds sysinfotest against a user.h that no longer declares sysinfo(). Every
        # entry the image ships has its user/NAME.c, so "no source" means "an assignment's".
        "awk " + shlex.quote(_UPROG_PRUNE_AWK) + " Makefile > /tmp/gini_mk && mv /tmp/gini_mk Makefile",
        # And what was built from the old assignment goes too, once, on the switch itself. xv6
        # compiles with -MD: sysproc.d still named kernel/sysinfo.h, which is gone, and make
        # stopped with "No rule to make target" before compiling anything. A .d that survived
        # would be worse where its header still exists -- objects built against one
        # assignment's proc.h linked with the other's. The cost is one full build -- a few seconds --
        # on the first Load after switching.
        'if [ -n "$switched" ]; then',
        "  rm -f kernel/*.o kernel/*.d user/*.o user/*.d user/usys.S",
        "fi",
    ]
    for f in getattr(spec, "files", ()):
        mine = shlex.quote(f"{lab}/{f.name}")
        orig = shlex.quote(f"{pristine}/{f.name}")
        tree = shlex.quote(f.tree)
        image = shlex.quote(f"{IMAGE_PRISTINE}/{f.tree}")
        if getattr(f, "readonly", False):
            # Shown, never built. The image's copy stays in the tree -- that is what the handout
            # means by "GINI always builds its own copies, so the results are always about your
            # kernel" -- and the student's folder gets a fresh copy each time, write-protected so
            # their editor opens it read-only. An edit to it would change nothing anyway, and a
            # copy kept from an older image would show them a program the kernel no longer runs.
            # `|| true` on chmod: a Windows bind mount may refuse it, and under `set -e` that
            # would stop the script before the files after this one were linked.
            lines += [
                f"if [ -L {tree} ] && [ -f {image} ]; then rm -f {tree}; cp {image} {tree}; fi",
                f"if [ -f {image} ]; then",
                f"  rm -f {mine}; cp {image} {mine}; chmod a-w {mine} 2>/dev/null || true",
                "fi",
            ]
            continue
        # Written as nested `if` blocks rather than `[ -f x ] && cp x y`: under `set -e` a false
        # test at the end of an && chain is a non-zero status, and the script would exit there —
        # silently, on the second Run, having linked only the files before it.
        # Order matters: capture the original FIRST, then seed the student's copy from it.
        # Seeding from the tree instead would copy a symlink into the assignment they just
        # left, so switching assignments would carry the old one's edits into the new one.
        # The original comes from the image's untouched copy when the image has one; an older
        # image has only the tree, which is honest only while it is not yet a symlink.
        lines += [
            f"if [ ! -f {orig} ] && [ -f {image} ]; then",
            f"  cp {image} {orig}",
            f"elif [ ! -L {tree} ] && [ -f {tree} ] && [ ! -f {orig} ]; then",
            f"  cp {tree} {orig}",
            "fi",
            f"if [ ! -f {mine} ] && [ -f {orig} ]; then cp {orig} {mine}; fi",
            f"ln -sf {mine} {tree}",
            # Newer than anything built from it. make follows the link and compares the
            # STUDENT's file, and after a switch back that file is weeks older than the usys.S
            # the other assignment generated -- so `sysinfo` got no stub and the link failed.
            f"if [ -f {mine} ]; then touch {mine}; fi",
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
    f = spec.file(name) if hasattr(spec, "file") else None
    if f is not None and getattr(f, "readonly", False):
        return "readonly"
    orig = pristine_path(machine_name, name, spec_id)
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
    f = spec.file(name) if hasattr(spec, "file") else None
    if f is not None and getattr(f, "readonly", False):
        return False, f"{name} is GINI's own copy and cannot be changed, so there is nothing to revert"
    orig = pristine_path(machine_name, name, spec_id)
    if not orig.is_file():
        return False, f"no original of {name} yet — press Run once so GINI can take a copy"
    try:
        (lab_dir(machine_name, spec_id) / name).write_text(
            orig.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except OSError as e:
        return False, f"could not revert {name}: {e}"
    return True, f"{name} put back — press Load to build it"

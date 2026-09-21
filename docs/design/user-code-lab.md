# The User Code Lab — a hub for the A-Labs

**Status: built** (`ui/user_code_lab.py`, `services/xv6_lab.py`), except the two things named
under "What this does not do". Three details came out different from the design once it met the
code; each is marked **[changed in build]** below, with why.

The question this answers: **how does a student with four assignments pick the one they are
working on, without the other three getting in the way?**

Before this there was no answer, because there had only ever been one assignment — and the rule
standing in for the question broke the moment a second one shipped. See "What was broken" below.

---

## The shape

Three levels, each one a drill-down, which is the model the Machine Lab already teaches:

```
Machine Lab — M1
  └── THIS ASSIGNMENT  ·  one tile: "User Code"
        └── User Code Lab            ← this document
              ├── A-Lab 01 — sysinfo          [armed on M1]
              ├── A-Lab 02 — trace
              ├── A-Lab 03 — …
              └── A-Lab 04 — …
                    └── the per-lab panel      ← already built (ui/user_code.py)
                        files · wiring checklist · Load · compile log
```

The middle level is the new one. The top and bottom already existed — the Machine Lab's tile used
to open the per-lab panel directly, and that link is what now points at the hub.

One tile per assignment, 4 this year. **Seven is the hard ceiling**, and it is measured rather
than chosen — see "The tiles". They wrap three to a row, so the hub is one screen with no
navigation of its own.

---

## Arming is per MACHINE

**One A-Lab is armed on a machine at a time.** This is forced by the kernel, not chosen for the
UI: A-Lab 01 (sysinfo) and A-Lab 02 (trace) both add a system call, so both edit the same five
files —

    kernel/syscall.h      kernel/syscall.c      kernel/sysproc.c
    user/user.h           user/usys.pl

Two labs wired into one tree collide in all five. There is no arrangement of folders or mounts
that makes "both armed on M1" mean anything coherent, so the hub states it plainly rather than
letting a student discover it as a build error.

**Across machines there is no collision, and we deliberately do not invent one.** A student who
places M1 and M2 can arm A-Lab 01 on M1 and A-Lab 02 on M2 and work on both. Each xv6 element
already gets its own container, its own kernel tree, and its own host folder — `lab_dir()` keys
the host path on the machine name — so this costs nothing to allow and would cost real
complexity to prevent.

It is also worth being honest that preventing it would not work. Students edit in their own
editor, in a folder on their own disk; a second copy of a directory or a second gBuilder
profile defeats any client-side rule. This is the same position `proof.py` takes about its own
threat model: stop the accidental case, do not pretend to stop a determined one.

**Nothing synchronises a class.** Arming is a local choice. One student can be on A-Lab 03 in
week four while another is still on A-Lab 01. A course sets deadlines; it does not set what is
armed.

### No "submit before you switch" gate

Considered and rejected. Submitting requires an assignment code — with no code there is no
chain at all, and the recorder says so: *"Enter your assignment code first — nothing is being
recorded, so there is nothing to prove."* A rule that you must submit A-Lab 01 before arming
A-Lab 02 is therefore unsatisfiable for a student practising before codes are issued, for
anyone working offline, and for any lab the course does not collect. Switching is
non-destructive (see "Per-machine, per-lab folders"), and the hub shows each lab's progress on
its tile, so a student who has left work unfinished can see it.

---

## What was broken, and why the hub fixes it

`services/xv6_lab.active_spec()` answered "which assignment is this machine doing?" with a
deliberately dumb rule, written when exactly one assignment existed:

```python
packs = _ls.catalog()
return packs[0] if len(packs) == 1 else None
```

Drop a second YAML into `domain/labs/` and this returns `None` for *both* labs. `None` means
the Machine Lab skips the User Code face entirely (`_build_overview`), so the band vanishes —
silently, with no error, for an assignment that is sitting right there on disk. The only way
back is a `GINI_LAB` environment variable, which is fine for a developer and not something to
put in a handout.

The hub replaces that rule with an explicit, per-machine choice. `active_spec()` becomes
`active_spec(machine_name)` — "what is armed on this machine" — which is the change its own
docstring already anticipates:

> When missions come back from the Teaching Center this is where the armed activity will be
> read instead — the rest of the module already takes a spec rather than looking one up, so
> only this function changes.

That remains true. The hub is the local half of that sentence; a Teaching-Center-delivered
assignment would arm a lab through the same door.

**Six call sites**, every one of which already has a machine name in scope, so the signature
change is mechanical:

| Where | Has |
|---|---|
| `compiler.py:1171` | `d.name` |
| `main_window.py:2976` (`_attach_xv6_labs`) | iterates machines |
| `machine_lab.py:628` (`_build_overview`) | `self.device` |
| `machine_lab.py:900` (`_open_user_code`) | `self.device` |
| `user_code.py:81` | `self.device` |
| `proof_recorder.py:674` (`_collect_shadows`) | the `machines` list |

The last one moves the per-machine resolution *into* `collect()`, which already takes a list of
machines — so it ends up simpler than it is now, not more complicated.

---

## Where "armed" lives

A one-line marker file in the machine's own lab directory:

```
~/.gini/xv6-lab/<machine>/.armed         →  "syscall-sysinfo"
```

Per machine by construction, survives a restart, and sits next to the work it describes.

**It is deliberately not in `Settings` and deliberately not in the `.gini` project file.** Not
in Settings because Settings is per-installation and arming is per-machine. Not in the project
file for the reason the code already gives about `claimed_boards`:

> `claimed_boards` is a property of this laptop, never of a topology, so a colleague's `.gini`
> file cannot hand you their hardware.

Same failure mode: a topology a student downloads from a classmate, or from the course page,
must not silently re-arm their assignment.

**Arming is recorded in the proof chain.** A marker reading a submission should be able to see
which assignment the work on each machine was for, and when the student switched. `note_build`
already carries the spec id, so per-build attribution exists — this adds the switch itself.

---

## Per-machine, per-lab folders, and the one structural change

The compiler used to mount one lab's folder straight at the container path:

```
~/.gini/xv6-lab/<machine>/   →   /opt/xv6-lab          (compiler.py:1187)
```

**[changed in build]** The design wrote this path the other way round (`<machine>/xv6-lab`). The
real one is `xv6-lab/<machine>`, so the assignment becomes a level *below* the machine, not a
sibling of it.

The host side of that mount was chosen from `spec.machine_folder` **at compose time**, so
re-arming could not take effect until the next Run — the wrong cost for a choice a student makes
from a tile. (`machine_folder` is gone; the assignment's own id is the folder name now.)

**Change: mount the parent, and let the link script pick the lab.**

```
~/.gini/xv6-lab/<machine>/   →   /opt/xv6-lab
                   ├── .armed                  "syscall-sysinfo"
                   ├── .pristine/              the image's originals, shared
                   ├── syscall-sysinfo/
                   ├── syscall-trace/
                   └── …
```

**[changed in build]** `.pristine` is per MACHINE, not per assignment — the design had one inside
each assignment's folder, and that does not work. After switching, the tree's `syscall.h` is a
symlink into the assignment just left, so there is no original left to copy from and the newly
armed folder would start empty and link the tree at a file that does not exist. The image's copy
of `syscall.h` is the image's copy of `syscall.h`; it does not depend on what is armed, so there
is one per machine and every assignment seeds from it.

The link script's order is load-bearing for the same reason: **capture the original, then seed
the student's copy from it, then link.** Seeding from the tree after it is a symlink would copy
the previous assignment's edits into the new one. And the `ln -sf` runs unconditionally — a
`[ -L ]` guard would see the link left by the previous assignment, call it done, and leave the
machine building the lab the student just left.

The in-container link script then symlinks the kernel tree at `/opt/xv6-lab/<armed-id>/`
instead of at the mount root. Re-arming becomes **one exec per machine** (re-link), not a
re-compose, and it can happen while the topology is running.

This also gives each lab its own `.pristine`, which is what makes switching non-destructive: a
student who armed A-Lab 02 on M1 for an afternoon and comes back to A-Lab 01 finds their
sysinfo work exactly as they left it, with Revert still anchored to the right originals.

After a re-link the machine is carrying different kernel sources, so the student must press
**Load** again before the kernel matches the armed lab. The hub says so at the moment of
switching rather than leaving them to infer it from a stale Syscall Lab.

---

## The tiles

Reuse `LayerCard` from `ui/machine_lab.py` unchanged — it already takes exactly what a tile
needs (title, one-line description, accent hue, a live stat line) and already carries the
theme-portable hue machinery and the live-edge behaviour.

| Part of the tile | What it shows |
|---|---|
| Title | `A-Lab 01 — sysinfo` (from `spec.title`) |
| Description | one line, from the spec |
| Stat line | progress **on this machine**: `A 6/6 · B 3/6`, or `not started` |
| **Left edge** | **lit = armed on this machine**, dark = not |

Hues come from `user_code_lab.LAB_HUES`. **[changed in build]** The design said "no two tiles
share one" and left it there; measuring it gives a hard ceiling of **seven**. Every tile is in one
band, so the constraint is all-pairs rather than the Machine Lab's weaker within-a-band rule, and
there is no eight-hue set that stays 26 ΔE apart in all seven themes — only four seven-sets exist
and all four contain `slate`. So `slate` is spent last, which keeps the hue that reads as
"disabled" off the screen for a course with six assignments or fewer. `test_seven_is_the_ceiling_and_this_is_why`
fails if the palette ever grows, so an eighth assignment cannot quietly repeat a colour.

Two fields were added to `LabSpec` for the tiles: `summary` (the sentence under the title — a
title is a name, and "A-Lab 02 — trace" does not say what it is) and an optional `hue`, so an
assignment can keep its colour when another is added before it alphabetically.

Because the hub is opened from a machine, everything on it is that machine's view. The same
hub opened from M2 can show a different tile lit and different progress numbers, which is
exactly the two-machine case working as intended.

The edge reuse is deliberate and worth naming, because it is the second meaning that edge
carries. In the Machine Lab it means *"this face is showing a live kernel"*; here it means
*"this is the lab this machine is carrying"*. The through-line is the same in both: **the edge
marks what is real right now.**

Progress comes from the machinery that already draws the checklist — `lab_spec.evaluate()` +
`progress()` — run against each lab's own folder. It is a few small file reads per tile, cheap
enough to do on open and on return from a panel, and it does **not** need a poll.

Hues follow the same rule as the Machine Lab's faces: from the set that stays apart in all
seven themes by CIE76 ΔE, no two tiles sharing one. `tests/test_machine_lab_hues.py` already
enforces this for the faces and extends to the tiles without new machinery.

---

## What the hub shows when nothing is armed

All tiles dark, and picking one arms it. Critically, the Machine Lab's **"User Code" tile stays
visible** whether or not anything is armed on that machine — it opens the hub, and the hub is
where the choice is made. That is a change from today, where the whole band disappears when
`active_spec()` returns `None`, which is precisely the failure described above.

---

## Submission

`proof_recorder._collect_shadows()` gathers the student's kernel files from the xv6 machines in
the current topology. With per-machine arming it collects **each machine's armed lab** — so a
topology with M1 on A-Lab 01 and M2 on A-Lab 02 hands in both, each attributed to its machine.

This is the existing contract rather than a new one; the docstring already says the collection
is "scoped to the xv6 machines in THIS topology". A submission describes an experiment, and if
the experiment carries two assignments then so does the submission. The Teaching Center already
receives the assignment code separately and can select on it.

Filenames do not collide across machines because the collection is keyed `<machine>/<file>`.

---

## What this does not do

* **It does not grade.** The 7 metrics in `syscall-sysinfo.yaml` remain unimplemented — no
  consumer of `spec.grade` exists anywhere outside `lab_spec.py`. Separate work; this hub
  neither helps nor hinders it.
* ~~It does not capture program output.~~ **Built.** The assignment names a `test_program`,
  the panel has a Run button for it, and what it prints is recorded on the `spawn` entry as
  `out`, marked `test: true`. See "Capturing the test's output" below.
* **It does not deliver assignments from the Teaching Center.** Labs still ship inside the
  `gini-core` wheel, so a new assignment is still a release. The hub is the seam a
  server-delivered assignment would arm through — a prerequisite for that work, not a
  substitute for it.

---

## Build order

1. ✅ `active_spec(machine_name)` reading `.armed`, with `arm()`, `armed_id()` and
   `ensure_layout()` beside it. Six call sites, all mechanical.
2. ✅ Mount the parent folder; link script takes the assignment id.
3. ✅ The hub panel: tiles from `catalog()`, per-machine progress, arm on click, re-link on change.
4. ✅ Re-point the Machine Lab's User Code face at the hub, and keep it visible when nothing is
   armed — it used to be dropped exactly when a student needed it.
5. ✅ Record arming in the chain (`note_tune`, machine · "assignment" · id).

Re-linking a running machine goes through an `on_relink` callback handed to `MachineLab` by
`main_window._relink_xv6_lab`, which runs the SAME `link_script` the launch path runs. The lab UI
does not hold the orchestrator, the same way it does not open its own terminal.

**Arming is written to disk before the re-link is attempted.** If the exec fails — machine down,
container not taking execs yet — the student's choice still sticks and the next Run links the
right assignment. The other order loses the choice on exactly the machines where it is least
obvious anything went wrong.


---

## Capturing the test's output

For an A-Lab the output **is** the deliverable — the assignment is a program that reports system
information — so a chain holding the build and the launch but not the answer records everything
except whether it worked.

It is a **bracket**, and the shape of the whole feature follows from what a bracket needs. The
console is one long byte stream, so output belongs to a program only when both ends are known:
the cursor is read before GINI types the command, which makes the start exact, and the shell's
returning prompt closes it.

Neither end exists for a program the student typed at the Keyboard, or for one sent to the
background — the shell hands the prompt straight back and there is nothing to wait for. So a
lab's test is **prescribed, launched from the User Code Lab, and run in the foreground**, and the
handout tells students to use it. That also settles a question that would otherwise need
answering: nothing records what a student types. `open_console` still records only that they went
in, and `proof_events` still says why — *"a proof of activity is not a keylogger."*

The output lands on the existing `spawn` entry as `out` (truncated verbatim, never summarised,
the same rule `command` follows) with `test: true` so a marker can find the run that is meant to
demonstrate the work without knowing which program each assignment names. A launch with neither
field is byte-for-byte what it always was, so nothing that reads old chains changes.

A refused run is recorded too. "It would not start" is the student's evidence about their own
code, and a chain keeping only successes cannot tell that from "never tried".

### This one needs the image rebuilt

Two agent-side changes, so a `gini-xv6` built before them cannot run a lab's test:

* **`PROGRAMS` is no longer the whole answer.** It is ten workload names fixed when the image is
  built; an A-Lab's test program is compiled by the student long afterwards, so a list could
  never know about it. The build leaves `user/_<name>`, which is exactly what `mkfs` puts into
  `fs.img` — asking the filesystem is a fact about the running kernel rather than a list somebody
  has to remember to update.
* **`prog` is now filtered.** The allow-list *was* the injection guard: `/run` writes its command
  into a real shell, and only names on that fixed list ever reached it. Accepting a lab's own
  program removes that guarantee, so the name is reduced to alphanumerics and underscores the way
  `_safe_args` already does one field over.
* `fg=1` runs the command without `&`.

The panel degrades honestly on an older image: no `run_and_capture` on the provider means the
button says to rebuild, rather than failing in a way that looks like the student's bug.

---

## Known duplication — read this before touching either side

**`domain.xv6.run_output` re-implements half of `services/console_tap.py`, and that is left
in place deliberately.**

`console_tap` is the original: it turns a terminal's byte stream into "they ran this, and it
printed that", and the C-Labs and T-Labs have been relying on it since they shipped. It is
tested and it works. Nothing here changes it.

What went wrong: this path was built without checking, and shipped its own half of that module —
with no escape stripping and **no line cap at all**. The escapes then defeated the echo-stripping
that the whole bracket exists to get right, so GINI's own keystrokes were recorded as the first
line of the student's output, and unbounded output went into the proof chain. Both were real
defects, found by asking "are we duplicating something?" rather than by a test.

**The fix was to import, not to move.** `services.xv6_lab.run_test` calls `console_tap.clean`
and uses its `MAX_LINES` / `MAX_LINE`, including its "… N more line(s)" wording, so there is one
copy of the regexes and one set of caps. `console_tap` itself is untouched.

**What is still duplicated, and why it was not unified now.** The SPLITTING half — telling the
shell's echo and the trailing prompt from the program's own output — still lives in
`domain.xv6.run_output`. `ConsoleTap` cannot do that job as it stands, because it is
keystroke-driven: it watches what a student types into a pty and closes a record when the next
command begins. This path has no keystrokes. GINI issues the command over HTTP and waits for the
prompt. Unifying them means reworking `ConsoleTap` to accept a command it was *told* about rather
than one it *watched being typed* — a change to code three lab families depend on, for no
behaviour a student would notice.

**For the next re-engineering:** give `ConsoleTap` a second entry point — "here is a command I
issued, here is the stream, tell me what it printed" — and have both the terminal and this path
use it. Then `run_output` and `run_finished` can go.

One difference is a choice rather than debt: interior blank lines are KEPT here and dropped by
`ConsoleTap`. Dropping them is right for a terminal transcript and wrong for a program's output —
a blank line a student printed between two sections is theirs.

### Two related notes, neither of them duplication of this work

* **`_LAUNCHABLE` (ui/machine_lab.py) and `PROGRAMS` (backend/xv6/gini_agent.py)** are the same
  ten names in two files, across the container boundary. Pre-existing, and the agent's own
  comment already says "keep in step". Relaxing the allow-list made `PROGRAMS` no longer the
  authority on what can run — it is now a fast path in front of a filesystem check — so the
  duplication matters less than it did, but it is still two lists.
* **Grading must reuse `domain.riders` / `ev.measure`, not invent a third reduction layer.**
  `riders.parse_measurement` already turns a tool's raw output into "one scalar that grading
  asserts on", and `ev.measure` already carries a structured reading. The seven unimplemented
  metrics in `syscall-sysinfo.yaml` want exactly that shape.

  Note one genuine tension to resolve there rather than ignore: `ev.measure` says "the raw stream
  is not recorded, because it is long, noisy and adds nothing an instructor would read". This
  feature records the raw stream on purpose, because for an A-Lab the output *is* the
  deliverable. Both can be true — they are different kinds of evidence — but a grading path that
  uses `measure` should say which it is doing.

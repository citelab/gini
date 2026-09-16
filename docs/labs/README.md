# Lab handouts — and what GINI still has to do to make them true

Two handouts, written **before** the implementation on purpose. They are student-facing documents
and they double as the specification: every instruction in them is a claim about what GINI does,
so a promise here that GINI cannot keep is a bug in the plan rather than a disappointment in a lab.

- [`assignment-1-sysinfo.md`](assignment-1-sysinfo.md) — Part A (add a system call) + Part B
  (`sysinfo`: free memory and process count). **This year.**
- [`assignment-2-trace.md`](assignment-2-trace.md) — Part A + Part C (`trace`: per-process system
  call tracing, inherited across `fork`). **Next year**, with a different call.

Both open with Part A because the mechanism — five edits in five files — is the lesson that has to
land before anything else, and because the System Calls Lab makes it self-checking in a way a bare
xv6 lab cannot.

## What writing them already caught

- **`kernel/sysinfo.h` does not exist.** MIT's 6.1810 supplies it; stock xv6-riscv does not. GINI
  has to ship it, or Assignment 1 does not compile on line one.
- **`copyout` here takes five arguments**, not four: `copyout(pagetable, p->sz, dstva, src, len)`.
  Every solution a student finds online will be the four-argument form and will not compile. The
  handout says so explicitly.
- **Assignment 1 needs `kalloc.c`, `proc.c` and `defs.h`**, which the first draft of the file list
  did not include. The free list and the process table are private to the files that own them, so
  Part B cannot be done from `sysproc.c` alone. This matters beyond the file list: it means
  Assignment 1 hands over the same GINI-carrying kernel files as Assignment 2, and the earlier
  claim that it had a smaller blast radius was wrong.

## What the student actually looks at

The handouts tell students to press **Load**, press **Revert**, and read a compile log. None of
those have a home: Load and Revert exist as endpoints, but their only UI is the shadow bar bolted
onto the Scheduler face, where it ended up because shadows began as a scheduler feature.

**The plan is one new face — `My Code` — and it is where student-authored kernel code lives for
every lab type, shadows included.**

### Where it goes

The Machine Lab's home page is a stack of layer bands — USER SPACE, the `ecall` boundary, the
SYSTEM-CALL INTERFACE, the KERNEL, the HARDWARE — an architecture diagram you can click. Each band
is a place *in the machine*.

A student's own code is not a layer of the machine; it is an overlay on it. So `My Code` gets its
own band above USER SPACE, labelled for the assignment rather than for a layer. That also keeps it
honest when the lab is about paging or the file system: the face does not move, only its file list
changes.

### What it shows

```
┌ My Code ───────────────────────────────────────────────────────────┐
│  Assignment 1 — sysinfo          ~/.gini/xv6-lab/M1      [ Reveal ] │
│                                                                     │
│  FILES YOU OWN                                                      │
│    syscall.h       edited      [Revert]                             │
│    syscall.c       edited      [Revert]                             │
│    sysproc.c       edited      [Revert]                             │
│    user.h          edited      [Revert]                             │
│    usys.pl         untouched                                        │
│    sysinfo.h       untouched                                        │
│    defs.h          untouched                                        │
│    kalloc.c        untouched                                        │
│    proc.c          untouched                                        │
│    sysinfotest.c   edited      [Revert]                             │
│                                                                     │
│  WIRING                                                             │
│    #define SYS_sysinfo 23      ✓        kernel/syscall.h            │
│    extern uint64 sys_sysinfo   ✓        kernel/syscall.c            │
│    row in syscalls[]           ✓        kernel/syscall.c            │
│    prototype in user.h         ✓        user/user.h                 │
│    entry("sysinfo")            ✗        user/usys.pl                │
│                                                                     │
│  [ Load ]      last build: failed, 12s ago                          │
│    ./user/sysinfotest.c:9: undefined reference to `sysinfo'         │
└─────────────────────────────────────────────────────────────────────┘
```

Four things, in order of how often a student needs them:

1. **Which assignment is armed, and where the files are.** A path they can open in their own
   editor, with a button that reveals it in Finder or Explorer. Students lose this folder.
2. **The files they own, and whether they have touched them.** `edited` versus `untouched` is
   computed the way the shadow bar already does it — an md5 against the pristine copy kept outside
   the mount. Revert is per file, so throwing away a broken `proc.c` does not cost them the
   `syscall.c` they got working.
3. **The wiring check.** GINI parses the student's own files and says which of the five
   registration sites are present. This is the handout's failure table, live, and it turns a
   linker error into a checklist.
4. **Load, and the scoped compile log underneath it** — the same widget as the shadow bar, lifted
   out and made reusable rather than copied.

### How this pairs with the System Calls Lab

They answer different questions and the student needs both:

| | |
|---|---|
| **My Code** | *Is it wired?* — static, read from their files, before anything runs |
| **System Calls Lab** | *Is it running?* — live, read from the kernel, as their program executes |

That is why the handout's check table has three rows rather than two: nothing in the histogram is a
wiring problem, `sys23` is a `#define` problem, and the name appearing means both faces agree.

### The one open question

**How much should the wiring check give away?** Telling a student that `entry("sysinfo")` is
missing is scaffolding; it names the gap, not the fix, and it replaces a link error most of them
cannot read. But the five sites *are* the lesson of Part A, and a checklist that ticks itself off
as they type makes it a fill-in-the-blanks exercise.

Options, in increasing order of restraint: always visible; revealed after the first failed Load;
or behind a "Check my wiring" button they have to choose to press. **Leaning: after the first
failed Load** — they meet the real error first, and get help once it has actually cost them
something.

### Where LAB_FILES comes from

The mission YAML, which ships in `gini-core`. So a new assignment is a `gini-core` release — not a
container image, and not a `gini-toolkit` release either. That is the whole point of
`docs/design/xv6-student-code.md`: the image is a toolchain and a pristine tree, and an assignment
is data.

## What GINI must do that it does not do yet

Grouped by what each unblocks. Nothing here is started.

### The file mechanism
- `LAB_FILES` per assignment: a list of (host filename, path in the kernel tree).
- Mount `~/.gini/xv6-lab/<machine>/` into the container and symlink the tree at it on **every**
  Run, idempotently. Proven feasible by `backend/xv6/lab_feasibility.sh`; not built.
- Seed the folder from the image's pristine copies, plus files xv6 does not have
  (`kernel/sysinfo.h`) and the starter test program.
- Register the lab's user programs in `UPROGS` from `LAB_FILES` — deterministically, not by
  handing students the Makefile.
- `_touch_sources` in the agent must walk `LAB_FILES`, or the stale-build fix does not cover
  these files and a Load can silently load the previous kernel.

### The `My Code` face (see above)
- The face itself: file list with per-file state, Revert, Load, the scoped compile log, and the
  wiring check. New band on the Machine Lab home page.
- Lift the shadow bar's Load/Revert/log out of `machine_lab.py`'s Scheduler face into a reusable
  widget, so shadows and lab files share one implementation rather than two that drift.
- Per-file Revert: `/revert?sub=` is keyed to the three shadow subsystems, not to a lab file list.
- "Reveal in Finder/Explorer" for the lab folder.
- The wiring check needs a small parser per lab — for a syscall lab, five patterns over the
  student's own files. It belongs in `core/` (pure, testable) with the face only rendering it.

### The feedback loop the handouts are built around
- **`SyscallLab(name_extra=…)` is unwired.** Every "by name" claim in both handouts depends on it.
  The source is a parse of the student's own `syscall.h` — which is what makes the face a check on
  their work rather than a decoration.
- Counting already works: a new syscall appears as `SC 23` on the shipped image with no kernel
  change. Verified.

### Grading
- Syscall metrics in `Xv6Runner` (it has ~20 metrics and none about syscalls).
- Assignment 1: compare the student's `freemem`/`nproc` against GINI's own readings. `free_pages`
  is already a metric and the process table is already parsed; the comparison is not written.
- Assignment 2: compare the student's trace against `parse_sctrace`'s `TRACE` lines. Both halves
  exist; the comparison does not.
- Submission collects `SHADOW_FILES`, a fixed 3-tuple. It must collect the lab's own file set, or
  a marker receives a chain saying a kernel was built with no way to read what was in it.

### Still unanswered from the feasibility work
- **Rootless Podman and SELinux.** `lab_feasibility.sh` answers it; it has only ever run on
  macOS/Docker.
- `provider.apply_syscall` is referenced by `ui/main_window.py` and implemented nowhere, so the
  Syscall Builder's Apply has never worked on any build. Assignment 1 deliberately does not use
  the Builder — the five edits are the lesson — but the button should stop claiming otherwise.

## Cross-references

`docs/design/xv6-student-code.md` — the mechanism and why no image release is needed per
assignment · `backend/xv6/lab_feasibility.sh` — the proof, 16/16 on macOS/Docker ·
`docs/XV6_LAB_PODMAN_TEST_PLAN.md` — running that on Podman.

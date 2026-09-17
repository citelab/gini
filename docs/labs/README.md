# Lab handouts — and what GINI still has to do to make them true

Two handouts, written **before** the implementation on purpose. They are student-facing documents
and they double as the specification: every instruction in them is a claim about what GINI does,
so a promise here that GINI cannot keep is a bug in the plan rather than a disappointment in a lab.

- [`a-lab-01-sysinfo.md`](a-lab-01-sysinfo.md) — Part A (add a system call) + Part B
  (`sysinfo`: free memory and process count). **This year.**
- [`a-lab-02-trace.md`](a-lab-02-trace.md) — Part A + Part C (`trace`: per-process system
  call tracing, inherited across `fork`). **Next year**, with a different call.

Both open with Part A because the mechanism — five edits in five files — is the lesson that has to
land before anything else, and because the System Calls Lab makes it self-checking in a way a bare
xv6 lab cannot.

## What writing them already caught

- **`kernel/sysinfo.h` does not exist.** MIT's 6.1810 supplies it; stock xv6-riscv does not. GINI
  has to ship it, or A-Lab 01 does not compile on line one.
- **`copyout` here takes five arguments**, not four: `copyout(pagetable, p->sz, dstva, src, len)`.
  Every solution a student finds online will be the four-argument form and will not compile. The
  handout says so explicitly.
- **A-Lab 01 needs `kalloc.c`, `proc.c` and `defs.h`**, which the first draft of the file list
  did not include. The free list and the process table are private to the files that own them, so
  Part B cannot be done from `sysproc.c` alone. This matters beyond the file list: it means
  A-Lab 01 hands over the same GINI-carrying kernel files as A-Lab 02, and the earlier
  claim that it had a smaller blast radius was wrong.

## What the student actually looks at

The handouts tell students to press **Load**, press **Revert**, and read a compile log. None of
those have a home: Load and Revert exist as endpoints, but their only UI is the shadow bar bolted
onto the Scheduler face, where it ended up because shadows began as a scheduler feature.

**The plan is one new face — `User Code` — and it is where student-authored kernel code lives for
every lab type, shadows included.**

### Where it goes

The Machine Lab's home page is a stack of layer bands — USER SPACE, the `ecall` boundary, the
SYSTEM-CALL INTERFACE, the KERNEL, the HARDWARE — an architecture diagram you can click. Each band
is a place *in the machine*.

A student's own code is not a layer of the machine; it is an overlay on it. So `User Code` gets its
own band above USER SPACE, labelled for the assignment rather than for a layer. That also keeps it
honest when the lab is about paging or the file system: the face does not move, only its file list
changes.

### What it shows

```
┌ User Code ───────────────────────────────────────────────────────────┐
│  A-Lab 01 — sysinfo          ~/.gini/xv6-lab/M1      [ Reveal ] │
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
| **User Code** | *Is it wired?* — static, read from their files, before anything runs |
| **System Calls Lab** | *Is it running?* — live, read from the kernel, as their program executes |

That is why the handout's check table has three rows rather than two: nothing in the histogram is a
wiring problem, `sys23` is a `#define` problem, and the name appearing means both faces agree.

### The checklist is always visible, and it is a tracker

Decided: show it. A student should be able to see what the assignment requires and how far they
have got. The five sites are still the lesson — the checklist names the *gap*, never the fix, and
"you have not written `entry("sysinfo")` yet" is a better teacher than a linker error most of them
cannot read.

**It is a tracker, not the grade, and the difference is load-bearing.** Every check is a pattern
over the student's own source, so it answers "have you written the line" and never "does it work".
A line that is present but wrong passes. Comments are stripped before matching, so commenting
something out does not tick it off. What is actually graded is measured while their code runs —
see `grade:` below.

### An assignment is a YAML file

The real one is `core/src/gini/domain/labs/syscall-sysinfo.yaml` — read that rather than a
copy here, because a second copy of a spec is a second thing to keep in step. Nothing in it is special-cased in
GINI: the Machine Lab reads it to know which files the student owns, what to seed, what to register
in the build, what the tracker shows, and what is graded.

```yaml
files:
  - name: syscall.h          # in the lab folder, on the student's machine
    tree: kernel/syscall.h   # where it is linked into the kernel source
  - name: sysinfo.h
    tree: kernel/sysinfo.h
    seed: |                  # xv6 has no sysinfo.h — the assignment carries it
      struct sysinfo { uint64 freemem; uint64 nproc; };
  - name: sysinfotest.c
    tree: user/sysinfotest.c
    uprog: sysinfotest       # registered in UPROGS from HERE, so they never edit the Makefile

checks:
  - id: usys
    part: A
    label: "Generate the trampoline"
    where: user/usys.pl
    match: 'entry\s*\(\s*"sysinfo"\s*\)'
    hint: "Miss this and nothing fails until the LINKER."

grade:
  - id: freemem_agrees
    metric: sysinfo_freemem_vs_free_pages
    tolerance_pages: 2
```

Three things this buys. A new assignment is **a file, not a code change**. The same framework
covers the shadow labs, which today have their own bespoke bar. And the handout, the tracker and
the grader stop being three descriptions of an assignment that can disagree — `where:` and
`hint:` are the handout's words, and if the assignment changes they change in one place.

It ships in `gini-core`, so a new assignment is a `gini-core` release — not a container image, and
not a `gini-toolkit` release. That is the whole point of `docs/design/xv6-student-code.md`: the
image is a toolchain and a pristine tree, and an assignment is data.

### Why `kalloc.c` is in the file list, and `proc.c` is not

Checked against the pinned kernel rather than assumed.

**`kalloc.c` is unavoidable.** The free list is a chain of `struct run`, and that type is declared
at `kernel/kalloc.c:17` and nowhere else — not in `defs.h`, not in a header. There is no way to
walk `kmem.freelist` from another file, because the type that makes it walkable does not exist
outside this one. That is not an obstacle to route around; it *is* part B1's lesson, and it is what
makes the cross-check real: the student counts by walking the list, GINI counts from its page
bitmap, and the two agreeing means something precisely because they are different methods.

**`proc.c` is not needed.** `struct proc proc[NPROC]` at `proc.c:11` has external linkage and
`struct proc` is in `proc.h`, which `sysproc.c` already includes. A student can write
`extern struct proc proc[NPROC];` and walk it from `sysproc.c`. Putting the accessor where the data
lives is better style, but it is a style point — and `proc.c` is the largest file in the set and
the one carrying GINI's scheduler shadow dispatcher.

So A-Lab 01 hands over **nine files, not ten**, and the one removed is the riskiest. The
earlier claim that A-Lab 01 and A-Lab 02 have the same blast radius was wrong in the other
direction: A-Lab 02 does need `proc.c`, because `trace` has to hook `fork()`, and there is no
way to do that from outside.

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

### The `User Code` face (see above)
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
- A-Lab 01: compare the student's `freemem`/`nproc` against GINI's own readings. `free_pages`
  is already a metric and the process table is already parsed; the comparison is not written.
- A-Lab 02: compare the student's trace against `parse_sctrace`'s `TRACE` lines. Both halves
  exist; the comparison does not.
- Submission collects `SHADOW_FILES`, a fixed 3-tuple. It must collect the lab's own file set, or
  a marker receives a chain saying a kernel was built with no way to read what was in it.

### Still unanswered from the feasibility work
- **Rootless Podman and SELinux.** `lab_feasibility.sh` answers it; it has only ever run on
  macOS/Docker.
- `provider.apply_syscall` is referenced by `ui/main_window.py` and implemented nowhere, so the
  Syscall Builder's Apply has never worked on any build. A-Lab 01 deliberately does not use
  the Builder — the five edits are the lesson — but the button should stop claiming otherwise.

## Cross-references

`docs/design/xv6-student-code.md` — the mechanism and why no image release is needed per
assignment · `backend/xv6/lab_feasibility.sh` — the proof, 16/16 on macOS/Docker ·
`docs/XV6_LAB_PODMAN_TEST_PLAN.md` — running that on Podman.

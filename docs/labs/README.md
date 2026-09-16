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

### The buttons the handouts tell students to press
- **Load** and **Revert** exist (`/rebuild`, `/revert`) but the only UI for them is the shadow bar
  on the Scheduler face. A syscall lab needs them, with the scoped compile log underneath.
- Per-file Revert: `/revert?sub=` is keyed to the three shadow subsystems, not to a lab file list.

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

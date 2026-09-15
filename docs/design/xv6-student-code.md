# Compiling student code in the shipped xv6 image

**Status: feasibility established by experiment (2026-09-15, branch `xv6-user-code`).
Two fixes landed; the rest is unbuilt and specified below.**

The question this answers: can we ship a syscall assignment — where a student writes a real
system call, writes an app that calls it, compiles both, and watches it in the Syscall Lab —
**without releasing a container image per assignment?**

Yes. Measured, not argued: `backend/xv6/lab_feasibility.sh` does the whole loop against
`gini-xv6:latest` and reports **16/16 on macOS/Docker**, in about 40 seconds, with a **1-second**
incremental kernel build. Run it on your own machine before believing this page.

## Why no image rebuild is needed

The image is not assignment content. It is a RISC-V cross-toolchain plus a *pristine, already
built* xv6 tree — `kernel/kernel` and `fs.img` are produced at image-build time
(`Dockerfile:37`), and 27 `kernel/*.o` plus 28 user programs ship with them. The agent already
compiles **inside the running container** and already owns QEMU as a child so it can relaunch it
(`boot.sh`, and `_rebuild()` in `gini_agent.py`). Everything an assignment adds is *data*.

Three facts make the syscall lab in particular nearly free:

- The pinned kernel's syscalls stop at `SYS_sync = 22`, and `domain/syscall_builder.py` computes
  `NEXT_FREE_NUMBER = 23`. **Verified against the image**, not assumed.
- `gini_sccount[64]` counts by syscall NUMBER, so a student's syscall 23 appears in the histogram
  the first time it is called, with **no kernel patch change**. Confirmed live: `SC 23 2`.
- `syscall_name(num, extra)` already falls back to `sys23`, and `SyscallLab(name_extra=…)` is
  already plumbed into both the histogram and the strace view.

So the image is rebuilt only when the *instrumentation* changes — not when an assignment does.

## The mechanism: one mounted directory, symlinks into the tree

Today exactly one host directory is mounted into an xv6 container:
`~/.gini/xv6-shadows/<machine>/` over `kernel/shadows/` (`compiler.py:1173`). A syscall lab has to
edit six files spread across `kernel/`, `user/` and the tree root, and mounting those directories
wholesale would hand the student `trap.c` and every `.o`.

Instead: mount **one** flat host folder at `/opt/xv6-lab`, and at container start replace each lab
file in the tree with a **symlink** at it. A single-file bind mount binds an *inode*, and editors
save via rename, which breaks it — that is the documented reason `kernel/shadows/` is mounted as a
directory (`compiler.py:1151`). A symlink resolves by **path**, so it survives a rename, and it
also gives per-file selection and per-file revert, which a directory mount cannot.

```sh
[ -f /opt/xv6-lab/syscall.c ] || cp kernel/syscall.c /opt/xv6-lab/syscall.c   # seed once
[ -L kernel/syscall.c ] || cp kernel/syscall.c /opt/gini_orig/syscall.c       # keep the pristine
ln -sf /opt/xv6-lab/syscall.c kernel/syscall.c
```

The `[ -L ]` guard is the safety of the whole scheme. The link step runs on **every** Run — a
fresh container is always pristine, verified in check 7 — and without the guard a second pass
would save a *symlink* as the pristine copy and destroy the only good original. The script tests
this explicitly.

This generalises. `LAB_FILES` as a list of `(host filename, container path)` covers the syscall
lab, the shadow labs, and any later "modify the kernel" lab; only the list changes. Blast radius
scales with the list, which is the point — a COW lab must link `trap.c`, a syscall lab must not.

## What the experiments actually showed

| # | Question | Result |
|---|---|---|
| 1 | Can the shipped image compile a new syscall? | Yes — `rc=0` in **1s**, incremental |
| 2 | Does the codegen's `[SYS_x] sys_x,` (no `=`) compile under `-Wall -Werror`? | **Yes** |
| 3 | Does a new *user program* build? | Yes — needs a `Makefile` UPROGS line and an `fs.img` rebuild |
| 4 | Does the Syscall Lab see it? | Yes — `SC 23 3`, no kernel change |
| 5 | Does student work survive Stop/Run? | Yes on the host; the container is always pristine |
| 6 | Does a fresh container recover it? | Yes — relink + 1s build |
| 7 | Rootless/SELinux file ownership | **Untested off macOS** — this is what the script is for |

Two things worth knowing that only came out of running it:

**`mkfs` overwrites `fs.img` while QEMU still has it open.** `_rebuild()` builds *then* restarts
QEMU. The kernel survived it here, but the ordering is luck, not design: the safe order is stop
QEMU, build, start. Cheap to fix, and it removes a class of "my file system went weird" reports.

**A host write is not always instantly visible in the container.** Seen twice on macOS
(Docker Desktop / virtiofs) and never on a native Linux bind mount, where host and container share
one page cache. Once it produced phantom compile errors from a truncated read; once — far worse —
`make` decided it was up to date, `/rebuild` returned `{"ok": true, "log": "loaded"}`, and QEMU
came back running the **old kernel** while the student's app printed `unknown sys call 23`. A
second Load fixed it. Neither reproduced in 3 repeats or 60 controlled trials, so the mechanism is
not pinned down; the mitigation is cheap and worth taking regardless:

- write lab files **atomically** (temp + `os.replace`) wherever gBuilder writes them;
- have `/rebuild` **verify** rather than trust — compare the built kernel against the source it
  claims to be built from, and say "no change detected" instead of "loaded".

Check 4 of the script asserts `make` actually recompiled, so this shows up as a failure rather
than as a confused student.

## What is missing in the Machine Lab

The honest inventory. Everything here is host-side — gBuilder and the agent, no image.

1. **The Syscall Builder cannot apply anything.** `ui/machine_lab.py:716` looks for
   `provider.apply_syscall`, and **no provider anywhere implements it** — the only reference in
   the tree is that `getattr`. So Apply always falls to the else branch and tells the student the
   feature is "available on the desktop build", which is not true on any build. Either implement
   it against the lab folder or change the message.
2. **No way to input the test app.** The builder generates the five kernel/user edits and stops.
   A syscall the student cannot call proves nothing. The lab needs an editable
   `user/<name>test.c` in the same lab folder, plus its `Makefile` UPROGS line — both confirmed
   working above.
3. **No Load button outside the shadow bar.** `/rebuild` and `/revert` exist and work
   (`Xv6Bridge.load`/`revert`), but the only UI for them is the shadow bar on the Scheduler face,
   live mode only (`machine_lab.py:1189`). The syscall lab needs the same three controls —
   Load, Revert, inline log — which argues for lifting the bar into a reusable widget rather
   than copying it.
4. **The student's app is not launchable.** `/programs` is a hardcoded list of ten; a student's
   `sysinfotest` is not in it. It runs fine by typing it at the Keyboard, so this is a
   convenience, not a blocker — but "my program isn't in the menu" will be asked.
5. **`name_extra` is unwired.** Nothing passes it, so a student's syscall shows as `sys23`. The
   source should be the student's **own** `syscall.h` — parse `#define SYS_(\w+) (\d+)` out of the
   lab folder. Then the call is named in the face *because they wired the table entry correctly*,
   which is the feedback the lab wants.
6. **Submission collects the wrong file set.** `xv6_shadows.SHADOW_FILES` is a fixed 3-tuple. A
   syscall submission must carry the lab's own file list, or the marker gets a chain that says a
   kernel was built and no way to read what was in it — the exact failure that module was written
   to prevent.
7. **`Xv6Runner` has no syscall metric.** ~20 metrics, none about syscalls. Pure and
   offline-testable, so it can land before any of the UI.

## Error display — fixed

The student's question "why didn't it build?" was answered badly, and in one case dishonestly.

`_scope_errors` kept lines containing `"gini_sched"` or `"error:"`. Both halves fail on a real
syscall lab:

- A **linker** failure contains no `"error:"` anywhere. Forgetting `entry("sysinfo");` in
  `user/usys.pl` is the most common mistake in this lab, and its entire message is
  `undefined reference to 'sysinfo'` — dropped.
- With nothing kept, the fallback returned the raw log tail. The kernel's **link command** lists
  `kernel/shadows/gini_sched.o`, so that 580-character `ld` invocation matched `"gini_sched"` and
  became the whole report. A student doing the syscall lab was pointed at the scheduler shadow —
  a file they had never opened — and the real error appeared nowhere.

Now: drop the invocations, keep the diagnostics. The distinction is a tool name followed by
whitespace (a command) versus followed by a colon (that tool reporting a problem). gcc's source
echo and caret line are kept, because they are the most useful part of the message; two lines of
toolchain noise that appear in *every* failure are dropped, because a report that always carries
the same irrelevant lines teaches students to skim. The filter names no lab file, so it reads a
shadow build and a syscall build the same way.

Before and after, on the real linker log:

```
OLD  riscv64-unknown-elf-ld -z max-page-size=4096 -T kernel/kernel.ld -o kernel/kernel
     kernel/shadows/gini_fs.o kernel/shadows/gini_vm.o kernel/shadows/gini_sched.o … (580 chars)

NEW  riscv64-unknown-elf-ld: user/sysinfotest.o: in function `main':
     ./user/sysinfotest.c:9: undefined reference to `sysinfo'
     make: *** [Makefile:110: user/_sysinfotest] Error 1
```

`frontend-ng/tests/test_xv6_build_errors.py` guards this with **real captured `make` output**,
not written-from-memory approximations — the bug was invisible to a plausible fixture, since the
giveaway was a command line that happened to contain the word `gini_sched`.

No debugger is needed. A file, a line, a column and the source line is what a student uses.

## Still open

- **Rootless Podman and SELinux.** The one thing macOS cannot answer. The script mounts with `:z`
  under Podman and checks both directions of ownership; run it on the home box and on campus.
- Stop QEMU before `make` touches `fs.img`.
- Decide the first assignment: `sysinfo` (gradeable against the existing `free_pages` and process
  table) or `trace` (needs console capture).
- Author the `LAB_FILES` list and the mission YAML.

## Cross-references

`backend/xv6/lab_feasibility.sh` · [os-shadows](../manual/os-13-shadows.md) ·
[os-lab-provenance](os-lab-provenance.md) · [xv6-rebuild-batch](xv6-rebuild-batch.md)

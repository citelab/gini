# Assignment 2 — Adding a system call: `trace`

**xv6 · GINI Machine Lab · two parts**

You are going to add a system call that makes the kernel report on itself. `trace(mask)` turns on
per-process tracing: from then on, every system call that process makes prints a line saying which
call it was and what it returned — and children inherit it across `fork()`.

Part A is the mechanism: connecting a name in user space to code in the kernel. Part B is the
content, and it has an unusual property. **You have been watching a system-call trace all term.**
The System Calls Lab is one. In Part B you build your own, and then check it against GINI's.

---

## Before you start

Open your topology in gBuilder, press **Run**, and double-click the xv6 machine to open the
**Machine Lab**.

Your files are on your own computer:

```
~/.gini/xv6-lab/<machine-name>/
    syscall.h      syscall.c      sysproc.c
    user.h         usys.pl
    proc.h         proc.c
    trace.c
```

Edit them where you like. GINI links them into the kernel source inside the machine, so what you
write is what gets compiled, and your work survives Stop/Run.

**Load** compiles and restarts the machine. **Revert** puts one file back. **Keyboard** types at
the xv6 shell. A kernel that does not compile leaves the machine running the one it already had.

> This assignment hands you `kernel/proc.c` and `kernel/proc.h`, which is more rope than
> Assignment 1. Those files also contain GINI's own machinery — the scheduler shadow dispatcher
> lives in `proc.c`. You can break things the Machine Lab depends on. Revert is per-file and
> always works; use it early rather than fighting a kernel that will not boot.

---

## Part A — Make the kernel answer to a new name

Your program cannot call into the kernel directly. It raises a trap, and the kernel looks up what
you asked for in a table. Connecting the two takes **five edits in five files**, and missing any
one gives a different error at a different moment.

### A1. `kernel/syscall.h` — give it a number

The last is `SYS_sync 22`, so:

```c
#define SYS_trace 23
```

### A2. `kernel/syscall.c` — declare it and add the table row

```c
extern uint64 sys_trace(void);
```

```c
[SYS_trace] sys_trace,
```

### A3. `kernel/sysproc.c` — the kernel side

Arguments arrive in registers. `argint` reads the first as an integer:

```c
uint64
sys_trace(void)
{
  int mask;
  argint(0, &mask);
  return 0;
}
```

### A4. `user/user.h` — declare it for your program

```c
int trace(int mask);
```

### A5. `user/usys.pl` — generate the trampoline

```perl
entry("trace");
```

### A6. `user/trace.c` — a program that uses it

The classic shape: set the mask, then `exec` whatever came after on the command line, so the
tracing applies to *that* program.

```c
#include "kernel/types.h"
#include "kernel/param.h"
#include "user/user.h"

int
main(int argc, char *argv[])
{
  if (argc < 3) {
    printf("usage: trace mask command [args]\n");
    exit(1);
  }
  if (trace(atoi(argv[1])) < 0) {
    printf("trace failed\n");
    exit(1);
  }
  char *nargv[MAXARG];
  for (int i = 2; i < argc && i < MAXARG + 2; i++)
    nargv[i - 2] = argv[i];
  exec(nargv[0], nargv);
  exit(0);
}
```

### Run it, and watch it

Press **Load**, then at the **Keyboard**:

```
trace 32 grep hello README
```

Nothing should be traced yet — Part A is about the call arriving. Open the **System Calls Lab**:
`trace` is in the histogram, **by name**, the moment your program runs.

| What you see | What it means |
|---|---|
| nothing in the histogram | your program never reached the kernel — A2's table row, or A5 |
| `sys23` rather than `trace` | it works, but A1's `#define` is wrong or missing |
| `trace` | all five edits are right |

---

## Part B — Make the kernel report on itself

`trace(mask)` takes a bitmask of system call numbers: bit *n* set means "trace call number *n*".
`trace(32)` is `1 << 5`, and `SYS_read` is 5, so that traces reads.

For every traced call, the process prints:

```
3: syscall read -> 1023
```

— the pid, the name, and the return value. Three pieces of work.

### B1. Remember the mask per process — `kernel/proc.h`

A mask belongs to a process, not to the system, so it goes in `struct proc`. Add a field. Read the
comments around the struct first: they tell you which lock covers which field, and yours should
sit with the ones that are private to the process.

### B2. Inherit it across `fork()` — `kernel/proc.c`

`fork()` builds a new process from an old one, copying what a child should start life with. Your
mask is one of those things: `trace 32 grep …` runs `grep` in a child, and tracing has to survive
into it.

Find where `fork()` copies state from parent to child and add yours.

> Get this wrong and the symptom is precise: tracing works until the first `fork()` and then
> stops. If that is what you see, you know exactly which line is missing.

### B3. Print the traced calls — `kernel/syscall.c`

Read `syscall()` properly now. It is the funnel every system call in the system goes through:

```c
num = p->trapframe->a7;
if (num > 0 && num < NELEM(syscalls) && syscalls[num]) {
    p->trapframe->a0 = syscalls[num]();
    ...
}
```

The number is in `a7` before dispatch; the return value is in `a0` after it. Your print goes after
the call, when there is a result to report, and only when the mask has that bit set.

You also need names. The kernel has the numbers but not the strings — add an array of syscall
names indexed the same way as `syscalls[]`.

**You will notice GINI's own instrumentation in this function** — it records pid, number, first
argument and return value into a ring buffer, which is what feeds the System Calls Lab. It is
doing a version of what you are about to do. Read it; it is a worked example sitting in the file
you have to edit. Then write yours, because yours has a per-process mask and prints names, and
that is the part that is actually about processes.

### Check yourself against the kernel's own trace

This is the interesting bit. Two independent traces of the same run:

1. Run `trace 32 grep hello README` and keep your output.
2. Open the **System Calls Lab** and look at the trace view for the same program.

Every `read` your trace reports should be one GINI recorded, with the same pid and the same return
value. If GINI shows reads yours missed, your mask test or your placement in `syscall()` is wrong.
If yours shows calls GINI did not, you are printing somewhere the call did not actually happen.

Two independent observations of one run agreeing is what makes either of them believable.

---

## What is graded

**Part A** — `trace` appears by name in the System Calls Lab when your program runs, and your
program receives the value your kernel returned.

**Part B** —

- `trace 32 grep hello README` traces reads and nothing else
- a mask with several bits traces exactly those calls
- tracing **survives `fork()`** — a traced parent produces a traced child
- an untraced process prints nothing
- pid, name and return value are right, checked against GINI's trace of the same run

Your kernel builds cleanly and the machine boots.

---

## When it goes wrong

| Message | Which edit |
|---|---|
| `'SYS_trace' undeclared` | A1 — the `#define` |
| implicit declaration of `sys_trace` | A2 — the `extern` |
| builds, but `unknown sys call 23` at runtime | A2 — the row in `syscalls[]` |
| implicit declaration of `trace` *in your program* | A4 — `user/user.h` |
| `undefined reference to 'trace'` at **link** time | A5 — `user/usys.pl` |

That last one is the most common mistake here, and it does not appear until linking.

Two more, specific to Part B:

- **Tracing stops at the first `fork()`** — B2. The child did not inherit the mask.
- **Every process traces, including the shell** — your field is not being initialised for
  processes that never called `trace`, so it is holding whatever was in that slot before.

If the machine will not boot, **Revert** the file you changed last. `proc.c` is the usual suspect:
it is the biggest file in this assignment and it holds machinery the Machine Lab needs.

---

## Submitting

Press **Submit** in the Machine Lab with your assignment code armed. Your kernel files travel with
the submission.

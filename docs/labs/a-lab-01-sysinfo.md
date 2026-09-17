# A-Lab 01 — Adding a system call: `sysinfo`

**xv6 · GINI Machine Lab · two parts**

You are going to add a new system call to a real operating system kernel, call it from your own
program, and watch it happen.

Part A is the mechanism: what it takes for a name you invent in user space to arrive at code you
wrote in the kernel. Part B is the content: making that call report how much memory is free and
how many processes exist — two numbers the kernel does not store anywhere and has to work out by
walking its own data structures.

---

## Before you start

Open your topology in gBuilder and press **Run**. Double-click the xv6 machine to open the
**Machine Lab**.

**Your files are on your own computer**, not inside the container:

```
~/.gini/xv6-lab/<machine-name>/
    syscall.h      syscall.c      sysproc.c      defs.h
    kalloc.c       trap.c         sysinfo.h
    user.h         usys.pl        sysinfotest.c
```

Part A touches five of these. Part B adds `kalloc.c` and `defs.h`, because the free list is a chain
of `struct run` — a type declared inside `kalloc.c` and nowhere else, so there is no way to walk it
from another file. The process table is different: `proc[]` is reachable from `sysproc.c` with an
`extern`, so `proc.c` is not yours to edit in this assignment.

Edit them in whatever editor you like. They are the real kernel files — GINI links them into the
kernel source tree inside the machine, so what you write is what gets compiled. They survive
Stop/Run, so your work is not lost when you close the lab.

Three buttons matter:

| | |
|---|---|
| **Load** | compiles your kernel and restarts the machine with it (about a second) |
| **Revert** | throws away your changes to one file and puts the original back |
| **Keyboard** | type at the xv6 shell |

If your code does not compile, **the machine keeps running the kernel it already had** and the
errors appear under the button. You cannot break the lab by writing bad C.

---

## Part A — Make the kernel answer to a new name

A system call is not a function call. Your program cannot jump into the kernel; it raises a trap,
and the kernel looks up what you asked for in a table. Connecting a name to an implementation
takes **five separate edits**, in five different files. Miss any one and you get a different
error, at a different moment.

### A1. Give it a number — `kernel/syscall.h`

Every system call is a number; the name is for you. The last one is `SYS_sync 22`, so yours is 23:

```c
#define SYS_sysinfo 23
```

### A2. Declare it and put it in the table — `kernel/syscall.c`

Two edits in this file. The declaration, next to the others:

```c
extern uint64 sys_sysinfo(void);
```

and a row in `syscalls[]`, which is what the kernel actually indexes with your number:

```c
[SYS_sysinfo] sys_sysinfo,
```

Read `syscall()` in this file before you go further. It is short, and it is the entire mechanism:
the number arrives in `p->trapframe->a7`, the table is indexed, and the return value is put back
in `p->trapframe->a0`. Every system call in the system goes through those few lines.

### A3. Write the kernel side — `kernel/sysproc.c`

For Part A it can do almost nothing. Arguments come from registers, not from a C argument list —
`argaddr` reads the first one as an address:

```c
uint64
sys_sysinfo(void)
{
  uint64 addr;
  argaddr(0, &addr);
  return 0;
}
```

### A4. Declare it for your program — `user/user.h`

```c
int sysinfo(struct sysinfo *info);
```

### A5. Generate the trampoline — `user/usys.pl`

This Perl script writes the assembly stub that puts your number in `a7` and executes `ecall`:

```perl
entry("sysinfo");
```

### A6. Write a program that calls it — `user/sysinfotest.c`

```c
#include "kernel/types.h"
#include "kernel/sysinfo.h"
#include "user/user.h"

int
main(void)
{
  struct sysinfo info;
  if (sysinfo(&info) < 0) {
    printf("sysinfo failed\n");
    exit(1);
  }
  printf("free bytes: %d\nprocesses:  %d\n", (int)info.freemem, (int)info.nproc);
  exit(0);
}
```

`kernel/sysinfo.h` is already in your folder. Read it — it is four lines, and it is the contract
between your program and your kernel.

### Now run it

Press **Load**, wait for the machine to come back, then at the **Keyboard**:

```
sysinfotest
```

It should print zeros. That is fine — Part A is about the call arriving, not the answer.

### Watch it happen

Open the **System Calls Lab**. Your call is in the histogram, **by name**, and in the trace as your
program runs.

This is your check for Part A, and it is precise:

| What you see | What it means |
|---|---|
| nothing in the histogram | your program never reached the kernel — A2's table row, or A5 |
| `sys23` rather than `sysinfo` | it works, but the `#define` in A1 is wrong or missing |
| `sysinfo` | all five edits are right |

---

## Part B — Make it tell the truth

Now the call does something. Open `kernel/sysinfo.h` and look at what it promises:

```c
struct sysinfo {
  uint64 freemem;     // bytes of free memory
  uint64 nproc;       // processes whose state is not UNUSED
  uint64 nrunnable;   // ready to run but not running — the run queue
  uint64 nsleeping;   // blocked, waiting for something
  uint64 memused;     // bytes the processes are holding
  uint64 uptime;      // ticks since boot (a tick is about half a second here)
};
```

**Not one of those is stored by the kernel.** There is no variable holding "free memory" and none
holding "how many processes". The kernel derives every one of them by walking something it already
has — and that is the lesson, not the arithmetic.

The first two are worked through below, in full. The rest are yours, and they are deliberately the
same two walks asking different questions.

### B1. Counting free memory — `kernel/kalloc.c`

Free memory in xv6 is **a linked list**, not a counter. Look at the top of the file:

```c
struct {
  struct spinlock lock;
  struct run *freelist;
} kmem;
```

Every free page is one `struct run` holding a pointer to the next. So "how much is free" means
walking that list and multiplying by `PGSIZE`. Take `kmem.lock` while you walk it — another CPU
can be allocating while you count.

Add a function here that returns the number of free bytes, and declare it in `kernel/defs.h` so
`sysproc.c` can call it.

### B2. Counting processes — `kernel/sysproc.c`

The process table is a fixed array, `struct proc proc[NPROC]`, with `NPROC` = 64, and unlike the
free list it is reachable from outside the file that owns it. Declare it and walk it:

```c
extern struct proc proc[NPROC];
```

A slot is in use when its `state` is not `UNUSED`. Take each `p->lock` before reading its state —
a process can be exiting while you look at it.

### B3. Now the rest — same walks, different questions

You have written both walks. Everything else in the struct comes from one of them, and the point
of doing them yourself is that you will find each one is a single line in a loop you already have.

| Field | What it is | Where it comes from |
|---|---|---|
| `nrunnable` | the **run queue**: processes ready but not on a CPU | the process walk — count `state == RUNNABLE` |
| `nsleeping` | processes blocked on something | the same walk — `state == SLEEPING` |
| `memused` | bytes the processes are holding between them | the same walk — add up `p->sz` |
| `uptime` | ticks since boot | `extern uint ticks;` — and take `tickslock` to read it, the way `sys_sleep` does a few lines above you |

One pass over `proc[]` fills four of the six fields. Do not write four loops; you are already
holding each `p->lock` once, and taking it four times is both slower and a good way to deadlock.

> **`nrunnable` is the interesting one.** It is the number every operating system reports as its
> load, and it is the queue the scheduler picks from — the one you watched move in the Process
> Scheduler face. Run `spin &` three times and look at it again.

### B4. Returning a struct to user space — `kernel/sysproc.c`

This is the part worth slowing down for.

`argaddr(0, &addr)` gives you a number the **user program** uses as an address. You cannot write
to it. The kernel runs on a different page table; that address means something else here, or
nothing at all. Dereferencing it is how a kernel corrupts a random page or panics.

To move bytes across that boundary you use `copyout`, which walks the process's page table
properly:

```c
copyout(p->pagetable, p->sz, addr, (char *)&info, sizeof(info));
```

Note the **five** arguments — `p->sz` is second. Solutions you find elsewhere will show four; they
are for a different version of xv6 and will not compile here.

For a worked example already in the tree, read `filestat` in `kernel/file.c` — it returns a
`struct stat` to user space in exactly this way. Yours is the same shape.

Build the struct on the kernel stack, fill it in, copy it out, and return 0 — or -1 if `copyout`
fails, because a program is allowed to pass you a bad pointer and that must not be your problem.

### Check yourself — this is the good part

The kernel is not the only thing that knows these numbers, and **GINI works them out a completely
different way**: it keeps a bitmap of every physical page rather than walking a free list.

So you have an independent answer to check against — and the thing to check is **the change**,
not the absolute number:

1. Open the **Memory Lab** and note the free page count. Run `sysinfotest` and note yours.
2. At the **Keyboard**, run `alloc 20 &` — a program that allocates memory and holds it.
3. Look at both numbers again.

**Both should have dropped by the same amount.** That is the check. A measurement you can move
predictably is a measurement you understand; a number that happens to look plausible once is not.

Do the same with the process count and the **Process** face, using `spin &` to add processes.

The two *absolute* numbers will not be identical, and that is worth understanding rather than
worrying about. They count slightly different things at the edges of memory. Yours — walking the
free list — is the allocator's own answer to "what could I hand out right now", which is what
`freemem` is supposed to mean.

Two independent methods moving together is how you know a number is real. It is also, roughly,
how operating systems are tested.

---

## Part C — Make it remember

Everything so far reads the kernel at the instant you ask. `nrunnable` is the queue length *now* —
run `sysinfotest` twice in a row and you will get two different answers, neither of them wrong and
neither of them useful.

What you actually want to know is whether the machine has *been* busy. Every Unix answers that
with a **load average**: the run queue, averaged over the last few minutes. `uptime` on any Linux
machine prints three of them.

That needs something this kernel has not done yet — **state that is maintained over time, by
something that happens on its own, and read later by somebody else.** The producer is the timer
interrupt; the consumer is your system call. That split is the whole point of this part, and it is
one of the most common shapes in an operating system.

### C1. The one thing that happens on its own — `kernel/trap.c`

Open it and find `clockintr()`:

```c
void
clockintr()
{
  if (cpuid() == 0) {
    acquire(&tickslock);
    ticks++;
    wakeup(&ticks);
    release(&tickslock);
  }
  w_stimecmp(r_time() + 5000000);   // the next tick, about half a second
}
```

This runs on every timer interrupt — twice a second in GINI's xv6. It is the kernel's heartbeat,
and `ticks` is the only thing it currently maintains.

Two things worth noticing before you add to it:

- **The `cpuid() == 0` test.** Every hart takes a timer interrupt; only hart 0 keeps the clock, so
  the others do not each count the same tick. Your sampling belongs inside that test for exactly
  the same reason.
- **`wakeup(&ticks)` walks the entire process table**, taking and releasing every `p->lock` as it
  goes — read it in `kernel/proc.c` if you do not believe it. So taking `p->lock` from a timer
  interrupt is not a daring thing to do here; it is what the line above you already does.

### C2. Somewhere to keep the samples — `kernel/trap.c`

Add this at the **end** of the file:

```c
// A-Lab: the run queue, sampled once a tick, for the last LOADN ticks.
int loadring[LOADN];
int loadi;

void
load_sample(void)
{
  extern struct proc proc[NPROC];
  struct proc *q;
  int n = 0;

  for(q = proc; q < &proc[NPROC]; q++){
    acquire(&q->lock);
    if(q->state == RUNNABLE || q->state == RUNNING)
      n++;
    release(&q->lock);
  }
  loadring[loadi % LOADN] = n;
  loadi++;
}
```

A **ring buffer**: a fixed array and an index that only ever goes up. `loadi % LOADN` wraps it
round, so the array always holds the last `LOADN` samples and the oldest is quietly overwritten.
No allocation, no bookkeeping, and nothing to free — which is why kernels use this shape
constantly.

Note it counts `RUNNABLE` **and** `RUNNING`: a process using a CPU right now is part of the load,
it simply is not queued.

### C3. Averaging it — `kernel/trap.c`

Underneath:

```c
// average of the last `want` samples, x100
uint64
load_avg(int want)
{
  int i, n = 0, have = loadi < LOADN ? loadi : LOADN;
  uint64 sum = 0;

  if(want > have)
    want = have;
  if(want <= 0)
    return 0;
  for(i = 1; i <= want; i++){
    sum += loadring[(loadi - i + LOADN) % LOADN];
    n++;
  }
  return (sum * 100) / n;
}
```

**Why `x100` and not a `float`.** The kernel does not save floating-point registers on a context
switch, so it cannot use them — a `float` in kernel code is a bug that shows up as another
process's arithmetic going wrong. Scaling an integer by 100 and dividing at the end is how kernels
carry fractions. Linux does the same thing with a 2048x fixed point in `calc_load()`.

`have` matters: for the first thirty seconds after boot there are not thirty seconds of history,
and averaging over empty slots would report a load of zero on a busy machine.

### C4. Calling it — `kernel/trap.c`

One line, inside the `cpuid() == 0` test, **after** `release(&tickslock)`:

```c
    release(&tickslock);
    load_sample();
```

After the release, not before: `wakeup` already takes `p->lock` while holding `tickslock`, and
there is no reason for you to hold two locks when one will do.

### C5. Declaring them — `kernel/defs.h`

In the `// trap.c` section:

```c
#define LOADN 60
void            load_sample(void);
uint64          load_avg(int);
```

Sixty samples at half a second each is thirty seconds of history.

### C6. Reading it out — `kernel/sysproc.c`

In `sys_sysinfo`, beside the others:

```c
  info.load5  = load_avg(10);   // 10 samples  = 5 seconds
  info.load15 = load_avg(30);   // 30 samples  = 15 seconds
  info.load30 = load_avg(60);   // 60 samples  = 30 seconds
```

The interrupt handler does the cheap part (one sample) and your system call does the arithmetic.
That is the right way round: whatever runs in an interrupt handler delays everything else on that
CPU.

### Watch it work

```
sysinfotest
spin 40 &
spin 40 &
spin 40 &
```

then run `sysinfotest` every few seconds and watch the three numbers separate and come back
together. Measured on a real machine:

| | 5s | 15s | 30s |
|---|---|---|---|
| idle | 0.00 | 0.04 | 0.04 |
| 12 seconds into three spinners | **3.30** | 2.80 | 1.57 |
| 26 seconds in | 3.20 | 3.06 | 3.13 |

**That separation is the whole idea.** The short average reacts at once; the long one is still
remembering a machine that was idle. Twenty-six seconds in they agree again, because by then the
machine really has been busy for half a minute. It is also why a real `uptime` prints three
numbers instead of one: the shape of the three tells you whether load is arriving or leaving.

## What is graded

**Part A** — `sysinfo` appears by name in the System Calls Lab when your program runs, and your
program receives the value your kernel returned.

**Part B** — `freemem` and `nproc` agree with what GINI reads independently, before and after
processes start and memory is allocated, and the other four fields are right.

**Part C** — the 5-second average rises within a few seconds of processes becoming runnable, and
the 30-second average lags behind it and then catches up. Your kernel builds cleanly and the
machine boots.

Both parts: **no busy-waiting, and no reading `kmem` or `proc[]` without their locks.**

---

## When it goes wrong

The five edits fail in five distinguishable ways. Read the error, then look at the table:

| Message | Which edit |
|---|---|
| `'SYS_sysinfo' undeclared` | A1 — the `#define` |
| implicit declaration of `sys_sysinfo` | A2 — the `extern` |
| builds, but `unknown sys call 23` at runtime | A2 — the row in `syscalls[]` |
| implicit declaration of `sysinfo` *in your program* | A4 — `user/user.h` |
| `undefined reference to 'sysinfo'` at **link** time | A5 — `user/usys.pl` |

That last one is the most common mistake in this assignment. It does not appear until linking,
long after everything has compiled.

A kernel that builds but hangs at boot is usually a lock you took and did not release. Press
**Revert** on the file you changed last and try again — Revert always gets you back to a machine
that boots.

---

## Submitting

Press **Submit** in the Machine Lab with your assignment code armed. Your kernel files travel with
the submission, so write code you would be willing to explain.

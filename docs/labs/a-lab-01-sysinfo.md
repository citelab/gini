A-LAB 01 · OPERATING SYSTEMS · ABOUT TWO HOURS

# Teaching the Kernel a New Word

*Adding a system call, and making it tell you what the machine is doing*

You have been calling system calls all term. `open`, `read`, `fork`, `exit` — every one of them is
a word your program knows and the kernel answers to. Somebody, once, taught the kernel each of
those words.

Today you teach it one of your own. It will be called `sysinfo`, and when you are finished it will
report how much memory is free, how many processes exist, how many are waiting for a CPU, and how
busy the machine has been for the last thirty seconds — none of which the kernel currently knows,
because none of it is written down anywhere.

This is not a design exercise. Every step below tells you which file to open, what to look for, and
exactly what to add. Follow them in order and it will work. The thinking happens afterwards, when
you look at what you built and are asked why it is shaped that way.

> **A promise about breaking things.** You are editing a real kernel. You will get it wrong, and
> the machine will refuse to boot. Every file you own has a **Revert** button beside it, and a
> kernel that does not compile leaves the machine running the one it had. You cannot get into a
> state you cannot get out of.

---

## Part 0 · Before you start

Open your topology in gBuilder and press **Run**. Double-click the xv6 machine to open the
**Machine Lab**, and click **User Code** at the top.

That face is where you live for the next two hours. It lists the ten files that are yours, tells
you which you have changed, ticks off each step as you complete it, and has the **Load** button
that compiles your kernel.

**Your files are on your own computer**, not inside the machine:

```
~/.gini/xv6-lab/<machine-name>/
    syscall.h    syscall.c    sysproc.c    defs.h
    kalloc.c     trap.c       sysinfo.h
    user.h       usys.pl      sysinfotest.c
```

Press **Reveal folder** and open them in whatever editor you like. They are the real kernel files:
GINI links them into the kernel source inside the machine, so what you write is what gets compiled.
They survive Stop and Run, so closing the lab does not lose your work.

| | |
|---|---|
| **Load** | compiles your kernel and restarts the machine with it — about a second |
| **Revert** | throws away your changes to **one** file and puts the original back |
| **Keyboard** | type at the xv6 shell |

Three faces will tell you whether it worked: **User Code** for the checklist, **System Calls** for
whether your call is being made, and **Virtual Memory** and **Process Scheduler** for checking your
answers against the kernel's own.

---

## Part 1 · Teach the kernel a new word

**About 30 minutes.** Five edits in five files. Miss any one and you get a different error at a
different moment, which is the point of doing all five by hand.

Your program cannot jump into the kernel. It raises a trap, and the kernel looks up what was asked
for in a table. These five edits build that path, from the name in your source to the function in
the kernel.

### Step 1 · Give it a number — `kernel/syscall.h`

Every system call is a number; the name is for you. Find the last one:

```c
#define SYS_sync   22
```

Add below it:

```c
#define SYS_sysinfo 23
```

> The name has to be exactly `sysinfo`. Your program will call `sysinfo`, and these two have to be
> the same word or nothing will connect.

### Step 2 · Declare the kernel function — `kernel/syscall.c`

Find the block of `extern` declarations, ending with:

```c
extern uint64 sys_sync(void);
```

Add below it:

```c
extern uint64 sys_sysinfo(void);
```

### Step 3 · Put it in the table — `kernel/syscall.c`

A little further down is the array the kernel actually indexes with your number. Find:

```c
  [SYS_sync]    = sys_sync,
```

Add below it:

```c
  [SYS_sysinfo] = sys_sysinfo,
```

> **Read `syscall()` in this file before you move on.** It is about fifteen lines, and it is the
> entire mechanism: the number arrives in `p->trapframe->a7`, this table is indexed with it, and
> the result is put back in `p->trapframe->a0`. Every system call on the machine goes through those
> lines — including the `write` that printed the last thing you saw.

### Step 4 · Write the kernel side — `kernel/sysproc.c`

Go to the end of the file and add:

```c
uint64
sys_sysinfo(void)
{
  uint64 addr;
  argaddr(0, &addr);
  return 0;
}
```

It does nothing yet. Arguments arrive in registers rather than as C parameters, and `argaddr`
fetches the first one as an address.

### Step 5 · Declare it for your program — `user/user.h`

Find `int sync(void);` and add below it:

```c
struct sysinfo;
int sysinfo(struct sysinfo *);
```

> `struct sysinfo;` first. `user.h` is included by programs that never include `sysinfo.h`, and
> without that line they warn about a type that appears from nowhere.

### Step 6 · Generate the trampoline — `user/usys.pl`

This is a Perl script that writes assembly. Find `entry("sync");` and add below it:

```perl
entry("sysinfo");
```

Each `entry` generates a three-instruction stub: put the number in `a7`, execute `ecall`, return.
That stub is the actual boundary between your program and the kernel.

> **This is the one that will catch you.** Forget it and everything compiles perfectly — then the
> linker says `undefined reference to 'sysinfo'`, because the stub that would have been the
> function was never generated.

### Step 7 · Press Load

Wait for the machine to come back, then at the **Keyboard**:

```
sysinfotest
```

It prints zeros. That is exactly right — Part 1 is about the call arriving, not the answer.

### Step 8 · Watch it happen

Open the **System Calls** lab. Your call is in the histogram, **by name**, and in the trace as your
program runs.

| what you see | what it means |
|---|---|
| nothing in the histogram | your program never reached the kernel — Step 3 or Step 6 |
| `sys23` rather than `sysinfo` | it runs, but Step 1 is wrong or missing |
| `sysinfo` | all five edits are right |

> **Look at the rest of that histogram while you are there.** `write` is enormous — far bigger
> than anything you did on purpose. Remember it; you will be asked about it.

---

## Part 2 · Make it tell the truth

**About 45 minutes.** Now the call does something.

Open `kernel/sysinfo.h`. It is already written for you, and it is the contract between your program
and your kernel:

```c
struct sysinfo {
  uint64 freemem;     // bytes of free memory
  uint64 nproc;       // processes whose state is not UNUSED
  uint64 nrunnable;   // ready to run but not running — the run queue
  uint64 nsleeping;   // blocked, waiting for something
  uint64 memused;     // bytes the processes are holding
  uint64 uptime;      // ticks since boot (a tick is about half a second here)
  uint64 load5;       // Part 3
  uint64 load15;
  uint64 load30;
};
```

**Not one of those numbers exists in the kernel.** There is no variable holding "free memory" and
none holding "how many processes". Every one is worked out, when asked, by walking something the
kernel already has. That is the lesson of this part, and the arithmetic is the easy bit.

### Step 9 · Count the free memory — `kernel/kalloc.c`

Look at the top of the file:

```c
struct {
  struct spinlock lock;
  struct run *freelist;
} kmem;
```

Free memory in xv6 is **a linked list**. Every free page is one `struct run` holding a pointer to
the next free page — the list lives *inside* the free memory itself. So "how much is free" means
counting the list.

Add at the end of the file:

```c
uint64
freemem(void)
{
  struct run *r;
  uint64 n = 0;

  acquire(&kmem.lock);
  for(r = kmem.freelist; r; r = r->next)
    n++;
  release(&kmem.lock);
  return n * PGSIZE;
}
```

> `acquire(&kmem.lock)` is not optional. Another CPU can be allocating while you count, and a
> linked list being modified under you is a walk into a page that is no longer a page.

### Step 10 · Declare it — `kernel/defs.h`

Find `void*           kalloc(void);` and add below it:

```c
uint64          freemem(void);
```

`defs.h` is how one kernel file reaches a function in another. Without this line `sysproc.c` cannot
call what you just wrote.

### Step 11 · Walk the process table — `kernel/sysproc.c`

Replace your `sys_sysinfo` from Step 4 with this. Add the two `extern` lines just above it:

```c
#include "sysinfo.h"

extern struct proc proc[NPROC];
extern uint ticks;

uint64
sys_sysinfo(void)
{
  uint64 addr;
  struct sysinfo info;
  struct proc *p = myproc();

  argaddr(0, &addr);

  info.nproc = info.nrunnable = info.nsleeping = info.memused = 0;
  for(struct proc *q = proc; q < &proc[NPROC]; q++){
    acquire(&q->lock);
    if(q->state != UNUSED){
      info.nproc++;
      info.memused += q->sz;
      if(q->state == RUNNABLE)
        info.nrunnable++;
      else if(q->state == SLEEPING)
        info.nsleeping++;
    }
    release(&q->lock);
  }

  info.freemem = freemem();

  acquire(&tickslock);
  info.uptime = ticks;
  release(&tickslock);

  info.load5 = info.load15 = info.load30 = 0;   // Part 3 fills these in

  if(copyout(p->pagetable, p->sz, addr, (char *)&info, sizeof(info)) < 0)
    return -1;
  return 0;
}
```

The process table is a fixed array of **64** slots. A slot is a real process when its `state` is
not `UNUSED`. One pass fills four fields — do not write four loops, because each one would take
every `p->lock` again.

### Step 12 · The line worth slowing down for

Look at the second-to-last statement:

```c
copyout(p->pagetable, p->sz, addr, (char *)&info, sizeof(info));
```

`addr` came from `argaddr`. It is a number **your program** uses as an address — and you cannot
write to it. The kernel is running on a different page table, where that same number means
something else entirely, or nothing at all. `*(struct sysinfo *)addr = info;` would compile, and
then corrupt whatever happens to live at that address in the kernel's map.

`copyout` walks the process's own page table, finds the physical page behind that address, and
writes there. It is the only correct way for a kernel to hand data back to a program.

> **Five arguments, and `p->sz` is second.** Solutions you find online show four. They are for a
> different version of xv6 and will not compile here. For a worked example already in this kernel,
> read `filestat` in `kernel/file.c` — it returns a `struct stat` to user space in exactly this way.

### Step 13 · Load, and check yourself against the kernel

Press **Load**, then at the **Keyboard**:

```
sysinfotest
```

Now the interesting part. **GINI works these numbers out a completely different way** — it keeps a
bitmap of every physical page rather than walking a free list, and it reads the process table
independently. So you have a second opinion, and the way to use it is to check **the change**, not
the number:

1. Run `sysinfotest` and write down `free bytes` and `runnable`.
2. At the **Keyboard**: `spin 30 &` three times.
3. Run `sysinfotest` again, and open the **Process Scheduler** lab.

`runnable` should have gone from 0 to 3, and the Scheduler face should agree. `free bytes` should
have dropped, and the **Virtual Memory** lab's free-page count should have dropped by the same
amount.

> **The two absolute numbers will not match exactly, and that is not a bug in either of you.** They
> count slightly different things at the edges of memory. You will be asked about this, so write
> down what you see.

---

## Part 3 · Make it remember

**About 45 minutes.** Everything so far reads the kernel at the instant you ask. Run `sysinfotest`
twice in a row and `runnable` gives two different answers, neither wrong and neither useful.

What you actually want to know is whether the machine **has been** busy. Every Unix answers that
with a load average — the run queue, averaged over the last few minutes. `uptime` on any Linux
machine prints three of them.

That needs something this kernel has not done yet: **state kept over time by something that happens
on its own, and read later by somebody else.** The timer interrupt will be the producer; your
system call will be the consumer. That split is one of the commonest shapes in an operating system,
and it is invisible from user space.

### Step 14 · Meet the heartbeat — `kernel/trap.c`

Find `clockintr()`:

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
  w_stimecmp(r_time() + 5000000);   // the next tick, about half a second away
}
```

This runs on every timer interrupt, twice a second. It is the only thing in the kernel that happens
without anybody asking, and `ticks` is the only thing it currently maintains.

Two things to notice:

- **`cpuid() == 0`.** Every CPU takes a timer interrupt; only CPU 0 keeps the clock, so the others
  do not each count the same tick. Your sampling belongs inside that test for the same reason.
- **`wakeup(&ticks)` walks the entire process table**, taking and releasing every `p->lock` as it
  goes. Open `wakeup` in `kernel/proc.c` and see. So doing that from a timer interrupt is not
  daring — it is what the line above you already does.

### Step 15 · Somewhere to keep the samples — `kernel/trap.c`

Add at the **end** of the file:

```c
// A-Lab 01: the run queue, sampled once a tick, for the last LOADN ticks.
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
No allocation, nothing to free, and it cannot run out — which is why kernels use this shape
everywhere.

It counts `RUNNABLE` **and** `RUNNING`: a process using a CPU right now is part of the load, it
simply is not queued.

### Step 16 · Average it — `kernel/trap.c`

Add underneath:

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

> **Why `x100` and not a `float`.** The kernel does not save floating-point registers when it
> switches between processes — look at `swtch.S` and count them. So a `float` in kernel code does
> not fail to compile; it quietly corrupts *another process's* arithmetic. Scaling an integer by
> 100 and dividing at the end is how kernels carry fractions. Linux does the same thing with a
> 2048x fixed point.

`have` matters: in the first thirty seconds after boot there are not thirty seconds of history, and
averaging over empty slots would report an idle machine when it is busy.

### Step 17 · Call it from the heartbeat — `kernel/trap.c`

Back in `clockintr`, inside the `cpuid() == 0` test, **after** `release(&tickslock)`:

```c
    release(&tickslock);
    load_sample();
```

After the release, not before. `wakeup` is already holding `tickslock` while it takes `p->lock`;
there is no reason for you to hold two locks when one will do.

### Step 18 · Declare them — `kernel/defs.h`

In the `// trap.c` section:

```c
#define LOADN 60
void            load_sample(void);
uint64          load_avg(int);
```

Sixty samples at half a second each is thirty seconds of history.

### Step 19 · Read it out — `kernel/sysproc.c`

Replace the placeholder line from Step 11:

```c
  info.load5  = load_avg(10);   // 10 samples = 5 seconds
  info.load15 = load_avg(30);   // 30 samples = 15 seconds
  info.load30 = load_avg(60);   // 60 samples = 30 seconds
```

The interrupt handler does the cheap part — one sample — and your system call does the arithmetic.
That is the right way round: whatever runs in an interrupt handler delays everything else on that
CPU.

### Step 20 · Watch it work

Press **Load**. Then:

```
sysinfotest
spin 40 &
spin 40 &
spin 40 &
```

and run `sysinfotest` every few seconds for half a minute. Here is what it does, measured on a real
machine:

| | 5s | 15s | 30s |
|---|---|---|---|
| idle | 0.00 | 0.04 | 0.04 |
| 12 seconds into three spinners | **3.30** | 2.80 | 1.57 |
| 26 seconds in | 3.20 | 3.06 | 3.13 |

**That separation is the whole idea.** The five-second average reacts at once; the thirty-second
average is still remembering a machine that was idle. By twenty-six seconds they agree again,
because by then the machine really has been busy for half a minute.

It is also why `uptime` prints three numbers instead of one: the *shape* of the three tells you
whether load is arriving or leaving. Wait for the spinners to exit and watch it happen in reverse.

---

## Before you answer

You should be holding six things:

1. Your call appearing **by name** in the System Calls histogram — and what `write` was doing there.
2. `runnable` moving from 0 to 3 when you started three spinners, and the Scheduler face agreeing.
3. Your `free bytes`, and GINI's free-page count, and the gap between them.
4. The reason `copyout` exists.
5. Three load averages separating and then converging.
6. At least one build that failed, and which of the twenty steps it was.

That last one counts. A step you got wrong and had to find teaches more than one that worked
first time.

## Answering

Open the **GINI Labs** tab. Your questions are waiting there. Two of them are about things you can
only have noticed while the machine was running, so answer before you tear the topology down.

---

*Every number `sysinfo` reports was computed by code you wrote, inside a kernel that was running
while you changed it. The system call you added is indistinguishable, from your program's side,
from the ones that came with the machine.*

COMP 310 / ECSE 427 · Fall 2026 · A-Lab 01

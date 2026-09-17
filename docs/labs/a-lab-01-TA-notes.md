A-LAB 01 · TA AND MARKER NOTES · NOT FOR STUDENTS

# Teaching the Kernel a New Word

*What a finished lab looks like, the question pool, and model answers*

Six questions, and every student gets all six. They are heavier than the T-Lab set because the
students have spent two hours building something rather than half an hour reading a panel, and
every question is answerable from what is on their own screen. Nothing is auto-marked.

**The one thing this assignment is for.** They arrive believing a system call is a function the
operating system provides. They should leave knowing it is a *number in a table*, that adding one
took five edits they can each account for, and that everything the kernel reports about itself is
computed on demand from structures they have now walked by hand. Everything else is scaffolding.

---

## What a finished lab looks like

| part | what they built | the observation that matters |
|---|---|---|
| **1** | five edits, call arrives | `sysinfo` appears **by name** in the System Calls histogram. `write` dwarfs it. |
| **2** | free list walked, process table walked, `copyout` | `runnable` tracks the Scheduler face exactly; `free bytes` tracks Virtual Memory's *change* but not its absolute value |
| **3** | ring buffer in `clockintr`, averaged in the syscall | 5s average leads, 30s lags, both converge after ~30s of load |

The User Code face shows 16 of 16 wired, Part A 6/6, Part B 6/6, Part C 4/4. **The checklist is a
tracker, not a grade** — every item is a pattern over their source, so it says "you wrote the line"
and never "it works". A student can be 16/16 with a kernel that does not build. Mark the behaviour,
not the ticks.

## The free-memory gap, in case you are asked

This will come up, and it is worth getting right rather than waving away.

Their `freemem` walks `kmem.freelist` — the pages the allocator could hand out. GINI counts free
pages from its own bitmap of physical memory. The two sit a few dozen pages apart, consistently.

Most of that gap **was GINI's fault** and is now fixed: the bitmap starts zeroed, `kinit` only ever
calls `kfree` on memory above the end of the kernel image, so the kernel's own pages were never
marked and read as free — about 50 pages of kernel counted as available. A student walking the free
list got the right answer and GINI did not. If a group is running an image older than 6.14.0 they
will see roughly 60 pages of disagreement; on a current image, about 10.

**The student's number is the correct one either way.** `freemem` means "what could be allocated",
and the free list is the allocator's own answer to that. Accept any student who says so.

What matters for marking is that they checked the **change** rather than the value — allocate, and
watch both move by the same amount. That is the technique, and it is the one that survives the two
measurements meaning slightly different things.

## Before the lab

- **The image must be 6.14.0 or newer.** Older images carry the old `gini_kadump` (see above) and
  an in-container agent whose compile errors are much harder to read — a missing `entry()` in
  `usys.pl` surfaces as a wall of linker command line rather than `undefined reference`.
- **Delete `~/.gini/xv6-lab/<machine>/` before a fresh start.** Seeded files are only written when
  absent, so a folder left over from a trial run keeps the old `sysinfo.h`.
- **Step 6 is where they will all stop.** Forgetting `entry("sysinfo");` compiles cleanly and fails
  at the *linker*. Students read the first error they see and go back to `syscall.c`, which is
  correct. Point them at the bottom of the compile log and at the word `undefined`.
- **Step 12 is the one to slow the room down for.** Several will try `*(struct sysinfo *)addr = info;`
  because it compiles. It does not crash immediately either — it corrupts something and the machine
  fails later, somewhere unrelated. If a group's kernel panics after `sysinfotest` runs cleanly
  once, look here first.
- **`spin 30 &` needs the ampersand.** Without it the shell waits and the run queue never grows,
  and they will conclude their `nrunnable` is broken.
- **Part 3 needs patience.** The 30-second average genuinely takes 30 seconds. A group that runs
  `sysinfotest` twice in five seconds and sees no movement has not done anything wrong.
- **Revert is per file.** A student with a kernel that will not boot should revert the file they
  touched last, not start over. Say this out loud at the start; they will not read it.

---

## The six questions

Every student gets all six. Between them they cover each part: Part 1 answers Q1 and Q6, Part 2
answers Q2, Q3 and Q4, Part 3 answers Q5.

### Q1. You made five edits before your call worked. What is each one for, and which one does not fail until the linker?

**Model answer.** The number (`syscall.h`) is what the program actually sends; the table row
(`syscall.c`) is how the kernel turns that number back into a function; the `extern` lets that file
name the function; the prototype in `user.h` lets the *program* name it; and `entry()` in `usys.pl`
generates the stub that puts the number in `a7` and executes `ecall`. The last one is the one that
survives compilation — every C file is happy, and the linker then cannot find a function that was
never generated.

**Accept:** all five with a role for each, and `usys.pl` identified as the link-time failure. The
roles matter more than the file names.

**Watch for:** describing the table row and the `extern` as the same thing. They are not — the
`extern` is a promise to the compiler, the table row is what the kernel indexes at run time. A
student who has only one of them has a call that compiles and then reports `unknown sys call 23`.

### Q2. `nproc` is not stored anywhere in the kernel. Where did your number come from, and what would it cost to keep it in a variable instead?

**Model answer.** It came from walking all 64 slots of `proc[]` and counting the ones whose state
is not `UNUSED`. Keeping it in a variable would mean updating that variable at every place a
process is created, exits, or is reaped — several sites, each needing the same lock — and any one
of them missed leaves the number permanently wrong with nothing to detect it. Walking costs 64
iterations on demand and cannot drift.

**Accept:** the walk, plus any version of "you would have to update it everywhere and one miss is
silent". Mentioning that the walk is bounded (64, not unbounded) is a bonus.

**Watch for:** "it would be faster". True and not the point — and worth asking back: faster for
whom, given nothing calls `sysinfo` in a loop?

### Q3. Why can `sys_sysinfo` not simply write through the pointer your program handed it?

**Model answer.** The pointer is a *user* virtual address. The kernel is running on a different
page table, where that number either means something else or nothing at all. Writing through it
would land on whatever the kernel has at that address. `copyout` walks the process's own page table
to find the physical page behind the address and writes there.

**Accept:** "different address spaces / different page tables" with the consequence stated. A
student who says it would corrupt kernel memory has it.

**Watch for:** "because the memory is protected" or "user memory is read-only to the kernel".
Neither is true — the kernel can write anywhere. The problem is that the *address means something
different*, not that access is denied. This distinction is the whole question.

**A trap, and it is ours rather than theirs.** `*(struct sysinfo *)addr = info;` compiles without a
warning and often appears to work once. Students who did it and got away with it will argue the
point. The honest answer is that it worked by luck.

### Q4. Your `freemem` and GINI's free-page count do not agree exactly. Which is right, and what does the disagreement tell you?

**Model answer.** Theirs. `freemem` counts the free list, which is the allocator's own answer to
"what could I hand out". GINI counts pages not marked in a bitmap, which includes memory that is
not on the free list and never will be. The disagreement is not an error in either — it is that
"free memory" is a definition, and two reasonable definitions differ at the edges. What matters is
that both *move by the same amount* when memory is allocated.

**Accept:** the student's number as correct, with any account of why two methods differ. The
strongest answers say they checked the change rather than the value.

**Watch for:** assuming GINI must be right because it is the tool. Push back — the free list is the
allocator's own bookkeeping, and nothing is closer to the truth than that.

### Q5. Part 3 put a counter in the timer interrupt and read it from a system call. Why could the system call not do the sampling itself?

**Model answer.** Because a system call only runs when somebody calls it. An average over the last
thirty seconds needs samples taken *across* those thirty seconds, whether or not anyone is asking.
The timer is the only thing in the kernel that happens on its own, so it is the only thing that can
produce that history. The system call is the consumer; it does the arithmetic when asked.

**Accept:** any version of "the syscall only runs when called, so it cannot observe time passing".

**Watch for:** "it would be too slow". Not the reason. Ask what their `sysinfo` would report about
the last thirty seconds if nobody ran it for a minute.

**The better answer, if you get it:** noticing that the split also keeps the interrupt handler
cheap — one sample per tick in the handler, the averaging deferred to the caller — because anything
slow in an interrupt handler delays everything else on that CPU.

### Q6. Run `sysinfotest` and look at the System Calls histogram. `write` is far larger than anything else. Why?

**Model answer.** `printf` is an ordinary user-space function — `user/printf.c`, about a hundred
lines — and its bottom layer is `putc`, which calls `write(fd, &c, 1)` for **one character at a
time**. Six lines of output is a few dozen `write` system calls. Nothing in the library is magic;
it sits on the same syscall path they just extended.

**Accept:** printf being a library over `write`, with the per-character detail or the observation
that the count scales with characters printed.

**Watch for:** "the kernel prints things". The *kernel* has `printk`, which writes to the console
directly and makes no system call at all — different function, same format code, completely
different path. A student who conflates the two has missed which side of the boundary they are on.

---

## If you only have time to ask one thing

Q3. Everything else in this lab is mechanism they followed; the user/kernel pointer boundary is the
idea they will need in every lab after this one, and it is the one they can most easily complete
the assignment without understanding.

---

COMP 310 / ECSE 427 · Fall 2026 · A-Lab 01 · TA Notes

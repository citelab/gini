"""The two ways the CPU changes what it's doing in xv6 — as ordered, steppable stages.

Students constantly conflate a *system call* (a TRAP: same process, user<->kernel, saves the
TRAPFRAME) with a *context switch* (swtch: a different process, kernel<->kernel, saves the
CONTEXT). And a *preemption* is both, nested. This module is the pure data behind the step-driven
"CPU journey" view: each stage says which privilege band and which process lane the CPU is in,
and which save-area is being written/read — so the difference becomes visible one step at a time.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Stage:
    title: str
    band: str        # "user" | "kernel"
    lane: str        # "A" (running proc) | "sched" | "B" (next proc)
    save: str        # "trapframe" | "context" | ""  (which save-area this stage touches)
    caption: str


# A system call: one process, dips into the kernel and back. Save unit = trapframe.
SYSCALL = [
    Stage("ecall", "user", "A", "",
          "The user program puts the syscall number in a7 and args in a0–a5, then executes "
          "`ecall` — a deliberate trap into the kernel."),
    Stage("uservec", "kernel", "A", "trapframe",
          "Hardware jumps to uservec (trampoline). It saves ALL user registers into this "
          "process's trapframe and switches to the kernel page table."),
    Stage("usertrap", "kernel", "A", "",
          "usertrap() sees scause = 8 (a syscall), advances the saved epc past the ecall, "
          "enables interrupts, and calls syscall()."),
    Stage("syscall()", "kernel", "A", "",
          "syscall() reads a7, dispatches syscalls[a7] (e.g. sys_fork), and stores the result "
          "back into the trapframe's a0."),
    Stage("userret", "kernel", "A", "trapframe",
          "userret restores the user registers FROM the trapframe, switches back to the user "
          "page table, and executes sret."),
    Stage("resume", "user", "A", "",
          "The SAME process resumes right after its ecall, with the return value in a0. No other "
          "process ran. Privilege went U → S → U."),
]

# A context switch: kernel thread of A hands the CPU to B via the scheduler. Save unit = context.
CONTEXT = [
    Stage("sched()", "kernel", "A", "",
          "Process A's kernel thread gives up the CPU (yield or sleep) and calls sched(), which "
          "calls swtch()."),
    Stage("swtch → sched", "kernel", "A", "context",
          "swtch saves A's 14 callee-saved registers into A's context and loads the scheduler's "
          "context. The CPU is now running scheduler()."),
    Stage("scheduler()", "kernel", "sched", "",
          "The per-CPU scheduler loop scans proc[] and picks the next RUNNABLE process, B, and "
          "marks it RUNNING."),
    Stage("swtch → B", "kernel", "B", "context",
          "swtch saves the scheduler's context and loads B's context. The CPU is now running B's "
          "kernel thread, exactly where B last called swtch."),
    Stage("B resumes", "kernel", "B", "",
          "B returns up through its own kernel path. A DIFFERENT process now has the CPU. "
          "Privilege stayed in S the whole time — no user/kernel crossing."),
]

# Preemption: a timer TRAP that triggers a context SWITCH — both mechanisms, nested.
PREEMPT = [
    Stage("timer trap", "kernel", "A", "trapframe",
          "A timer interrupt traps process A into the kernel — uservec saves A's trapframe. This "
          "is a TRAP, just like a syscall."),
    Stage("yield()", "kernel", "A", "",
          "usertrap sees a timer (which_dev == 2) and, once the time-slice is up, calls yield() "
          "→ sched()."),
    Stage("swtch → sched", "kernel", "A", "context",
          "swtch saves A's CONTEXT and enters the scheduler — a context switch now happens INSIDE "
          "the trap. Two different save-areas, one event."),
    Stage("swtch → B", "kernel", "B", "context",
          "The scheduler picks B and swtch loads B's context."),
    Stage("userret (B)", "kernel", "B", "trapframe",
          "B eventually returns to user via userret, restoring B's TRAPFRAME."),
    Stage("resume B", "user", "B", "",
          "A DIFFERENT process resumes in user mode. Preemption = a trap (trapframe) wrapping a "
          "context switch (context)."),
]

JOURNEYS = {"syscall": SYSCALL, "context": CONTEXT, "preempt": PREEMPT}
JOURNEY_TITLES = {
    "syscall": "System call (trap · same process)",
    "context": "Context switch (swtch · different process)",
    "preempt": "Preemption (trap + context switch)",
}

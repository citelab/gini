# GINI × xv6 Machine Lab — Nuts-and-Bolts Manual

This manual documents the GINI visual integration of xv6-riscv: what each face puts
on the screen, what the numbers mean, and how the instrumentation is bolted into the
kernel. It is written to be **indexable by GINI AI** — every page carries YAML
frontmatter and the same heading skeleton, so a retrieval system can answer
"what is this number?", "where does it come from?", and "what are its limits?"
from a single page.

Audience: instructors, TAs, and the GINI AI assistant. The **programmer's manual**
(how to extend the patch, add a dump, add a game) is a separate, future document.

## Page skeleton

Every subsystem page uses these headings, in this order:

1. **What is on the screen** — the widgets, charts, and interactions of the face.
2. **What it is doing** — the semantics: what each number/color/motion means.
3. **How it is bolted into xv6** — the kernel patch: hooks, structs, counters,
   with file anchors into `backend/xv6/gini_patch.py`.
4. **Wire format** — the exact dump record lines and who parses them.
5. **Limits and honesty** — sampling limits, observer effects, known caveats.
6. **Cross-references** — related pages by `id`.

## Ground rules that hold everywhere

- **Nothing is simulated in live mode.** Every number is read from the running
  kernel; demo mode is an explicit, user-chosen stand-in (`machine_state.py` —
  the mode never auto-falls-back).
- **Counters are cumulative since boot.** The kernel never resets them on its own;
  the frontend differences successive dumps. A missed poll costs resolution, never
  correctness.
- **The observer is visible.** GINI's own polling does real kernel work; it is
  counted separately (`gini_edge_obs`, grey dashed on the board) rather than hidden.
- **Dumps are bracketed** with 0x1e/0x1f so the agent can split machine-readable
  dumps from the human console stream.

## Index

| id | page | covers |
|----|------|--------|
| os-architecture | [os-00-architecture.md](os-00-architecture.md) | container, agent (PID 1), QEMU, ports, two read paths, real vs demo |
| os-wire-protocol | [os-01-wire-protocol.md](os-01-wire-protocol.md) | every control character, bracketing, every record format, cumulative rules |
| os-scheduler | [os-02-scheduler.md](os-02-scheduler.md) | gini_pick, policies, quantum, Gantt, controls |
| os-process-tree | [os-03-process-tree.md](os-03-process-tree.md) | proc table, sleep channels, alarms, kill/priority/tickets control plane |
| os-memory | [os-04-memory.md](os-04-memory.md) | page tables, fault ring + classification, allocator, COW/sharing |
| os-storage | [os-05-storage.md](os-05-storage.md) | superblock, buffer cache, block allocator, write-ahead log |
| os-traps | [os-06-traps.md](os-06-traps.md) | taxonomy ring, TC/TR, trap-catch, sigalarm strip |
| os-syscalls | [os-07-syscalls.md](os-07-syscalls.md) | histogram, strace ring, custom syscall numbering |
| os-kernel-board | [os-08-kernel-board.md](os-08-kernel-board.md) | residency, edges, three doors, instr/entry, trail, path trace |
| os-locks | [os-09-locks.md](os-09-locks.md) | contention telemetry, acquires vs spins |
| os-cpu | [os-10-cpu-journey.md](os-10-cpu-journey.md) | CPU & Registers face, mode bar, CSRs, CPU Journey |
| os-fingerprints | [os-11-fingerprints.md](os-11-fingerprints.md) | radar/scatter, accumulator, classifier vs grader |
| os-games | [os-12-games.md](os-12-games.md) | engine, decks, practice/graded, confusion matrix, scoring |
| os-shadows | [os-13-shadows.md](os-13-shadows.md) | all 7 shadows, dispatch, validators, wedge detection, rebuild loop |
| os-programs | [os-14-programs.md](os-14-programs.md) | the launchable workloads and what each exercises |
| os-known-issues | [os-15-known-issues.md](os-15-known-issues.md) | verified bugs and stale comments, as of 2026-08-30 |
| os-glossary | [os-16-glossary.md](os-16-glossary.md) | every GINI-coined term mapped to its textbook term ("doors" → trap classes, "shadow" → pluggable policy, …) |

## Source-of-truth map

| Layer | Where |
|-------|-------|
| Kernel patch (all instrumentation) | `backend/xv6/gini_patch.py` (literate; sections §1–§5) |
| Agent (PID 1, HTTP 5000) | `backend/xv6/gini_agent.py` |
| Container boot | `backend/xv6/boot.sh`, Dockerfile |
| Parsers / domain models | `core/src/gini/domain/xv6.py`, `xv6_vm.py`, `xv6_fs.py`, `kernel_board.py`, `os_events.py`, `xv6_runner.py`, `diagnose.py`, `fingerprint.py`, `machine_state.py` |
| Mac-side bridge | `frontend-ng/src/gini/runtime/xv6_bridge.py` |
| UI faces | `frontend-ng/src/gini/ui/machine_lab.py`, `os_hud.py`, `*_lab.py`, `process_tree.py`, `cpu_journey.py`, `diagnose_game.py`, `game_catalog.py` |

Line numbers cited in these pages are anchors as of 2026-08-30; treat them as
approximate after the next edit to the file in question.

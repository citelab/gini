---
id: os-architecture
title: Architecture — agent, QEMU, and the two read paths
subsystem: platform
layer: [container, agent, bridge, domain]
kernel_files: []
endpoints: [/health, /programs, /console, /console/stream, /source, /snapshot, /reboot, /rebuild, /revert]
keywords: [agent, PID 1, QEMU, serial, gdb, ports, real mode, demo mode, wedge, bridge, container]
---

# Architecture — agent, QEMU, and the two read paths

## What is on the screen

Nothing directly — this page is the substrate every face stands on. What the user
does see of it: the **Real/Demo** radio pair and **Reboot** button in the Machine
Lab top bar, the amber "No live data… or switch to Demo" banner, and the wedge
banner ("a hang is a property of the machine", so it sits at the Lab's top level,
not on any one face).

## What it is doing

One container per xv6 machine runs a single-file Python agent
(`backend/xv6/gini_agent.py`, stdlib `HTTPServer`) as **PID 1**. The agent owns
QEMU as a child process — a deliberate inversion (`boot.sh:4–8`): when QEMU was
PID 1 it could never be restarted, and agent-owns-QEMU is what makes the shadow
Load loop (edit → `make` → relaunch) possible without killing the container.

| Port | What | Published to host? |
|------|------|--------------------|
| 5000 | agent HTTP | yes (`compiler.py:1136`) |
| 4444 | QEMU serial, TCP | yes (host_port+1) |
| 1234 | gdb stub | container-internal only; gdb runs inside |

QEMU command (`gini_agent.py:288–296`): `qemu-system-riscv64 -machine virt -bios
none -kernel kernel/kernel -m 128M -smp $XV6_CPUS -serial tcp::4444,server,nowait
-gdb tcp::1234`. The element's Size tier sets `-smp`: S/M→1, L→2, XL→4
(`compiler.py:127–140`) — which is why lock contention is all-zero on S/M.

### The two read paths

**Path A — serial control characters (no halt).** `SerialLink` owns the single
QEMU serial socket; the human console is multiplexed through the agent because
QEMU allows one client. `_ingest` splits the byte stream at ingest into a clean
console and 0x1e/0x1f-framed dump blocks. `dump(ctrl, wait)` sends one control
byte and returns exactly that dump; the lock spans the whole exchange
(`gini_agent.py:192–210` documents the cross-delivery bug this fixed). All the
per-face GET endpoints (`/procs /vm /vmall /faults /fs /sc /traps /locks /shadows
/board`) are this path — see [os-wire-protocol](os-01-wire-protocol.md).

**Path B — gdb (halts briefly).** `gdb_run(commands)` runs `gdb-multiarch
--batch` against `localhost:1234`, 6 s timeout, and **always appends `detach`**
so a dying client cannot leave the kernel halted. `/snapshot` (registers + bt +
a gdb-python walk of `proc[]` + ticks), `/step` (`tbreak swtch; continue` — one
context switch), and `/trapcatch?kind=` (conditioned `tbreak usertrap`:
syscall→`$scause==8`, pagefault→`12|13|15`, illegal→`2`, timer/device→interrupt
codes) are this path.

### Lifecycle endpoints

- `POST /rebuild` — incremental `make kernel/kernel fs.img` (180 s timeout), then
  QEMU restart. Compile errors are scoped to lines naming the shadow file.
- `POST /revert?sub=sched|vm|fs` — restore that subsystem's pristine stub from
  `/opt/gini_*_ref.c`, then rebuild. No `sub` restores all three.
- `POST /reboot` — relaunch QEMU on the current kernel, no make. All shadows come
  back disabled (`gini_shadow[].enabled` boots 0) — the guaranteed exit from a
  wedge.
- `GET /health` — carries the wedge verdict.
- `GET /source?file=` — read-only source from the **patched** tree (this is what
  the board's double-click-to-source uses), with path-traversal defence.

### Wedge detection

Two detectors, deliberately neither reboots:

- **Hard wedge** (panic, interrupts-off loop): agent-side `Wedge`
  (`gini_agent.py:333–386`). `/procs` is the heartbeat; 10 s grace; blames
  whichever shadows were enabled and increments their `faults` count. The student
  must press Reboot.
- **Soft wedge** (a picker that loops with interrupts on — dumps still answer,
  nothing runs): frontend-side `MachineState.stall()`
  (`machine_state.py:277–307`), 8 s grace, keyed on "runnable procs exist, none
  RUNNING, Gantt gained no slots".

### Real vs demo — two planes, never blended

`MachineState` (`machine_state.py:206–342`) holds two data planes, `_real` and
`_demo`, each a `(provider, vm, fs)` trio. The mode is a **user choice**; nothing
auto-falls-back from real to demo, and switching starts a new episode so demo and
real samples never share a Gantt. Real mode with no kernel yields `None` and the
face shows the banner instead of an empty dialog. Demo stand-ins are pure and
deterministic: `DemoScheduler` (mirrors `gini_pick()`, fakes dump *text* through
the same parsers), `DemoVm` (7-leaf address space, COW pair, simulated faults),
`DemoDisk` (2000-block FS, WAL cycle). Provenance travels in the data:
`Snapshot.source`, and `have=()` lists which panels a build can actually
populate, so panels say "not available (real)" rather than borrowing demo data.

Feeds with no demo at all: the kernel board and lock contention. Games and
fingerprints fall back to canned decks instead.

## How it is bolted into xv6

The kernel side is entirely `backend/xv6/gini_patch.py` — a literate patch script
that regex-anchors insertions into a pristine xv6-riscv tree at image build time.
Sections: §1 scheduler, §2 quantum + fault/trap rings, §3 vm/bio/fs/kalloc
instrumentation + shadows, §4 dumps + console control plane + kernel board,
§5 user programs. Every anchor is `regex_once` — if an anchor misses, the build
still succeeds and the kernel falls back to stock behavior for that feature.

## Wire format

See [os-wire-protocol](os-01-wire-protocol.md) for the complete reference.

## Limits and honesty

- One serial wire for everything: user programs print to the same serial the
  agent parses. A chatty program can corrupt a dump mid-frame; a mangled process
  line drops that process from one poll's table (`machine_lab.py:41–45` — "the
  fix is a dedicated dump channel, not dropping the program").
- gdb reads halt the kernel briefly; that's why the per-face polls all use the
  serial path and gdb is reserved for Step/Snapshot/Trap-catch.
- `/run` writes into a real shell — arguments are sanitized to lowercase
  alphanumerics + spaces, 32 chars, allow-listed program names only.

## Cross-references

[os-wire-protocol](os-01-wire-protocol.md) · [os-shadows](os-13-shadows.md) ·
[os-programs](os-14-programs.md) · [os-known-issues](os-15-known-issues.md)

"""Staged checks: which stages a Check runs, in what order, and what each one's output means.

A-Lab 02 MEASURES: it runs a workload and compares numbers within a tolerance. A-Lab 03 asks
something simpler and stricter, so it gets its own small machine instead of more metrics: named
stages, each one command and one verdict, run in order, each only once every stage it needs has
passed. Written for any lab with `stages:` in its YAML, so A-Lab 04 can use it as it stands.

Three rules carry the design, and each was chosen against an alternative:

* **A stage whose prerequisite did not pass is not run at all.** Not run and marked failed, which
  is what a flat list of commands would do. A broken allocator can take the kernel down, and every
  stage after that crash would report the crash again, under nine different names. "Not run — needs
  A2" tells the student the one thing they can act on.
* **A stage that kills the kernel ends the check.** A panic, or no prompt before the timeout, fails
  that stage, and every stage not yet run is "not run — the kernel stopped during X". GINI does not
  reboot on the student's behalf: they press Load, which is how they will fix it anyway, and an
  automatic reboot would hide the very thing they need to see.
* **The verdict is read from the whole output, not from its last line.** `megatest data` prints
  `data: ok` and then `read into heap: FAIL …`; `megatest leaks` prints every other test's line
  before its own. A stage passes only with its `<key>: ok` line AND no line containing `: FAIL`.
  The match is exact and whole-line: `buddytest` prints `4K: ok` and `alloc 2M: ok`, and neither
  may count as `alloc: ok`.

Pure, like the rest of `gini.domain`. `run` takes a callable that executes one command and returns
what it printed, so the console, the agent and the container are somebody else's problem, and the
same rules can be fed a recorded transcript instead of a kernel.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

#: The seed range. Wide enough that two checks practically never repeat one, small enough to type.
SEED_MIN, SEED_MAX = 1, 999_999

OK, FAIL, NOT_RUN = "ok", "FAIL", "not run"


def new_seed(rng: random.Random | None = None) -> int:
    """A fresh seed for one Check. Recorded with the results, so any run can be repeated by hand."""
    return (rng or random).randint(SEED_MIN, SEED_MAX)


def command_for(stage, seed: int) -> str:
    """The stage's command with `{seed}` filled in. A command without `{seed}` is unchanged."""
    return str(stage.command).replace("{seed}", str(int(seed)))


def plan(spec, part: str | None = None) -> tuple:
    """The stages one Check runs, in the spec's order.

    `part` None (or "all") is Check all: every stage. A part letter is that part's stages and,
    before them, everything they need, transitively — so Check Part B runs 0, A1 and A2 first.
    Without that the dependencies would be decorative: B1 would run on a kernel nobody had checked
    could allocate a block.
    """
    stages = tuple(getattr(spec, "stages", ()) or ())
    if not part or part == "all":
        return stages
    by_id = {s.id: s for s in stages}
    want: set[str] = set()
    todo = [s.id for s in stages if s.part == part]
    while todo:
        sid = todo.pop()
        if sid in want or sid not in by_id:
            continue
        want.add(sid)
        todo.extend(by_id[sid].needs)
    return tuple(s for s in stages if s.id in want)


@dataclass(frozen=True)
class StageResult:
    """One stage of one Check: what ran, and what it means."""
    stage: object                     # lab_spec.Stage
    verdict: str                      # OK | FAIL | NOT_RUN
    summary: str = ""                 # the line that decided a FAIL, or why it was not run
    command: str = ""                 # with the seed filled in
    seed: int = 0
    lines: tuple = ()                 # what it printed (empty when not run)
    stopped: bool = False             # the kernel died during this stage

    @property
    def id(self) -> str:
        return self.stage.id

    @property
    def ok(self) -> bool | None:
        """True / False, or None for a stage that was not run — it was never measured."""
        return None if self.verdict == NOT_RUN else self.verdict == OK

    def line(self) -> str:
        """`A1  ok`, `A2  FAIL — alloc: FAIL`, `C4  not run — needs B1`."""
        return f"{self.id}  {self.verdict}" + (f" — {self.summary}" if self.summary else "")


@dataclass(frozen=True)
class StageCheck:
    """A whole Check: the seed, and one result per stage in the order they were planned."""
    seed: int
    part: str = "all"
    results: tuple = field(default_factory=tuple)

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def get(self, sid: str) -> StageResult | None:
        return next((r for r in self.results if r.id == sid), None)

    def tally(self) -> dict:
        return {"ok": sum(1 for r in self.results if r.verdict == OK),
                "failed": sum(1 for r in self.results if r.verdict == FAIL),
                "not_run": sum(1 for r in self.results if r.verdict == NOT_RUN),
                "total": len(self.results)}


def _first(lines, pred) -> str:
    return next((ln for ln in lines if pred(ln)), "")


def judge(stage, lines, returned: bool) -> tuple[str, str, bool]:
    """`(verdict, summary, kernel_stopped)` for one stage's output.

    The rule, for every stage: it returned to the prompt within its timeout; it printed every
    `expected` line exactly; no line contains anything `rejected`; and nothing printed `panic`. The
    summary of a failure is the first rejected line, else the panic line, else "timed out", else the
    first expected line that is missing (or the line that stood where it should have been).
    """
    ls = [str(ln).rstrip() for ln in (lines or ())]
    stripped = [ln.strip() for ln in ls]
    panic = _first(stripped, lambda ln: "panic" in ln)
    stopped = bool(panic) or not returned
    bad = _first(stripped, lambda ln: any(r in ln for r in stage.rejected))
    missing = [e for e in stage.expected if e not in stripped]
    if not (bad or panic or missing) and returned:
        return OK, "", False
    if bad:
        summary = bad
    elif panic:
        summary = panic
    elif not returned:
        summary = f"timed out after {int(stage.timeout)} s"
    else:
        want = missing[0]
        head = want.split(":", 1)[0] + ":" if ":" in want else ""
        summary = (_first(stripped, lambda ln: head and ln.startswith(head))
                   or f"did not print '{want}'")
    return FAIL, summary, stopped


def run(stages, execute, seed: int, part: str = "all", dead: str = "",
        say=None, dead_in: str = "boot") -> StageCheck:
    """Run `stages` in order and return the Check.

    `execute(stage, command)` runs one command and returns `(lines, returned)`: what it printed,
    and whether the prompt came back. A third item, when present, is a note that replaces the
    summary of a failure -- why the command could not even start, say. `dead`, when set, is why the kernel cannot run anything at
    all (a panic during boot): the first stage fails with it and the rest are not run, "the kernel
    stopped during <dead_in>".

    `say` hears each stage as it starts, for the live progress line.
    """
    done: dict[str, StageResult] = {}
    out: list[StageResult] = []
    stopped_in = ""
    for st in stages:
        cmd = command_for(st, seed)
        if stopped_in:
            r = StageResult(st, NOT_RUN, f"the kernel stopped during {stopped_in}", cmd, seed)
        else:
            unmet = next((n for n in st.needs
                          if n not in done or done[n].verdict != OK), None)
            if unmet is not None:
                r = StageResult(st, NOT_RUN, f"needs {unmet}", cmd, seed)
            elif dead:
                r = StageResult(st, FAIL, dead, cmd, seed, (), True)
                done[st.id] = r
                out.append(r)
                stopped_in = dead_in          # it never ran: the kernel was already gone
                continue
            else:
                if say:
                    say(f"{st.id}: {cmd}")
                res = execute(st, cmd)
                lines, returned = res[0], res[1]
                note = res[2] if len(res) > 2 else ""
                verdict, summary, stopped = judge(st, lines, returned)
                if note and verdict == FAIL:
                    summary = note            # e.g. the agent would not start it: say so
                r = StageResult(st, verdict, summary, cmd, seed, tuple(lines or ()), stopped)
            if r.stopped:
                stopped_in = st.id
        done[st.id] = r
        out.append(r)
    return StageCheck(seed=int(seed), part=part or "all", results=tuple(out))


def not_run(stages, seed: int, part: str, reason: str) -> StageCheck:
    """A Check in which nothing could run at all -- the machine was down. Every stage not run,
    with `reason`."""
    return StageCheck(seed=int(seed), part=part or "all",
                      results=tuple(StageResult(st, NOT_RUN, reason, command_for(st, seed), seed)
                                    for st in stages))


# --------------------------------------------------------------------------- #
# transcripts
# --------------------------------------------------------------------------- #
PROMPT = "$ "


def split_transcript(text: str) -> tuple[list[str], list[tuple[str, list[str], bool]]]:
    """A console transcript -> `(boot lines, [(command, lines, returned)])`.

    The shape of a recorded run: the boot, then every command after a `$ ` prompt with what it
    printed, until the next prompt. A command whose output never reached another prompt did not
    return — the kernel stopped during it. The boot "returned" only if a prompt appeared at all,
    which is what `boot_problem` reads.
    """
    body = "\n" + (text or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks = body.split("\n" + PROMPT)
    boot = chunks[0].split("\n")[1:]
    runs = []
    for i, c in enumerate(chunks[1:], start=1):
        lines = c.split("\n")
        cmd = lines[0].strip()
        if not cmd:
            continue
        returned = i < len(chunks) - 1
        runs.append((cmd, [ln for ln in lines[1:] if ln.strip()], returned))
    return boot, runs


def boot_problem(boot_lines, booted: bool) -> str:
    """Why a kernel that never reached a shell cannot run stage 0 — the panic line if there was one.
    Empty when it booted."""
    if booted:
        return ""
    panic = _first([str(ln).strip() for ln in boot_lines or ()], lambda ln: "panic" in ln)
    return panic or "the machine did not boot to a shell"


def from_transcript(spec, text: str, seed: int, part: str = "all") -> StageCheck:
    """Apply the rules to a recorded transcript instead of a running kernel. What the tests use,
    and what makes the rules checkable against what the programs really print."""
    boot, runs = split_transcript(text)
    booted = bool(runs)
    by_cmd: dict[str, tuple[list[str], bool]] = {}
    for cmd, lines, returned in runs:
        by_cmd.setdefault(cmd, (lines, returned))

    def execute(stage, cmd):
        return by_cmd.get(cmd, ([], False))

    return run(plan(spec, part), execute, seed, part, boot_problem(boot, booted))

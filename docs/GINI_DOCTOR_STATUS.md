# gini-doctor — status, and the design space

**Written 2026-09-16, against `master` at v6.14.0. This is a status report, not a design.** It
exists to be the substrate for a design session: what is built and verified, what was learned
building it, which properties are load-bearing, and which questions are genuinely open.

Where a claim here was measured, it says so. Where it is an opinion, it says that too.

---

## 1. What exists today

A POSIX shell script — **915 lines, 130 facts, 10 probe groups** — plus a 53-line Python package
that ships it and hands it to `sh`. Published on PyPI as **`gini-doctor` 6.14.0**, the fourth
distribution in this repo, with **zero dependencies**.

```
doctor/
  pyproject.toml                        gini-doctor, dependencies = []
  README.md
  src/gini_doctor/
    gini-doctor.sh                      the whole tool
    cli.py                              execv("sh", script, *argv)
    __init__.py  __main__.py
scripts/gini-doctor.sh                  wrapper, so a checkout can run it by the documented path
docs/LAB_DIAGNOSIS.md                   what a differing field means, and the fix
.github/workflows/gini-doctor.yml       publish + assert the .sh is in the wheel and deps are empty
frontend-ng/tests/test_doctor_matches_the_code.py    9 tests, see §4
```

### The probe groups

| Group | What it establishes |
|---|---|
| `system` | OS, kernel, arch, memory, disk. Always runs — a report that cannot be identified cannot be compared. |
| `engine` | podman/docker version, rootless, network backend, cgroup manager, storage driver, **and whether podman's recorded id mapping still matches `/etc/subuid`** |
| `compose` | which compose **provider** answers, and its version |
| `rootless` | subuid/subgid, user namespaces, `XDG_RUNTIME_DIR`, lingering, cgroup delegation |
| `registry` | registries.conf, stored credentials, and a real pull — retried with every credential source neutralised |
| `qt` | the interpreter that **actually runs gBuilder**, PySide6, the X libraries Qt needs |
| `gini` | installed distributions and where from, the four images, `~/.gini` |
| `live` | throwaway compose project: run, find by label, exec two ways, `ps -q`, tear down, check leftovers |
| `perf` | cpufreq governor, thermal throttle counter, cgroup cpu caps, load, a fixed benchmark ×5 (the **spread** is the signal) |
| `xv6` | boots a real kernel, times 24 polls of the agent from inside the container. Not in the default set: ~50s |

### The three modes

- **probe** — a menu at a terminal; the usual set when piped. `--only`, `--all`, `--no-run`.
- **compare** — N reports in, only the fields on which they disagree out. This is the deliverable.
- **fanout** — ssh to each host piping the script in on stdin, collect, compare. **Nothing is
  installed on the hosts.**

Report format is `key<TAB>value`, one fact per line, sorted, values flattened to a single line.
Chosen for diffability; it happens to be a serviceable wire format, which §5 depends on.

---

## 2. What it found, in practice

Not hypotheticals. Each of these came out of a real run on a real machine, in one week.

- **tr-open-12 vs tr-open-18** were identical on every system fact — podman 4.9.3, rootless,
  netavark, overlay, same graphroot, same provider, same auth files, same shared home. The only
  difference: **9 images versus 0**. The home directory is shared across the lab; the image store
  is per-machine under `/local`. So one stale credential in `~/.docker/config.json` broke pulls on
  every machine at once, and the single machine whose local store had been filled in beforehand
  never noticed and looked special when it was not.
- **Behind that, a second failure the first was hiding**: podman's storage still mapped to a
  subuid range the machine no longer had. `podman system migrate`. The range was valid; the
  storage was stale; the error message reads as neither.
- **`podman compose ps -q <svc>` returns EMPTY** on tr-open-12 for a container `podman ps` finds
  by label — the defect behind gBuilder reporting "N did not start" about a healthy lab. Observed,
  not argued from a docstring.
- **Reference baseline for the Machine Lab feed** on a developer Mac:
  `n=24 min=6 med=7 p95=9 max=354 stalls>1s=0`.

It also found four bugs in itself, all by being run rather than read: a `case` inside `$( )`, a
`grep | head || echo MISSING` that can never report MISSING, `docker exec` without `-i` silently
swallowing a heredoc, and — worst — probing `/usr/bin/python3` instead of the pipx interpreter
that actually runs gBuilder, which reported "No module named PySide6" **identically on every
machine**, so it survived comparison as background noise.

---

## 3. Properties that are load-bearing

Not preferences. Each one was paid for, and a design that breaks one should do so knowingly.

1. **It runs where GINI does not.** The machine that needs a diagnostic is by definition one where
   something is already wrong. Anything the doctor must install, import, resolve or reach first is
   a way for it to be unavailable exactly when wanted. This is why `dependencies = []`, and why
   the engine is `sh`.
2. **A single report is nearly worthless; the comparison is the product.** Every design decision
   should be read as "does this make two machines more comparable?"
3. **It must never mutate the system.** It reports `podman system migrate`; it does not run it —
   that command stops every running container, and a diagnostic that ends a student's lab to fix a
   problem they did not know they had is not a diagnostic. The one exception is its own throwaway
   compose project, which is uniquely named, never touches `gini-lab`, and is removed even when
   `down` fails.
4. **No verdict without evidence.** "FAILED" without the registry's own error was the least useful
   line in the first real report. Raw text is kept; advice is appended, never substituted.
5. **It describes THIS codebase**, and is tested against it (§4).
6. **Non-interactive paths must never block.** The menu appears only with a terminal on both ends,
   so a pipe, a cron job or `ssh host sh -s` cannot sit waiting for a keypress.

---

## 4. How it is kept honest

`test_doctor_matches_the_code.py` (9 tests) asserts the doctor against the code it mirrors: the
four image names against `BUILD_SPECS`, the three distribution names against their pyprojects,
`gbuilder` against frontend-ng's console scripts, the two compose labels against the ones
container lookups filter on, `GINI_ENGINE` against `setup/runtime.py`, the GINI home against
`app/paths.py`, and that `dependencies = []` still holds.

**This is also the standing answer to "should it be its own repository?"** It could be, and it
would then drift, because nothing would be able to run these assertions. That argument survives
into any future architecture: whatever the client is written in, something has to fail when the
thing it describes changes.

The publish workflow separately asserts the script is inside the wheel and that `Requires-Dist` is
empty.

---

## 5. The proposal on the table

From the maintainer, 2026-09-16, recorded as stated:

> A **health-lab** (doctor-center) that reports are pulled into. The doctor prefers to send;
> `--local` forces everything local. The lab is more than reporting — it learns **the baseline**,
> holds **testing regimes**, and eventually **deploys tests to the doctors**. Compose tests at the
> doctor sites in a scripting language such as **Lua**. Reuse the Teaching Center **software**,
> but a **separate instance** — this has nothing to do with teaching. Move the **client to Go**.

Settled by that message: separate instance, not a separate codebase. Health data does not enter
the teaching database.

### Why the baseline is the real prize

`--compare` today requires you to already possess a known-good machine **and know which one it
is**. On 2026-09-16 that was tr-open-12, and the reason it worked turned out to be an accident of
provisioning order. A corpus replaces "compare against the machine I trust" with "this field
differs from 28 of 30" and "your feed p95 is 40× the fleet median" — which is a different and
better question.

### Why pushing probes as data matters

The probe set changed four times in one week — the id-mapping check, the credential-neutralised
pull retry, the pipx interpreter fix, the perf group. Every change needed a new release to reach a
machine. If probes were data the lab served, they would have reached thirty machines the same day.

---

## 6. Open questions for the design session

Genuinely open. Where there is a leaning it is marked as such, not as a decision.

### Transport and consent

1. **Default-send or opt-in?** *Leaning: opt-in, and strongly.* These run on student machines at a
   university; reports carry hostname, username, home paths and installed versions. Configuration
   (`GINI_DOCTOR_LAB=…` baked into the lab image) makes it effectively default **there** while
   sending nothing anywhere else. What is the consent story, and who owns it — course, lab, or
   institution?
2. **What is in a report, and does that change when it leaves the machine?** Today it deliberately
   records credential files by size and count, never content. Does a hostname leave? A username?
3. **Retention, and who can read the corpus.**
4. **Offline and failure behaviour.** Sending must not be in the critical path (§3.1). Queue and
   retry, or fire-and-forget?

### The lab

5. **Separate instance of the TC software — how separate?** Same code, different database, different
   TLS identity, different auth secret, different port. Does it reuse `account`/`session`, or is
   auth simply a shared key? Does any teaching code need to change to support a second profile, and
   is that change welcome in `gini-teaching-center`, or does it want a new distribution that
   imports it?
6. **Schema.** `key<TAB>value` is stable and diffable but untyped and unversioned. A corpus wants
   types (numbers comparable across machines) and a schema version. Where does the schema live so
   §4's "tested against the code" still holds?
7. **What is a baseline, statistically?** Modal value per field? Per field *per machine class*? The
   lab has 30 near-identical boxes and a handful of developer laptops — a single fleet median would
   be wrong for both.

### The client in Go

8. **What does Go buy, concretely?** A static binary with no `sh`, no Python and no pipx; real
   Windows support (the current tool needs WSL or Git Bash); concurrent fanout; typed reports. All
   real.
9. **What does it cost?** Losing `curl … | sh`, which is how this reached a machine with nothing
   installed. A Go binary must also be downloaded — is that better or worse on a broken box?
   Distribution moves from PyPI to GitHub Releases (or both, with the wheel fetching a binary — at
   which point `dependencies = []` means something different). A third language in a Python-and-C
   repo, with its own toolchain, CI and release surface. **Nobody has established that the shell
   script's limits are being felt** — that case should be made before it is paid for.
10. **Does `gini-doctor` stay on PyPI?** It shipped there this week and the admin route is
    `pipx install gini-doctor`.
11. **Is it one client or two?** A Go binary for the fleet and the shell script kept as the
    zero-dependency floor is coherent — but then §4's drift argument applies to both.

### Probes as data

12. **Declarative packs or a scripting language?** *Leaning: data first.* Most probes are "run
    this, keep lines matching that, verdict on this pattern" — a table, not a language. Declarative
    packs get the "push a check to thirty machines" win with no interpreter.
13. **If Lua: what is the trust story?** Running downloaded scripts is remote code execution on
    thirty machines as the student user. Signing, pinning, a review gate — decided up front, not
    added after.
14. **Where do the "testing regimes" run?** The `live` and `xv6` groups already start containers.
    A lab-authored test that starts containers on a student's machine is a larger promise.

### Unresolved from the week's work

15. **Podman has never run a real GINI topology on the 6.14.0 code.** Verification was Docker on
    macOS plus a synthetic two-service compose project. This is not a doctor question, but it is
    the largest outstanding risk in the same area.

---

## 7. Where the rest is written

`docs/LAB_DIAGNOSIS.md` — what a differing field means and the fix, including the feed table ·
`doctor/README.md` — the user-facing description · `scripts/README.md` — the checkout wrapper ·
`frontend-ng/tests/test_doctor_matches_the_code.py` — the coupling assertions.

# gini-doctor

Answers one question: **is this machine able to run GINI, and if a machine next to it can, what
is different?**

```bash
pipx install gini-doctor      # or: pip install --user gini-doctor
gini-doctor                   # probe this machine, save a report, say what it points to
```

It looks at the container engine GINI will actually use and whether it answers, which compose
*provider* answers, the rootless prerequisites that fail late on Linux (subuid mapping,
`XDG_RUNTIME_DIR`, lingering, cgroup delegation, and whether podman's storage still matches
`/etc/subuid`), whether an image registry is reachable anonymously and through the engine, the
interpreter that really runs gBuilder and whether Qt starts in it, the GINI packages and whether
each image resolves under the name the runtime uses, and — for a Machine Lab that feels sluggish —
CPU governor, throttling and a short timing benchmark. Windows, macOS and Linux are all probed;
a fact that cannot exist on a platform is reported as `n/a`, not as missing.

After the facts it prints what the report points to: for each finding, the exact command, why,
what else it does, and which facts to re-check. **The doctor never runs any of these.**

## Comparing machines

A single report says less than a comparison. Run the doctor on a machine that works and on one that
does not, then:

```bash
gini-doctor compare working.json broken.json
```

Only the facts on which they disagree are printed, with platform-only differences kept apart.
`gini-doctor diagnose report.json` repeats the findings for a saved report, and `gini-doctor groups`
lists the probe groups (`--only engine,perf` runs just those).

Run it **as the user who runs gBuilder, not as root** — subuid mappings, `XDG_RUNTIME_DIR` and
lingering are per-user, and a root report describes a machine nobody uses.

## What it will and will not do

- It reads. The two groups that do more — `live` (a throwaway container and compose project) and
  `xv6` (boots the xv6 image for about a minute, not in the default set) — show what they will run
  and why, and run only if you answer **Y** or pass `--yes`. With no terminal to ask on, they are
  skipped. Neither ever downloads anything; they use images already on the machine.
- It never opens credential files, and usernames and home paths are reduced to `<user>` and `~` in
  every report. The hostname is kept, because that is how a report is identified.
- It works offline. `GINI_HEALTHCENTER` (or `--healthcenter`) lets it take its policy from a Health
  Center; if that cannot be reached, it uses the last policy it saw, then its built-in one.

## No dependencies, deliberately

The machine that needs a doctor is one where something is already wrong, so anything this had to
install first is a way for it to be unavailable exactly when it is wanted. It does not depend on
`gini-core`, and emphatically not on `gini-toolkit`, which carries PySide6.

The doctor is Python's standard library only and runs on Python 3.8 or newer. For a machine with
nothing installed at all, Stage 0 — a POSIX shell script and a PowerShell script — finds a usable
Python (and reports it as the problem if there is none), then fetches the doctor as one file and
runs it:

```bash
curl -fsSL https://raw.githubusercontent.com/citelab/gini/master/doctor/src/gini_doctor/stage0/stage0.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/citelab/gini/master/doctor/src/gini_doctor/stage0/stage0.ps1 | iex
```

Piped like this there is no terminal to answer Y on, so `live` is skipped; download the script and
run it (`sh stage0.sh`) to be asked.

## The legacy engine

The shell doctor that came before this one still ships while the Python doctor is checked against
it on the lab's Linux machines (it already matches on macOS). `gini-doctor legacy …` runs it, and
its multi-machine options — `--fanout`, `--compare`, `--report`, `--menu` — are passed to it
automatically. To check the two engines against each other on a machine:

```bash
gini-doctor legacy --report --only system,engine,compose,rootless,registry,qt,gini,perf > legacy.txt
gini-doctor run --yes --out new.json
gini-doctor parity legacy.txt new.json
```

## Where the rest is written up

What a differing field means and what to do about it: [`docs/LAB_DIAGNOSIS.md`](../docs/LAB_DIAGNOSIS.md).
The build order and what is left: [`docs/GINI_DOCTOR_PLAN.md`](../docs/GINI_DOCTOR_PLAN.md).

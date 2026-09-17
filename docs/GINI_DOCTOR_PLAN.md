# gini-doctor v1 — staged implementation plan

**Branch:** `feat-gini-doctor`. **Written 2026-09-17**, from the design session recorded in
`GINI_Doctor_HealthCenter_v1_Design_r3.docx` (decisions) on top of `docs/GINI_DOCTOR_STATUS.md`
(what existed at v6.14.0). This file is the build order; the design document is the why.

## The decisions this plan implements

- Python only, no Go. Two stages: **Stage 0** is shell with no Python dependency (POSIX `sh`, and
  PowerShell 5.1 on Windows) whose job is to find a usable Python; **Stage 1** is Python,
  standard library only. No new shell after Stage 0.
- Windows, Linux and macOS are equals. Probes declare their platforms; a report says
  `n/a` (not applicable here) rather than pretending a Linux-only fact is "missing" on a Mac.
- The doctor runs **fully offline**. The Health Center adds to a local run and is never required.
  Uploading an offline report is the person's explicit act, never automatic.
- **gini-healthcenter** is a separate instance derived from the Teaching Center. Two doctors meet
  there through a case code; an AI agent (a *client* of the Health Center, so the shared server
  stays model-free) orchestrates the diagnosis.
- The Health Center is trusted. The doctor still **never writes**: probes are read-only, anything
  that executes asks for Y, and fixes are shown as command + reason + side effects + how it will be
  verified, for the person to run. No sandbox in v1.
- Policy (Python floor, groups, timeouts) can be set from the Health Center, with precedence
  **live → cached → built-in**, and every report records which one it ran under.
- JSON on the wire. Hostname is sent; usernames and home paths are not; credential files are not
  read at all.
- No fanout in v1: one machine at a time.

## Stages

Each stage ends green and usable on its own. The legacy shell engine and the current
`gini-doctor` command keep working, untouched, until **S3** cuts over.

### S0 — Stage 0 bootstrap (sh + PowerShell)  ← done

- `doctor/src/gini_doctor/stage0/stage0.sh` and `stage0.ps1`.
- Enumerate candidate interpreters (override `GINI_DOCTOR_PYTHON`, the interpreter behind
  `gbuilder`, `python3`/`python`/`py`, well-known install paths), record each as a fact, choose the
  best that meets the floor.
- Floor: built-in default, or `min_python=` from the Health Center's plain-text policy endpoint
  when `GINI_HEALTHCENTER` is set and reachable (`curl`/`wget`/`Invoke-RestMethod`, short timeout).
- Never block: on macOS, `/usr/bin/python3` is not touched unless Command Line Tools are present.
- Hand off to Stage 1 with the Stage 0 findings in `GINI_DOCTOR_STAGE0`.
- **No usable Python is a report, not a crash:** Stage 0 writes the same JSON report shape with
  `stage0.*` facts and a verdict.
- Tests: `sh` under `dash`; `.ps1` under `pwsh` when available (CI: Windows PowerShell 5.1).

### S1 — Stage 1 core  ← done

- `gini_doctor/stage1/`: report model + JSON (schema `gini-doctor/1`), runner (argv lists,
  timeouts, never raises), platform detection, redaction, policy (live → cached → built-in),
  probe registry with platform declarations, compare, CLI (`run`, `compare`, `show`).
- First group ported: `system`, on all three platforms.
- Runs on Python **3.8+**; tested on 3.8 and 3.12.

### S2 — Probe parity with the shell engine  ← done (macOS); Linux lab and Windows outstanding

- All nine legacy groups ported, each declaring its platforms: `engine`, `compose`, `rootless`
  (Linux only), `registry`, `qt`, `gini`, `live`, `perf`, `xv6`. Legacy default set restored;
  `xv6` stays opt-in.
- **Consent:** `live` and `xv6` start containers, so they declare what they will run and why, and
  run only on Y at the prompt or `--yes`. With no terminal they are skipped, never assumed.
- **Read-only registry:** the legacy real pull is gone. `registry` checks Docker Hub anonymously
  over HTTPS and via `<engine> manifest inspect`, and flags a stored credential that blocks pulls
  without ever opening a credential file. `live` uses only images already present.
- **Runtime image names:** `gini.image.<name>.runtime` is a read-only `image inspect` of
  `gini-<name>:latest`, the name GINI resolves, so "listed but does not resolve" (seen on Docker
  Desktop 27.4.0) and "pulled but never tagged" are facts with remedies.
- **Local diagnosis:** `docs/LAB_DIAGNOSIS.md` is now `stage1/remedies.json` (26 rules, per-platform
  commands with facts filled in). `run` prints findings after the facts; `diagnose report.json`
  does it offline. Each finding is command + reason + side effects + what to re-check. Shown, never
  run.
- **Parity:** `parity legacy.txt new.json` maps legacy keys and spellings onto Stage 1's. First real
  machine, macOS + Docker Desktop (2026-09-17): 13 disagreements → one real gap fixed (image tags
  hidden by an alphabetical cut of six) and twelve legacy-on-macOS artefacts now classed `improved`;
  rerun: **54 agree, 0 disagree, 3 improved, 0 unmapped**.
- **Outstanding:** the same run on a lab Linux machine with rootless Podman, and on Windows; then
  the legacy half of `frontend-ng/tests/test_doctor_matches_the_code.py` goes.

### S3 — Cut over  ← first half done; removal waits for Linux parity

Done:
- `gini-doctor` runs Stage 1 in-process; `gini-doctor legacy …` runs the shell engine, and its
  `--fanout`/`--compare`/`--report`/`--menu` options are routed there automatically.
- `doctor/tools/bundle.py` builds `stage0/stage1-bundle.py`, all of Stage 1 in one file imported from
  memory (stdlib only). Stage 0 (sh and PowerShell) fetches and runs it when Stage 1 is neither
  next to it nor installed, refuses anything without the bundle marker, and removes it afterwards.
  A test and the publish workflow both fail if the committed bundle is stale.
- `requires-python = ">=3.8"`; the publish workflow checks the wheel carries Stage 0, Stage 1, the
  remedy table and the bundle, still has no dependencies, and that the installed command runs; the
  test workflow installs and runs the command on Linux, macOS and Windows at 3.8 and 3.12.
- `frontend-ng/tests/test_doctor_matches_the_code.py` holds BOTH engines to the codebase contract.
- `doctor/README.md` describes the Python doctor.

After a clean Linux parity run:
- Delete `gini-doctor.sh`, the `legacy` subcommand and `--fanout`; point `scripts/gini-doctor.sh`
  at Stage 0; update `scripts/README.md` and `docs/LAB_DIAGNOSIS.md`; drop the legacy half of the
  coupling test.

### S4 — gini-healthcenter server  ← the pure half is done; the server is next

Done — `healthcenter/src/gini_healthcenter/`, pure in the same way `core/src/gini/domain/` is (no
SQLite, no HTTP, no model), plus `healthcenter/tests/` (24 tests, no dependencies beyond pytest):

- `cases.py` — a case and the rules it will not break. Open vends the code in the same breath, so
  there is no moment at which a case exists and cannot be joined. A CLOSED case accepts nothing; a
  STALLED one accepts everything, because stalled means the agent stopped and not the machines, and
  refusing a report then would throw away the one thing a stall is promised to keep. A task names a
  participant that has already appeared, so nothing can be pushed at a machine the case never met.
  A refusal is a terminal outcome distinct from a timeout. A diagnosis with empty evidence is
  refused, and `Diagnosis` has no field that could record having acted.
- `policy.py` — one document, two renderings. `document()` validates what it builds through the
  doctor's own `policy.validate` and refuses anything that would not survive, because a field the
  doctor drops would otherwise be served, accepted and ignored on thirty machines with nothing
  saying so. A patch-level floor is refused outright: Stage 0's `sed` and PowerShell regex capture
  `major.minor`, so `3.8.1` would be enforced as `3.8` by the stage that picks an interpreter and
  recorded as `3.8.1` by the stage that does not.
- `stage1/casecode.py` — in the DOCTOR, not here. Both sides need the rule (the server mints, the
  doctor tells a typo from a wrong case before opening a connection) and only one of the two may
  not take a dependency, so the shared rule lives on the doctor's side. Crockford base32, twelve
  symbols, hash-derived check symbol, its own salt — sharing `ticket.py`'s would make a lab code
  validate as a case code, turning a local "that is not a case code" into a round trip and a
  confusing answer. `test_doctor_matches_the_code.py` holds the two formats to the same folding
  rules and to different salts.
- `test_policy_reaches_both_stages.py` runs the real `sh stage0.sh` against a real server serving
  both endpoints: Stage 0 takes the floor from `policy.txt`, hands off, and Stage 1 takes the
  version from `policy.json`. It reads the matching pattern out of `stage0.ps1` rather than
  restating it.

**The derivation decision, and what it rests on.** The Health Center takes its shared vocabulary
from **gini-doctor**, not from `gini_teaching_center` and not from `gini-core`. What settled it:

1. `accounts.Accounts(root)` constructs a `Store(root)`, and that `Store` owns the course schema.
   Reusing the Teaching Center's identity therefore means carrying `course`, `activity` and
   `material` tables into the health database — empty, but there, in the one database the design
   says must never hold the other kind of data.
2. `server.py` binds `ROOT`, `PORT`, `_ACCTS` and `_STORE` at import time from the environment, so
   two profiles cannot share a process. "A separate instance" is a separate process with a
   different environment whichever way this is packaged, so the profile option buys nothing there.
3. `certs.py` is the one module with no coupling at all — 91 lines, no `Store`, no course concepts.
   It is worth reusing, and it is reusable as it stands.
4. The doctor depends on nothing, so depending on it costs nothing and cannot cycle. It is also the
   package that already owns the report schema, the comparison and the policy validator — the three
   things this server must agree with exactly.

**Still open, and outward-facing: whether `healthcenter/` becomes a fifth published distribution.**
There is deliberately no `pyproject.toml` yet. `test_packaging.py::_distributions()` enumerates
every top-level directory that has one, so a pyproject cannot land alone: it needs a publish
workflow carrying `outdir dist/ healthcenter/`, a line in `scripts/release.sh`'s confirmation list,
and trusted publishing configured on PyPI for a new project name. Tests import through
`healthcenter/tests/conftest.py` until that is decided, the way the doctor's tests do.

Outstanding:
- `store.py` — SQLite persistence for cases, storage-shaped like the Teaching Center's, wrapping
  `cases.py` rather than re-deciding anything it settles.
- `server.py` — the endpoints: `GET /doctor/policy.txt`, `GET /doctor/policy.json`, join with a
  case code, upload a report (live or gathered offline), poll for tasks, post results.
- Staff identity and TLS for the portal (reuse `certs.py`; decide whether to reuse `accounts.py`
  given 1 above, or write the smaller thing this needs).
- Portal view of a case for a person to follow or take over.

### S5 — Doctor ↔ Health Center

- `gini-doctor run --case CODE` joins, uploads, polls for tasks; `gini-doctor upload --case CODE
  report.json` for an offline report. The code format is already in `stage1/casecode.py`, so the
  doctor can refuse a typo offline, before it opens a connection.
- Server-pushed probes: read-only by default; anything that executes shows command + reason and
  needs Y. Proposed actions displayed, never run; affected fields re-probed afterwards.
- TLS with the Health Center identity pinned.

### S6 — The troubleshooting agent

- A Health Center client: diff the reports in a case, choose probes that narrow the difference,
  post tasks, finish with a diagnosis (evidence attached) and remedies.
- Model failure stalls a case; it never loses data, and a person can continue from the portal.

## Deferred (not v1)

Fanout; sandboxed execution of server-pushed probes; signed probe packs; a full privacy review.

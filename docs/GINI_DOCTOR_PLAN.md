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

### S2 — Probe parity with the shell engine  ← built; parity on real machines outstanding

- All nine legacy groups ported, each declaring its platforms: `engine`, `compose`, `rootless`
  (Linux only), `registry`, `qt`, `gini`, `live`, `perf`, `xv6`. Legacy default set restored;
  `xv6` stays opt-in.
- **Consent:** `live` and `xv6` start containers, so they declare what they will run and why, and
  run only on Y at the prompt or `--yes`. With no terminal they are skipped, never assumed.
- **Read-only registry:** the legacy real pull is gone. `registry` checks Docker Hub anonymously
  over HTTPS and via `<engine> manifest inspect`, and flags a stored credential that blocks pulls
  without ever opening a credential file. `live` uses only images already present.
- **Local diagnosis:** `docs/LAB_DIAGNOSIS.md` is now `stage1/remedies.json` (18 rules, per-platform
  commands). `run` prints findings after the facts; `diagnose report.json` does it offline. Each
  finding is command + reason + side effects + what to re-check. Shown, never run.
- **Parity tool:** `parity legacy.txt new.json` maps legacy keys and spellings onto Stage 1's and
  lists agreements, disagreements, facts dropped on purpose, and the documented change where the
  legacy doctor probed PATH's python3 instead of gBuilder's. Sandbox run (no engine): 46 agree,
  0 disagree.
- **Outstanding before S3:** parity runs on a real macOS machine with Docker, a lab Linux machine
  with rootless Podman, and a Windows machine; then move
  `frontend-ng/tests/test_doctor_matches_the_code.py` onto the Stage 1 sources (it still guards the
  legacy script, which exists until S3).

### S3 — Cut over

- `gini-doctor` console script runs Stage 1 in-process; `curl … | sh` and `irm … | iex` run
  Stage 0, which fetches or unpacks Stage 1.
- Single-file Stage 1 build for the no-install path (so Stage 0 can hand it to Python on stdin);
  a drift test asserts the build matches the sources.
- Lower `requires-python` to the Stage 1 floor; update `gini-doctor.yml` wheel assertions; delete
  the legacy shell engine and `--fanout`; update `README.md`, `LAB_DIAGNOSIS.md`, `scripts/`.

### S4 — gini-healthcenter server

- Derived from `teaching-center/` as a separate instance (own data root, TLS identity, secrets,
  port). Decide in S4 whether that is a second profile of `gini_teaching_center` or a small
  distribution that imports it.
- Cases: open → vend code → reports attach → tasks → results → diagnosis → close.
- Policy endpoints: `GET /doctor/policy.txt` (Stage 0) and `GET /doctor/policy.json` (Stage 1).
- Upload endpoint for reports (live or saved offline); case record of every task and answer.
- Portal view of a case for a person to follow or take over.

### S5 — Doctor ↔ Health Center

- `gini-doctor run --case CODE` joins, uploads, polls for tasks; `gini-doctor upload --case CODE
  report.json` for an offline report.
- Server-pushed probes: read-only by default; anything that executes shows command + reason and
  needs Y. Proposed actions displayed, never run; affected fields re-probed afterwards.
- TLS with the Health Center identity pinned.

### S6 — The troubleshooting agent

- A Health Center client: diff the reports in a case, choose probes that narrow the difference,
  post tasks, finish with a diagnosis (evidence attached) and remedies.
- Model failure stalls a case; it never loses data, and a person can continue from the portal.

## Deferred (not v1)

Fanout; sandboxed execution of server-pushed probes; signed probe packs; a full privacy review.

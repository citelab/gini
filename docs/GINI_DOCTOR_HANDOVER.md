# gini-doctor — state of this branch, for whoever picks it up

Written 2026-09-17. Read with `docs/GINI_DOCTOR_DESIGN.md` (the decisions) and
`docs/GINI_DOCTOR_PLAN.md` (the seven stages and what each contains).

## Where the work is

This clone, on branch `feat-gini-doctor`, based on `citelab/gini` master `5ff4554`. Upstream is at
`957a964`, two commits ahead; neither touches anything under `doctor/`, and this branch has not been
merged with them yet.

**S0–S3a are committed**, one commit per stage. Nothing under `doctor/` is left in the working tree.

## Before anything else

```bash
python3 -m pytest -q doctor/tests    # 103 passed, 1 skipped on this Mac
python3 doctor/tools/bundle.py       # after ANY change under stage1/, in the same commit
```

**The test count depends on the machine, not the tree.** `test_stage0.py` parametrises five tests
over the shells that exist and `test_bundle.py` two more, so a machine with both `sh` and PowerShell
collects 111 where a Mac with only `sh` collects 104, and the floor test skips unless `python3.8` is
on PATH. Read a lower number as "fewer shells here", not as a regression.

`stage1-bundle.py` is generated and committed. A test and a CI step fail whenever it does not match
the sources.

The tests need no dependencies beyond pytest and run on Python 3.8 and 3.12. To check the floor:
`uv run --no-project --python 3.8 --with pytest python -m pytest -q doctor/tests`.

## What exists

- `doctor/src/gini_doctor/stage0/` — `stage0.sh`, `stage0.ps1`: find a usable Python, record every
  interpreter as a fact, hand off to Stage 1 (next to it, installed, or fetched as the bundle).
  A machine with no usable Python still gets a report, exit status 3.
- `doctor/src/gini_doctor/stage1/` — the doctor: `report`, `runner`, `redact`, `policy`, `compare`,
  `diagnose` + `remedies.json`, `parity`, `cli`, and `probes/` (system, engine, compose, rootless,
  registry, qt, gini, live, perf, xv6).
- `doctor/tools/bundle.py` — builds the single-file Stage 1.
- `doctor/tests/` — 111 tests. Probes are tested against a scripted machine (`fakes.FakeMachine`),
  so no Docker, Podman or Qt is needed to run them.
- `doctor/src/gini_doctor/gini-doctor.sh` — the LEGACY shell engine, untouched, still shipped.

## Known state and traps

- **The legacy engine is still there on purpose.** It goes only after a clean parity run on a Linux
  lab machine (see below). `gini-doctor legacy …` reaches it; `--fanout`/`--compare`/`--report`/
  `--menu` are routed to it automatically.
- **Parity is the gate for the rest of S3.** macOS + Docker Desktop passed on 2026-09-17:
  54 agree, 0 disagree, 3 improved, 0 unmapped. Outstanding: a lab Linux machine with rootless
  Podman, and a Windows machine. On each:
  ```bash
  gini-doctor legacy --report --only system,engine,compose,rootless,registry,qt,gini,perf > legacy.txt
  gini-doctor run --yes --out new.json
  gini-doctor parity legacy.txt new.json
  ```
  A disagreement is either a real gap in the new doctor or a missing rule in `parity.MAP`; the macOS
  run produced one of each, and `doctor/tests/test_parity_macos.py` replays it so it cannot regress.
- **CI has never run.** `.github/workflows/gini-doctor-tests.yml` (Linux/macOS/Windows × 3.8/3.12)
  and the updated publish workflow are written but unexercised; the first push will be their first
  run. Everything in them passed locally on Linux.
- **Windows is untested on real Windows.** `stage0.ps1` has only run under pwsh 7 on Linux. Windows
  PowerShell 5.1 is what students have.
- `doctor/pyproject.toml` now says `requires-python = ">=3.8"`, and the wheel carries Stage 0,
  Stage 1, the remedy table and the bundle.
- `frontend-ng/tests/test_doctor_matches_the_code.py` now holds BOTH engines to GINI's runtime
  contract. Its legacy half goes when the shell engine does.

## Next

1. Push, which is the first time either workflow runs and the first time `stage0.ps1` meets Windows
   PowerShell 5.1 (windows-latest) rather than pwsh on Linux.
2. Parity on Linux and Windows (the three commands are above).
3. The rest of S3: delete the shell engine, `legacy` and `--fanout`; point `scripts/gini-doctor.sh`
   at Stage 0; update `scripts/README.md` and `docs/LAB_DIAGNOSIS.md`.
4. S4: the Health Center server, derived from `teaching-center/`.

# gini-healthcenter

Where two doctors meet over one case.

A separate instance derived from the Teaching Center — its own data root, TLS identity, secrets and
port — because health data has nothing to do with a course and must never enter the teaching
database. It is never required: the doctor runs fully offline, and a Health Center only adds to a
local run.

**This is the pure half, and only the pure half.** No SQLite, no HTTP, no model:

| Module | What |
|---|---|
| `cases.py` | a case and the rules it will not break: open → vend code → join → reports attach → tasks → results → diagnosis → close |
| `policy.py` | one policy document in the two shapes the doctor's two stages can read |

The store, the server, the endpoints and the portal are still to come — see
[`docs/GINI_DOCTOR_PLAN.md`](../docs/GINI_DOCTOR_PLAN.md) §S4.

## It speaks the doctor's formats by importing them

Reports are read with `gini_doctor.stage1.report.Report`, compared with the doctor's own
`compare`, policy is validated with the doctor's `policy.validate`, and case codes are minted with
`gini_doctor.stage1.casecode`. The direction is deliberate: **the doctor is the side that may
depend on nothing**, so the shared vocabulary lives there and this server takes the dependency.
There is no dependency on `gini-core`, and the doctor gains none.

That is what makes `differences()` part of the case model rather than a report on the side — a
single report says much less than a pair, and the Health Center must not develop a second opinion
about what counts as a difference.

## Running the tests

```bash
python3 -m pytest -q healthcenter/tests          # 24 tests, no dependencies beyond pytest
```

`tests/conftest.py` puts this tree and the doctor's on the path, so a bare checkout works with
nothing installed. One of them runs the real `stage0.sh` against a real server on a real socket:
it is the only proof that a floor set here reaches the shell that actually chooses an interpreter.

## Not a published distribution yet

There is no `pyproject.toml` here on purpose. Whether this becomes a fifth distribution or a second
profile of `gini_teaching_center` is the decision recorded in §S4 of the plan, and it carries a
publish workflow, a line in `scripts/release.sh` and a new PyPI project with it.

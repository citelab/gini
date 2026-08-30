"""The project file format — the shape gBuilder saves a topology in.

Constants only, and here rather than in gBuilder because BOTH sides need them and only one of them
has gBuilder. The Teaching Center writes a downloaded submission in this format so a marker can
open it with no conversion step, and `gini-teaching-center` depends on `gini-core` alone — no Qt,
no toolkit, 2.3MB on a headless VM instead of 400MB. It tried to import these from
`gini.services.persistence`, which is in gini-toolkit and is therefore never installed beside it,
so downloading a submission failed on every real deployment with `No module named 'gini.services'`.
It only ever worked in a checkout that happened to have both.

The proof format lives in `gini.domain.proof` for exactly this reason: anything the two sides must
agree on belongs to the package they share, not to one of them.

`services/persistence.py` re-exports these, so `from gini.services.persistence import FORMAT` keeps
working for anything that already does it.
"""
from __future__ import annotations

PROJECT_EXT = ".gini"
FORMAT = "gini-project"
VERSION = 1

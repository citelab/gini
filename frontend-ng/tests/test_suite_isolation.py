"""The suite must not read the machine it is running on.

Two autouse fixtures in `conftest.py` exist for this — `_isolated_gini_home` and
`_pristine_fragment_registry` — and between them they still had a hole, because a fixture cannot
run before an import. `gini.domain.fragments` builds its registry at module import
(`FRAGMENTS = _load()`), which reads the user layer at `~/.gini/content/fragments`; by the time any
fixture runs, the developer's own authored packs are already in the registry, and
`_pristine_fragment_registry` then preserves them as the pristine baseline.

It cost a release: `test_catalog` failed here on a fragment the maintainer had authored months ago
and that exists nowhere in this repo, while CI — which has no `~/.gini` — was green. A suite whose
answer depends on who runs it cannot tell anyone whether a release is safe.

The guard below is the one that would have caught it, and it skips on a machine with nothing
authored, which is why CI could never have caught it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _authored_ids() -> set:
    real = Path.home() / ".gini" / "content" / "fragments"
    if not real.is_dir():
        return set()
    return {p.stem for p in list(real.glob("*.yaml")) + list(real.glob("*.yml"))}


def test_gini_home_is_never_the_developers_own():
    from gini.setup.marker import gini_home

    assert gini_home() != Path.home() / ".gini", (
        "the suite is reading the developer's real GINI home; results will differ per machine")
    assert os.environ.get("GINI_HOME_DIR"), "conftest must redirect GINI_HOME_DIR at import"


def test_no_fragment_authored_on_this_machine_reaches_the_registry():
    """The exact failure. `cap-lan`, authored here, declared `teaches: switching` — not a concept
    this build knows — and failed an assertion about the shipped catalog."""
    mine = _authored_ids()
    if not mine:
        pytest.skip("nothing authored in ~/.gini on this machine — no leak is possible to detect")

    from gini.domain import fragments as _frag

    leaked = sorted(mine & set(_frag.FRAGMENTS))
    assert not leaked, (
        f"fragments authored on this machine are in the test registry: {leaked}. "
        f"conftest redirects GINI_HOME_DIR at import for exactly this reason — something now "
        f"imports gini.domain.fragments before that runs.")

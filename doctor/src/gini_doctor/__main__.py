"""`python -m gini_doctor` — the same thing as the `gini-doctor` command, for a machine where
the console script did not land on PATH (a `pip install --user` without ~/.local/bin, say)."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

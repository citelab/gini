"""``python -m gini_doctor.stage1`` — what Stage 0 hands off to."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

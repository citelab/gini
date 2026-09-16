"""gini-doctor — is this machine able to run GINI, and how does it differ from one that can?

The package is a thin wrapper. All of the work is in `gini-doctor.sh`, which sits next to this
file and is shipped inside the wheel, because the machine that needs diagnosing is by definition
one where something does not work — and a diagnostic with an import graph is a diagnostic that
can fail for the same reason as its patient.
"""
from __future__ import annotations

from pathlib import Path

SCRIPT = Path(__file__).with_name("gini-doctor.sh")

__all__ = ["SCRIPT"]

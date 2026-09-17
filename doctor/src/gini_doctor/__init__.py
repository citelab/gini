"""gini-doctor — is this machine able to run GINI, and how does it differ from one that can?

Two stages. ``stage0/`` is the only shell: a POSIX script and a PowerShell script whose one job is
to find a Python that can run ``stage1/``, the doctor itself (standard library only, Python 3.8+).
The ``gini-doctor`` command runs Stage 1 directly, because a pipx install already has a Python.

``gini-doctor.sh`` is the legacy shell engine. It is still shipped, reachable as
``gini-doctor legacy …``, until the Python doctor has matched it on the lab's Linux machines as it
already has on macOS; then it is removed.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "gini-doctor.sh"                       # legacy engine
STAGE0_SH = HERE / "stage0" / "stage0.sh"
STAGE0_PS1 = HERE / "stage0" / "stage0.ps1"
BUNDLE = HERE / "stage0" / "stage1-bundle.py"

__all__ = ["SCRIPT", "STAGE0_SH", "STAGE0_PS1", "BUNDLE"]

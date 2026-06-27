"""gLoader -- the GINI topology loader (top-level entry point).

The implementation lives in :mod:`gini.services.gloader`; this module exposes it as a
headline GINI component (alongside gBuilder and gRouter) and provides the CLI::

    python -m gini.gloader topology.gini          # compile the spec + launch on Docker
    python -m gini.gloader topology.gini --sim     # run the in-process simulator
"""
from __future__ import annotations

from .services.gloader import GLoader, main

__all__ = ["GLoader", "main"]


if __name__ == "__main__":
    raise SystemExit(main())

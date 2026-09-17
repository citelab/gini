"""GINI Health Center — where two doctors meet over one case.

A separate instance derived from the Teaching Center, not part of it: its own data root, TLS
identity, secrets and port, because health data has nothing to do with a course and must never
enter the teaching database (design decision 14).

What is here so far is the pure half, and it is pure in the same way ``core/src/gini/domain/`` is —
no SQLite, no HTTP, no Qt, no model:

* ``cases`` — a case and the rules it will not break.
* ``policy`` — the one policy document, in the two shapes the doctor's two stages can read.

It reads and writes the doctor's own formats by importing ``gini_doctor`` rather than
re-describing them. That direction is deliberate: the doctor is the side that may depend on
nothing, so the shared vocabulary lives there and the server takes the dependency.
"""
from __future__ import annotations

__all__ = ["cases", "policy"]

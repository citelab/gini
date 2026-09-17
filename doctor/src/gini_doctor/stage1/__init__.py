"""Stage 1 of gini-doctor: the Python engine.

Stage 0 (``../stage0/``) is the only shell left in the doctor. Its one job is to find a Python
that can run THIS package. Everything that has to be correct, comparable and tested lives here.

Two rules hold across every module in this package:

* **Standard library only, Python 3.8+.** The doctor runs on machines where things are already
  broken; anything it had to install first is a way for it to be unavailable exactly when it is
  wanted. ``dependencies = []`` is enforced by the publish workflow.
* **Never raise out of a probe, never write to the machine.** A missing tool is a fact
  (``absent``); a failed command is a fact with its own error text kept (``error``); a probe that
  crashes becomes a fact saying so. The only files the doctor writes are its own report and its
  own policy cache.
"""
from __future__ import annotations

SCHEMA = "gini-doctor/1"
ENGINE_VERSION = "1.0.0.dev0"

__all__ = ["SCHEMA", "ENGINE_VERSION"]

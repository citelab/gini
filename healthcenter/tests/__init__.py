"""This file exists to keep two `conftest.py` files from shadowing each other.

`doctor/tests/test_stage0.py` and `test_bundle.py` import their helpers with `from conftest import
…`, which only works because pytest imports that file as the top-level module `conftest`. Without
this `__init__.py`, the Health Center's `conftest.py` is imported under the same name, and

    python -m pytest doctor/tests healthcenter/tests

fails to collect with `cannot import name 'find_posix_shell' from 'conftest'`, naming whichever of
the two was imported first. CI runs the two suites as separate steps and would never have seen it,
which is what makes it worth a file: it is a trap for a person, not for the build.

With this here, pytest imports the Health Center's as `tests.conftest` and the doctor's stays
`conftest`, so either order works and both suites keep their own fixtures.
"""

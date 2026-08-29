#!/usr/bin/env bash
# Set this checkout up for development, or run its tests.
#
#   ./scripts/dev.sh install      # editable installs of all three packages
#   ./scripts/dev.sh test         # the test suite
#   ./scripts/dev.sh check        # what is installed, and from where
#
# `install` uses `pip install -e`, so the installed commands run YOUR working tree — edit a file
# and the next `gini-tc` or `gbuilder` picks it up with no reinstall.
#
# Why all three together: `gini` is a namespace package split across gini-core (the domain model
# and the proof format) and gini-toolkit (the app). Installing one without the other leaves half
# the tree unimportable, and the error names a module rather than the missing install.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${PYTHON:-python3}"

case "${1:-install}" in

install)
  if [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "Note: no virtualenv is active. On a recent macOS/Linux, pip may refuse to install into"
    echo "the system Python (PEP 668). If that happens:"
    echo "    $PY -m venv .venv && source .venv/bin/activate && $0 install"
    echo
  fi
  echo "Installing gini-core, gini-toolkit and gini-teaching-center (editable)…"
  # core FIRST: the other two depend on it, and installing them first would pull the published
  # gini-core from PyPI over the top of your checkout — the exact confusion this avoids.
  $PY -m pip install -e ./core
  $PY -m pip install -e ./frontend-ng
  $PY -m pip install -e ./teaching-center
  echo
  "$0" check
  ;;

test)
  cd frontend-ng
  # Extra arguments REPLACE the default target rather than adding to it: `dev.sh test
  # tests/test_bootstrap.py` should run that file, not the whole suite plus that file.
  targets=("${@:2}")
  [ ${#targets[@]} -eq 0 ] && targets=(tests/)
  # The Qt suite is excluded by default: it needs a display, and app-level setStyleSheet re-polishes
  # every live widget, which makes it quadratic and slow. Run it explicitly when touching the UI.
  exec $PY -m pytest "${targets[@]}" -q --ignore=tests/test_qt_suite.py
  ;;

check)
  echo "Installed:"
  $PY -m pip list 2>/dev/null | grep -Ei "^gini" || echo "  (none — run: $0 install)"
  echo
  echo "Commands:"
  # Look next to the interpreter FIRST. `command -v` only sees an activated venv, so an install
  # into a venv you have not sourced would report "not on PATH" and read as a failed install.
  bindir="$(dirname "$($PY -c 'import sys; print(sys.executable)')")"
  for c in gbuilder gini-tc gini-teaching-center; do
    if [ -x "$bindir/$c" ]; then
      printf '  %-22s %s\n' "$c" "$bindir/$c"
    else
      printf '  %-22s %s\n' "$c" "$(command -v "$c" 2>/dev/null || echo 'not installed')"
    fi
  done
  echo
  echo "Imports:"
  $PY - <<'PYEOF'
mods = [("gini.domain", "gini-core"), ("gini.services.bootstrap", "gini-toolkit"),
        ("gini_teaching_center", "gini-teaching-center")]
for mod, dist in mods:
    try:
        m = __import__(mod, fromlist=["_"])
        print(f"  {mod:32} ok   ({dist})")
    except Exception as e:
        print(f"  {mod:32} FAIL {type(e).__name__}: {e}")
PYEOF
  ;;

*) echo "usage: $0 {install|test|check}" >&2; exit 2 ;;
esac

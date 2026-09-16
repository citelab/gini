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

# Which Python, decided ONCE and acted on rather than warned about.
#
# This used to print "no virtualenv is active … pip may refuse (PEP 668)" and then run the install
# anyway. It was right every time: on Debian 12, Ubuntu 24.04 and a current macOS the install dies
# with `externally-managed-environment`, and the distro's own advice in that error — "try apt
# install python3-xyz" — is wrong for this repo and sends people somewhere unhelpful. A warning
# that knows exactly what is about to happen and lets it happen is not a warning.
#
# So: an active virtualenv wins; otherwise a .venv sitting in the checkout is used, because
# somebody made it here for this; otherwise stop, with the two commands that fix it.
if [ -n "${PYTHON:-}" ]; then
  :                                         # an explicit choice is never second-guessed
elif [ -n "${VIRTUAL_ENV:-}" ]; then
  PY="$VIRTUAL_ENV/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY="$PWD/.venv/bin/python"
  echo "Using the virtualenv in this checkout: .venv"
  echo "(activate it with \`source .venv/bin/activate\` if you want gbuilder on your PATH too.)"
  echo
elif [ "${1:-install}" != "check" ]; then
  cat >&2 <<EOF
No virtualenv, and no .venv in this checkout.

Debian, Ubuntu and current macOS refuse to install into the system Python (PEP 668), and the
error they give sends you to apt for packages that are not there. Make one first:

    python3 -m venv .venv
    ./scripts/dev.sh ${1:-install}

(PYTHON=/path/to/python overrides this if you know what you want.)
EOF
  exit 1
fi

case "${1:-install}" in

install)
  echo "Installing gini-core, gini-toolkit, gini-teaching-center and gini-doctor (editable)…"
  # core FIRST: the other two depend on it, and installing them first would pull the published
  # gini-core from PyPI over the top of your checkout — the exact confusion this avoids.
  $PY -m pip install -e ./core
  $PY -m pip install -e ./frontend-ng
  $PY -m pip install -e ./teaching-center
  # Depends on nothing, so order does not matter — but it is a distribution in this checkout and
  # `dev.sh install` claiming to install them all has to mean it.
  $PY -m pip install -e ./doctor
  echo
  "$0" check
  ;;

test)
  cd frontend-ng
  # Extra arguments REPLACE the default target rather than adding to it: `dev.sh test
  # tests/test_bootstrap.py` should run that file, not the whole suite plus that file.
  targets=("${@:2}")
  # The Discord bot's tests live in bot/, outside this package, because the bot is a service run
  # from the checkout rather than a distribution (bot/README.md says why). They are listed here so
  # they run in the ordinary `dev.sh test`, rather than being the one directory nobody executes.
  [ ${#targets[@]} -eq 0 ] && targets=(tests/ ../bot/tests/ ../reason/tests/)
  # This runs EVERYTHING, Qt included. There is no separate Qt suite to exclude: an --ignore for
  # `tests/test_qt_suite.py` used to sit here, and no such file has ever existed in this repo, so
  # it excluded nothing while advertising a suite you could not run.
  #
  # Most UI tests set QT_QPA_PLATFORM=offscreen themselves at import (~90 files). Three do not and
  # build a QApplication anyway — test_board_dialog, test_flash_dialog, test_pricing — so on a
  # machine with no display, export it for the whole run:
  #     QT_QPA_PLATFORM=offscreen ./scripts/dev.sh test
  exec $PY -m pytest "${targets[@]}" -q
  ;;

check)
  echo "Installed:"
  $PY -m pip list 2>/dev/null | grep -Ei "^gini" || echo "  (none — run: $0 install)"
  echo
  echo "Commands:"
  # Look next to the interpreter FIRST. `command -v` only sees an activated venv, so an install
  # into a venv you have not sourced would report "not on PATH" and read as a failed install.
  bindir="$(dirname "$($PY -c 'import sys; print(sys.executable)')")"
  for c in gbuilder gini-tc gini-teaching-center gini-doctor; do
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

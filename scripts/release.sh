#!/usr/bin/env bash
# Cut a release. Normally you never type a version number.
#
#   ./scripts/release.sh                  # show where we are and what each bump would give
#   ./scripts/release.sh patch            # 6.1.0 -> 6.1.1   bug fixes
#   ./scripts/release.sh minor            # 6.1.0 -> 6.2.0   new features, nothing broken
#   ./scripts/release.sh major            # 6.1.0 -> 7.0.0   something changed that will break people
#   ./scripts/release.sh 6.1.0            # this exact version — see "naming one" below
#
# **Naming one.** A bump is derived from the newest git TAG, and that is only the right answer while
# the tags are ahead of every package on PyPI. They were not: gini-toolkit was published at 6.0.0
# by hand, before a tag drove it, while the repo's newest tag was v0.1.0. Bumping gave 0.2.0 — which
# uploads fine and then loses, because pip serves the HIGHEST version, so every `pipx install
# gini-toolkit` would have kept getting 6.0.0 and no further 0.x release could ever have won. Say
# the version outright when the derived one would land below what is already published; check with
#     pip index versions gini-toolkit
#
# The version is derived from the git tag by setuptools-scm, for all three packages at once —
# gini-core, gini-toolkit and gini-teaching-center share this repo and share a number. Pushing the
# tag is what publishes: the workflows in .github/workflows fire on `v*`.
#
# The guards exist because each has already cost something:
#
#   dirty tree      setuptools-scm stamps a DIRTY tree as `X.Y.Z+1.dev0` — a pre-release that plain
#                   `pip install` skips. That is how a "published" package became uninstallable.
#   existing tag    PyPI refuses to re-upload a version. Ever. Catching it here costs a second;
#                   catching it after the upload costs a version number.
#   failing tests   a release is the worst time to find out.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

last="$(git describe --tags --abbrev=0 2>/dev/null || echo "")"
if [ -z "$last" ]; then
  cur="0.0.0"
  echo "No tags yet — the first release will be 0.1.0 unless you ask for something else."
else
  cur="${last#v}"
fi
IFS='.' read -r MA MI PA <<< "${cur%%-*}"
MA=${MA:-0}; MI=${MI:-0}; PA=${PA:-0}

next() { case "$1" in
  patch) echo "$MA.$MI.$((PA + 1))" ;;
  minor) echo "$MA.$((MI + 1)).0" ;;
  major) echo "$((MA + 1)).0.0" ;;
esac; }

level="${1:-}"
if [ -z "$level" ]; then
  echo "current : ${last:-（none)}"
  echo "branch  : $BRANCH"
  echo
  echo "  patch -> $(next patch)"
  echo "  minor -> $(next minor)"
  echo "  major -> $(next major)"
  echo
  echo "usage: $0 {patch|minor|major|X.Y.Z}"
  exit 0
fi
case "$level" in
  patch|minor|major) VERSION="$(next "$level")" ;;
  # An explicit X.Y.Z, for when the derived bump is the wrong answer — see the header.
  *) if printf '%s' "$level" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
       VERSION="$level"
     else
       echo "usage: $0 {patch|minor|major|X.Y.Z}" >&2; exit 2
     fi ;;
esac
TAG="v$VERSION"

# --- guards ---------------------------------------------------------------- #
if [ -n "$(git status --porcelain)" ]; then
  echo "The working tree has uncommitted changes." >&2
  echo "setuptools-scm would stamp this ${VERSION}.dev0 — a pre-release that pip skips by default." >&2
  echo "Commit or stash first." >&2
  exit 1
fi
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "$TAG already exists. PyPI never allows a version to be reused — pick the next one up." >&2
  exit 1
fi

echo "Running the tests before tagging anything…"
if ! ( cd frontend-ng && python3 -m pytest tests/ -q -x \
        --ignore=tests/test_qt_suite.py 2>&1 | tail -5 ); then
  echo "Tests failed — nothing tagged." >&2
  exit 1
fi

# --- do it ------------------------------------------------------------------ #
cat <<INFO

  ${last:-(no tag)}  ->  $TAG      ($level)

  This tags $BRANCH and pushes. The tag push is what publishes:
    .github/workflows/gini-core.yml       -> PyPI gini-core $VERSION
    .github/workflows/gini-toolkit.yml    -> PyPI gini-toolkit $VERSION
    .github/workflows/teaching-center.yml -> PyPI gini-teaching-center $VERSION

  Container images are NOT built by this — they are slow and need both machines:
    ./scripts/images.sh build $VERSION      (on each machine)
    ./scripts/images.sh merge $VERSION      (once, after both)

INFO
read -r -p "Tag and push $TAG? [y/N] " ok
# Accept the capital too. `[ "$ok" = "y" ]` matched lowercase ONLY, so answering the [y/N] prompt
# with "Y" printed "Nothing done." after a two-minute test run — indistinguishable from a guard
# refusing the release, and the obvious next move is to look for what is wrong with the release.
# (A `case` rather than `${ok,,}`: macOS ships bash 3.2, which has no case conversion.)
case "$ok" in
  y|Y|yes|Yes|YES) ;;
  *) echo "Nothing done."; exit 0 ;;
esac

git tag -a "$TAG" -m "$TAG"
git push origin "$BRANCH"
git push origin "$TAG"

echo
echo "Pushed. Watch: https://github.com/citelab/gini/actions"
echo "Then, once PyPI has it:  pip install --upgrade gini-teaching-center"

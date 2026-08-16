#!/usr/bin/env bash
# Publish gini-toolkit to PyPI: tag -> clean build -> check -> upload -> push tag.
#
#   Usage:  scripts/release-pypi.sh <version> [--test]
#   Example: TWINE_PASSWORD=pypi-AgE... scripts/release-pypi.sh 6.0.1
#
#   Auth: set the PyPI token in the environment (never on the command line):
#           export TWINE_PASSWORD=pypi-...        # your PyPI API token
#         (TWINE_USERNAME defaults to __token__). A ~/.pypirc also works.
#   --test uploads to TestPyPI instead of PyPI.
#
# Requires: git, docker not needed, and `pipx` (used to run build+twine in isolation).
set -euo pipefail

VERSION="${1:?usage: scripts/release-pypi.sh <version> [--test]}"
REPO="pypi"
[[ "${2:-}" == "--test" ]] && REPO="testpypi"
TAG="v${VERSION}"

cd "$(dirname "$0")/.."                       # repo root

# 1. clean working tree (setuptools-scm reads git; a dirty tree taints the version)
if [[ -n "$(git status --porcelain)" ]]; then
  echo "✗ Working tree is not clean — commit or stash first."; git status --short; exit 1
fi

# 2. tag this commit (must be a NEW version — PyPI never lets you re-upload one)
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  if [[ "$(git rev-list -n1 "$TAG")" != "$(git rev-parse HEAD)" ]]; then
    echo "✗ Tag ${TAG} exists but not on HEAD. Move it deliberately, then re-run."; exit 1
  fi
  echo "• Tag ${TAG} already on HEAD — reusing."
else
  git tag "$TAG"; echo "• Tagged ${TAG}"
fi

# 3. clean build (isolated, via pipx — never pollutes your Python env)
rm -rf frontend-ng/dist frontend-ng/build
( cd frontend-ng && pipx run build )

# 4. the built version must match the tag (guards against a dirty/dev build sneaking through)
WHEEL="$(ls frontend-ng/dist/gini_toolkit-*.whl)"
echo "• Built: ${WHEEL}"
case "$WHEEL" in
  *"-${VERSION}-"*) ;;
  *) echo "✗ Built version does not match ${VERSION} (dirty tree or wrong tag?)."; exit 1;;
esac

# 5. validate metadata + README rendering, then upload
export TWINE_USERNAME="${TWINE_USERNAME:-__token__}"
pipx run twine check frontend-ng/dist/*
pipx run twine upload --repository "$REPO" frontend-ng/dist/*

# 6. record the tag upstream
git push origin "$TAG" || echo "  (couldn't push tag — push it manually: git push origin ${TAG})"

echo "✓ Published gini-toolkit ${VERSION} to ${REPO}."
[[ "$REPO" == "testpypi" ]] && echo "  Test-install: pipx install --index-url https://test.pypi.org/simple/ --pip-args='--extra-index-url https://pypi.org/simple' gini-toolkit==${VERSION}"

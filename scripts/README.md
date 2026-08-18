# Release scripts

Two scripts cut a full GINI release. A release is **one version** stamped across the PyPI wheel and
all container images, so run both with the **same version number**.

## Prerequisites (one time)

- `git`, `docker`, and **`pipx`** (`brew install pipx` or `pip install pipx`).
- A **PyPI API token** (pypi.org → Account settings → API tokens).
- A **GitHub PAT** with `write:packages` (github.com → Settings → Developer settings → Tokens (classic)).
- The GHCR org exists (e.g. `gini-toolkit`) and you can push to it.

## 1. Publish the app to PyPI

```bash
export TWINE_PASSWORD=pypi-...your-token...      # PyPI API token (username defaults to __token__)
scripts/release-pypi.sh 6.0.1                    # tag v6.0.1 -> build -> check -> upload -> push tag
scripts/release-pypi.sh 6.0.1 --test             # dry-run to TestPyPI first (recommended)
```

It refuses a dirty tree or a version/tag mismatch, so you can't accidentally publish a `.dev` build.
A PyPI version can be uploaded **only once, ever** — always bump the version.

## 2. Push the container images to GHCR

```bash
export GHCR_USER=anrl
export GHCR_TOKEN=ghp_...your-PAT...             # PAT with write:packages
scripts/push-images.sh 6.0.0                     # multi-arch (amd64+arm64) build + push, all 4 images
```

Images are built **multi-architecture** (amd64 + arm64) via `docker buildx`, so an x86 machine and an
Apple-Silicon machine both pull the right variant. Cross-building is slow (QEMU emulates the other
CPU). Override targets with `GINI_PLATFORMS=linux/arm64` if you only need one.

## 3. Make the packages public — ONE TIME

GHCR packages start **private**; `gini-setup`'s anonymous pull needs them public. This persists, so
do it once per package after the first push:

- https://github.com/orgs/gini-toolkit/packages → each package → **Package settings → Change
  visibility → Public**.

## Verify

```bash
pipx install gini-toolkit && gini-setup && gbuilder
```

## Later: CI replaces both

A GitHub Actions workflow triggered on a `v*` tag can run both automatically — PyPI via **trusted
publishing** (OIDC, no token) and GHCR via the built-in `GITHUB_TOKEN` (no PAT). These scripts are
the manual equivalent; keep them for local/offline releases.

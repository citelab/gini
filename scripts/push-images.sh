#!/usr/bin/env bash
# Build MULTI-ARCH (amd64 + arm64) GINI images and push them to GHCR, tagged with the release
# version and :latest.
#
# Why multi-arch: building on one machine (e.g. an Apple-Silicon Mac) otherwise produces an image for
# only that CPU (arm64), and other hosts (x86 Macs/Linux, Windows/WSL2) can't pull it. `docker buildx`
# cross-builds both and pushes a single manifest, so every host gets the right variant automatically.
#
#   Usage:  scripts/push-images.sh <version>
#   Example: GHCR_USER=anrl GHCR_TOKEN=ghp_... scripts/push-images.sh 6.0.0
#
#   Env:
#     GHCR_USER      your GitHub username (e.g. anrl)
#     GHCR_TOKEN     a PAT with `write:packages`
#     GINI_OWNER     registry namespace/org         (default: gini-toolkit)
#     GINI_PLATFORMS target platforms               (default: linux/amd64,linux/arm64)
#
# NOTE: cross-building via QEMU is SLOW (xv6 compiles a toolchain per arch) — expect several minutes.
#       Making the packages PUBLIC is a one-time GitHub-UI step (see scripts/README.md).
set -euo pipefail

VERSION="${1:?usage: scripts/push-images.sh <version>}"
OWNER="${GINI_OWNER:-gini-toolkit}"
PLATFORMS="${GINI_PLATFORMS:-linux/amd64,linux/arm64}"
: "${GHCR_USER:?set GHCR_USER=<github-username>}"
: "${GHCR_TOKEN:?set GHCR_TOKEN=<PAT with write:packages>}"

cd "$(dirname "$0")/.."                       # repo root

# each image: "name|build-context|dockerfile"  (empty dockerfile => <context>/Dockerfile)
SPECS=(
  "gini-xv6|backend/xv6|"
  "gini-oszoo|backend/oszoo|"
  "gini-grouter|backend|backend/grouter-build/Dockerfile"
  "gini-pox|backend/sdn|"
)

# 1. cross-arch emulation (Docker Desktop already has it; Colima usually needs it — safe to re-run)
docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null 2>&1 || true

# 2. a buildx builder that supports multi-platform
docker buildx inspect gini >/dev/null 2>&1 || docker buildx create --name gini --bootstrap >/dev/null
docker buildx use gini

# 3. auth
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

# 4. build+push each image for all platforms (buildx pushes a single multi-arch manifest)
for spec in "${SPECS[@]}"; do
  IFS='|' read -r img ctx df <<<"$spec"
  echo "• buildx ${img}  (${PLATFORMS})…"
  args=(--platform "$PLATFORMS"
        -t "ghcr.io/${OWNER}/${img}:${VERSION}"
        -t "ghcr.io/${OWNER}/${img}:latest"
        --push)
  [[ -n "$df" ]] && args+=(-f "$df")
  docker buildx build "${args[@]}" "$ctx"
done

echo "✓ Pushed multi-arch images to ghcr.io/${OWNER} at :${VERSION} and :latest."
echo "  Verify arches:  docker buildx imagetools inspect ghcr.io/${OWNER}/gini-xv6:${VERSION}"
echo "  First time only: make each package Public at https://github.com/orgs/${OWNER}/packages"

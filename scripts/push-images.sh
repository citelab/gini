#!/usr/bin/env bash
# Build + push the GINI container images to GHCR, tagged with the release version (and :latest).
#
#   Usage:  scripts/push-images.sh <version> [--no-build]
#   Example: GHCR_USER=anrl GHCR_TOKEN=ghp_... scripts/push-images.sh 6.0.0
#
#   Auth (environment, never on the command line):
#     GHCR_USER   your GitHub username           (e.g. anrl)
#     GHCR_TOKEN  a PAT with `write:packages`     (ghp_...)
#     GINI_OWNER  registry namespace/org          (default: gini-toolkit)
#
#   --no-build reuses the images already in your local Docker instead of rebuilding.
#
# NOTE: making the packages PUBLIC is a one-time step in the GitHub UI (see scripts/README.md);
#       it persists across pushes, so it is intentionally NOT done here.
set -euo pipefail

VERSION="${1:?usage: scripts/push-images.sh <version> [--no-build]}"
OWNER="${GINI_OWNER:-gini-toolkit}"
: "${GHCR_USER:?set GHCR_USER=<github-username>}"
: "${GHCR_TOKEN:?set GHCR_TOKEN=<PAT with write:packages>}"
NOBUILD=0; [[ "${2:-}" == "--no-build" ]] && NOBUILD=1

cd "$(dirname "$0")/.."                       # repo root
IMAGES=(gini-xv6 gini-oszoo gini-grouter gini-pox)

if [[ "$NOBUILD" -eq 0 ]]; then
  echo "• Building images (this rebuilds the xv6 kernel etc. — a few minutes)…"
  docker build -t gini-xv6:latest    backend/xv6
  docker build -t gini-oszoo:latest  backend/oszoo
  docker build -f backend/grouter-build/Dockerfile -t gini-grouter:latest backend
  docker build -t gini-pox:latest    backend/sdn
fi

echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

for img in "${IMAGES[@]}"; do
  for tag in "$VERSION" latest; do
    docker tag  "${img}:latest" "ghcr.io/${OWNER}/${img}:${tag}"
    docker push "ghcr.io/${OWNER}/${img}:${tag}"
  done
done

echo "✓ Pushed ${IMAGES[*]} to ghcr.io/${OWNER} at :${VERSION} and :latest."
echo "  First time only: make each package Public at https://github.com/orgs/${OWNER}/packages"

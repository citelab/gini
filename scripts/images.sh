#!/usr/bin/env bash
# Build and push the GINI container images for ONE architecture, then (optionally) merge the two
# architectures into a single multi-arch tag.
#
# The point of this dance: `docker pull ghcr.io/…/gini-xv6:6.1.0` must give arm64 on a Mac and
# amd64 on a PC, from ONE tag. That is a "manifest list", and the registry resolves it at pull
# time. gBuilder therefore contains no architecture logic at all — see
# frontend-ng/src/gini/services/bootstrap.py.
#
# Why not one machine with buildx + QEMU? Because these images compile things: the gRouter is C and
# xv6 is a RISC-V kernel. Emulated cross-builds of those are slow enough to be painful. You have
# both architectures on your desk, so build each natively and merge.
#
#   On the Apple Silicon Mac:   ./scripts/images.sh build 6.1.0
#   On the AMD64 Linux box:     ./scripts/images.sh build 6.1.0
#   On either, once both done:  ./scripts/images.sh merge 6.1.0
#
#   Only one machine to hand?   ./scripts/images.sh all 6.1.0     (QEMU, slower)
#
# Login first, on each machine:
#   echo "$GITHUB_TOKEN" | docker login ghcr.io -u <your-github-username> --password-stdin
set -euo pipefail

REGISTRY="${GINI_REGISTRY:-ghcr.io/gini-toolkit}"
IMAGES=(gini-grouter gini-pox gini-oszoo gini-xv6)
BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"

# context and dockerfile per image — mirrors BUILD_SPECS in gini/setup/images.py. If you change one,
# change the other; a source build and a release build must produce the same thing.
context_for() { case "$1" in
  gini-grouter) echo "$BACKEND|grouter-build/Dockerfile" ;;
  gini-pox)     echo "$BACKEND/sdn|Dockerfile" ;;
  gini-oszoo)   echo "$BACKEND/oszoo|Dockerfile" ;;
  gini-xv6)     echo "$BACKEND/xv6|Dockerfile" ;;
esac; }

arch_suffix() {
  case "$(uname -m)" in
    arm64|aarch64) echo "arm64" ;;
    x86_64|amd64)  echo "amd64" ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
}

cmd="${1:-}"; version="${2:-}"
[ -n "$version" ] || { echo "usage: $0 {build|merge|all} <version>   e.g. $0 build 6.1.0" >&2; exit 2; }

case "$cmd" in
build)
  a="$(arch_suffix)"
  echo "Building for linux/$a  ->  $REGISTRY/<image>:$version-$a"
  for img in "${IMAGES[@]}"; do
    IFS='|' read -r ctx dockerfile <<< "$(context_for "$img")"
    echo; echo "==> $img"
    # --platform is explicit even though we build natively: it stamps the manifest correctly, so a
    # mislabelled image cannot end up in the wrong half of the merge.
    docker build --platform "linux/$a" -t "$REGISTRY/$img:$version-$a" -f "$ctx/$dockerfile" "$ctx"
    docker push "$REGISTRY/$img:$version-$a"
  done
  echo; echo "Done for $a. Run this on the other machine too, then: $0 merge $version"
  ;;

merge)
  echo "Merging arm64 + amd64 into one tag per image"
  for img in "${IMAGES[@]}"; do
    echo "==> $img"
    # THE step that makes one tag serve both. After this, `docker pull $REGISTRY/$img:$version`
    # resolves by the puller's architecture, with no client-side choice anywhere.
    docker buildx imagetools create -t "$REGISTRY/$img:$version" \
      "$REGISTRY/$img:$version-arm64" "$REGISTRY/$img:$version-amd64"
    docker buildx imagetools create -t "$REGISTRY/$img:latest" "$REGISTRY/$img:$version"
    docker buildx imagetools inspect "$REGISTRY/$img:$version" | grep -E "Platform|Name:" | head -6
  done
  echo; echo "Merged. Verify a client sees both:"
  echo "  docker manifest inspect $REGISTRY/gini-xv6:$version | grep architecture"
  ;;

all)
  # ONE machine, both architectures, via QEMU emulation. Kept because it is the right answer in CI
  # or when you only have one machine to hand — but it is SLOW here: these images compile a C
  # router and a RISC-V toolchain, and emulating that costs minutes per image. With both machines
  # on your desk, `build` on each + `merge` is far faster.
  echo "Cross-building linux/amd64 + linux/arm64 on this machine (slow — emulation)"
  docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null 2>&1 || true
  docker buildx inspect gini >/dev/null 2>&1 || docker buildx create --name gini --bootstrap >/dev/null
  docker buildx use gini
  for img in "${IMAGES[@]}"; do
    IFS='|' read -r ctx dockerfile <<< "$(context_for "$img")"
    echo; echo "==> $img"
    docker buildx build --platform linux/amd64,linux/arm64 \
      -t "$REGISTRY/$img:$version" -t "$REGISTRY/$img:latest" \
      -f "$ctx/$dockerfile" --push "$ctx"
  done
  echo; echo "Pushed multi-arch. Verify:  docker buildx imagetools inspect $REGISTRY/gini-xv6:$version"
  ;;

*) echo "usage: $0 {build|merge|all} <version>" >&2; exit 2 ;;
esac

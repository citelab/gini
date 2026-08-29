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
#   Check what a student sees:  ./scripts/images.sh verify 6.1.0
#
#   Only one machine to hand?   ./scripts/images.sh all 6.1.0     (QEMU, slower)
#
# `verify` reads the registry ANONYMOUSLY — the same view a student's `docker pull` gets — and
# fails if any image's tag is missing an architecture. It exists because 6.0.0 was pushed
# arm64-only, and gBuilder pins images to its OWN version (setup/images.py:image_tag), so an
# Intel machine running 6.0.0 asks for a tag that has nothing for it and can never recover by
# upgrading images alone. `merge` runs it automatically, and release.sh runs it on the previous
# release before letting you cut a new one.
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
[ -n "$version" ] || { echo "usage: $0 {build|merge|verify|all} <version>   e.g. $0 build 6.1.0" >&2; exit 2; }

# Architectures a tag actually offers, read anonymously straight from the registry. Anonymous is
# the point: a `docker login` on this machine can make a private package look fine while every
# student gets "denied". Prints e.g. "amd64 arm64", or nothing if the tag is absent.
ACCEPT='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'
platforms_for() {
  local img="$1" tag="$2" ns="${REGISTRY#ghcr.io/}" tok
  # `|| true` inside the substitution on purpose: `set -e` would otherwise kill the script here,
  # and an unreachable registry must return 2 so the caller can warn rather than fail a release.
  tok="$(curl -fsS --max-time 20 "https://ghcr.io/token?scope=repository:$ns/$img:pull&service=ghcr.io" \
         2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
  [ -n "$tok" ] || return 2                       # registry unreachable — caller decides
  # No `-f` here, deliberately. With it, a 404 (tag absent) exits non-zero, `pipefail` propagates
  # that, and an ABSENT TAG gets misreported as an unreachable registry — which this function
  # treats as "proves nothing" and passes. A missing tag must read as "no architectures", not as
  # a skipped check.
  curl -sS --max-time 30 -H "Authorization: Bearer $tok" -H "Accept: $ACCEPT" \
       "https://ghcr.io/v2/$ns/$img/manifests/$tag" 2>/dev/null | python3 -c '
import json, sys
try:
    m = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)                           # no such tag -> no architectures
# "unknown" entries are buildx attestation manifests, not something anyone can run.
print(" ".join(sorted({e.get("platform", {}).get("architecture", "")
                       for e in m.get("manifests", [])} - {"unknown", ""})))
'
}

# 0 = every image serves both architectures, 1 = something is missing, 3 = could not check.
# 3 is distinct on purpose: "I could not look" must never print as "I looked and it is fine".
verify_version() {
  local ver="$1" bad=0 unreachable=0 plats
  case "$REGISTRY" in
    ghcr.io/*) ;;
    *) echo "  (verify understands ghcr.io only; REGISTRY=$REGISTRY)"; return 3 ;;
  esac
  for img in "${IMAGES[@]}"; do
    plats="$(platforms_for "$img" "$ver")" || { unreachable=1; plats=""; }
    if [ $unreachable -eq 1 ]; then
      printf '  %-14s %s\n' "$img" "could not read the registry"
      continue
    fi
    if [ -z "$plats" ]; then
      printf '  %-14s %s\n' "$img" "NOT PUBLISHED at this tag"; bad=1; continue
    fi
    case " $plats " in
      *" amd64 "*) case " $plats " in
                     *" arm64 "*) printf '  %-14s %s\n' "$img" "$plats" ;;
                     *)           printf '  %-14s %s\n' "$img" "$plats  <-- no arm64"; bad=1 ;;
                   esac ;;
      *) printf '  %-14s %s\n' "$img" "$plats  <-- no amd64"; bad=1 ;;
    esac
  done
  [ $unreachable -eq 1 ] && return 3
  return $bad
}

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
  echo; echo "Checking what an anonymous client actually sees:"
  verify_version "$version" && rc=0 || rc=$?
  case $rc in
    0) echo "All images serve both architectures." ;;
    3) echo "Could not check the registry — verify by hand: $0 verify $version" >&2 ;;
    *) echo "MERGE INCOMPLETE — see above. Build the missing architecture and merge again." >&2
       exit 1 ;;
  esac
  ;;

verify)
  echo "$REGISTRY/<image>:$version as an anonymous client sees it"
  verify_version "$version" && rc=0 || rc=$?
  case $rc in
    0) echo "OK: every image serves both architectures." ;;
    3) echo; echo "Could not read the registry, so this proves NOTHING — not an OK." >&2; exit 0 ;;
    *) echo; echo "Incomplete: an install on the missing architecture cannot pull these." >&2
       exit 1 ;;
  esac
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

*) echo "usage: $0 {build|merge|verify|all} <version>" >&2; exit 2 ;;
esac

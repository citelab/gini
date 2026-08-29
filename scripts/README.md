# scripts

Three scripts. Everything you do repeatedly should be one of them.

| | |
|---|---|
| `./scripts/dev.sh install` | editable installs of all three packages — the app runs your working tree |
| `./scripts/dev.sh test` | the test suite |
| `./scripts/dev.sh check` | what is installed, and from where |
| `./scripts/release.sh` | show the current version and what each bump would give |
| `./scripts/release.sh patch\|minor\|major` | tag and push — **this is what publishes to PyPI** |
| `./scripts/images.sh build <version>` | build + push this machine's architecture to GHCR |
| `./scripts/images.sh merge <version>` | merge both architectures into one tag |
| `./scripts/images.sh all <version>` | both architectures on one machine, via QEMU (slow) |

`../commit.sh "message"` still commits and pushes in one go.

---

## Versions are automatic

You never type a version number. `setuptools-scm` derives it from the git tag, so one tag versions
**all three packages at once** — `gini-core`, `gini-toolkit` and `gini-teaching-center` live in this
repo and share a number.

```
./scripts/release.sh              # current: v0.1.0 → patch 0.1.1 · minor 0.2.0 · major 1.0.0
./scripts/release.sh minor        # runs the tests, tags v0.2.0, pushes
```

Pushing the tag is what publishes: the workflows in `.github/workflows/` fire on `v*` and upload to
PyPI via trusted publishing, so there is no API token anywhere.

Three guards, each of which has already cost something here:

* **A dirty tree is refused.** setuptools-scm stamps uncommitted changes as `X.Y.Z+1.dev0` — a
  *pre-release*, which plain `pip install` skips. That is how a package gets published and is then
  uninstallable.
* **An existing tag is refused.** PyPI never lets a version be reused. Catching it before the
  upload costs a second; catching it after costs a version number.
* **The tests run first.** A release is the worst moment to discover a failure.

---

## Container images

Images are **not** built by `release.sh` — they are slow and want both machines. They are also the
one place where architecture matters, and the answer is that it matters *in the registry*, not in
the client:

```
docker pull ghcr.io/gini-toolkit/gini-xv6:6.1.0
```

resolves to arm64 on a Mac and amd64 on a PC, from a single tag, because `merge` publishes a
**manifest list**. gBuilder therefore contains no architecture logic at all — a client-side choice
could be wrong, and wrong would mean a confident pull of binaries that will not run.
`test_bootstrap.py` asserts an image reference never names an architecture, so nobody re-adds
`-arm64` suffixes later.

The usual flow, with both machines:

```
# Apple Silicon Mac
./scripts/images.sh build 6.1.0        # pushes :6.1.0-arm64

# AMD64 Linux box
./scripts/images.sh build 6.1.0        # pushes :6.1.0-amd64

# either machine, once both are done
./scripts/images.sh merge 6.1.0        # -> :6.1.0 and :latest, multi-arch
```

Native builds because these images compile a C router and a RISC-V toolchain; emulated cross-builds
of that are slow enough to be painful. `all` does it on one machine via QEMU when that is the only
option — in CI, or when you are away from one of the two.

Log in on each machine first:

```
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
```

First time only, make each package public at `https://github.com/orgs/gini-toolkit/packages`.

---

## What was here before

* `push-images.sh` — folded into `images.sh all`. Same QEMU cross-build, one fewer script.
* `release-images.sh` — renamed to `images.sh`.
* `release-pypi.sh` — deleted. It published one package, took a hand-typed version and needed a
  PyPI token in the environment. All three of those are now handled by a tag plus trusted
  publishing.

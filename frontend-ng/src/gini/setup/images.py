"""Pull the custom GINI images from the registry, pinned to the app version — or build them
locally from a source checkout (`gini-setup --build`, for source-based installs)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import IMAGES, REGISTRY


def image_tag(version: str | None) -> str:
    """A released version (e.g. 6.1.0) pins images to that tag; a dev/unreleased build uses 'latest'
    (there's no matching published tag yet)."""
    if not version:
        return "latest"
    if "dev" in version or "+" in version or version.startswith("0"):
        return "latest"
    return version


def image_refs(version: str | None) -> list[str]:
    tag = image_tag(version)
    return [f"{REGISTRY}/{name}:{tag}" for name in IMAGES]


def local_name(ref: str) -> str:
    """The plain name the runtime resolves, derived from a registry reference.

    `ghcr.io/gini-toolkit/gini-grouter:6.1.0` -> `gini-grouter:latest`
    """
    return ref.rsplit("/", 1)[-1].split(":", 1)[0] + ":latest"


def repair_tag(name: str, run=None) -> bool:
    """Re-apply a tag Docker has stopped resolving. True when the image is really here.

    Docker's name index can lose a tag the image itself still claims. Seen on Docker Desktop 27.4.0
    straight after `docker pull` + `docker tag` of an image already present under another tag —
    which is precisely what `pull_images` does on every upgrade:

        docker images gini-grouter        -> gini-grouter latest 57b66b555d00 154MB
        docker image inspect gini-grouter -> No such image: gini-grouter
        docker image inspect 57b66b555d00 -> RepoTags: [gini-grouter:latest, ...]

    Three answers about one image, and the wrong one is the one everything asks. It cost twice:
    Run refused to start a topology, and `missing_locally` — which asks the same question — decided
    the images were gone and re-downloaded all four on every launch.

    The repair is CONFIRMED, never assumed. A `docker tag` that returns 0 without fixing the index
    would otherwise turn a visible failure into a download that changes nothing, or a Run that
    fails deeper in where the message is far less legible.
    """
    # Resolved HERE rather than as a default argument. `run=subprocess.run` in the signature binds
    # at import, so a caller that patches this module's `subprocess` — which is how anything tests
    # a docker interaction — would be silently ignored and reach the real daemon instead.
    run = run or subprocess.run
    want = name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"
    try:
        listed = run(["docker", "images", "--no-trunc", "--format",
                      "{{.ID}} {{.Repository}}:{{.Tag}}"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     timeout=60)
        if listed.returncode != 0:
            return False
        for line in (listed.stdout or "").splitlines():
            image_id, _, ref = line.strip().partition(" ")
            if ref != want or not image_id:
                continue
            if run(["docker", "tag", image_id, want], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60).returncode != 0:
                return False
            return run(["docker", "image", "inspect", "--format", "{{.Id}}", want],
                       capture_output=True, timeout=60).returncode == 0
    except Exception:                          # noqa: BLE001 — a repair must not raise
        return False
    return False


def missing_locally(refs, run=subprocess.run) -> list[str]:
    """Which of these images are NOT on this machine, by the name the runtime resolves.

    The setup marker records what a pull REPORTED, and that is not the same as what Docker holds.
    Both failures this project has shipped wrote a marker saying the machine was ready over a
    machine that could not start anything — 6.0.0 pulled arm64-only images on an Intel Mac, and
    6.1.0 pulled successfully but left nothing under the names the runtime resolves — and nothing
    ever re-checked, so gBuilder reported READY for ever and the panel never came back.

    A marker is a cache. This is its invalidation: cheap, local, and the same question the
    orchestrator will ask at Run time.

    An unreachable Docker returns [] rather than "everything is missing": that machine is already
    heading for NEEDS_RUNTIME, and guessing here would put a download in front of somebody whose
    engine is simply stopped.
    """
    names = [local_name(r) for r in refs]
    if not names:
        return []
    try:
        r = run(["docker", "image", "inspect", "--format", "{{.Id}}", *names],
                capture_output=True, timeout=30)
        if r.returncode == 0:
            return []                      # one call, and the common case answers here
    except Exception:                      # noqa: BLE001 — cannot ask; claim nothing
        return []
    missing = []
    for ref, name in zip(refs, names):     # only when something IS missing, to say which
        try:
            one = run(["docker", "image", "inspect", "--format", "{{.Id}}", name],
                      capture_output=True, timeout=15)
            # Before believing it is gone, check it is not merely UNNAMED. A tag Docker has lost
            # track of looks identical to an absent image here, and the difference is a
            # four-image download on every single launch versus a `docker tag` that takes no time
            # at all. See `repair_tag`.
            if one.returncode != 0 and not repair_tag(name, run=run):
                missing.append(ref)
        except Exception:                  # noqa: BLE001
            missing.append(ref)
    return missing


#: A layer's line in `docker pull` output: "5711127a7748: Pull complete".
_LAYER = re.compile(r"^([0-9a-f]{8,}): (.+?)\s*$")


class PullProgress:
    """How far a `docker pull` has got, from the only signal Docker actually gives.

    There is no percentage. `docker pull` into a PIPE emits no byte counts at all — the
    "Downloading [===>   ] 12MB/50MB" redraws are a TTY affectation, and a 20 MB image with seven
    layers produced none of them. That is what the progress bar's old "docker gives us no usable
    percentage" was reacting to, and it was half right: there is no percentage, but there IS a
    count. Each layer announces itself and then reports finishing:

        7x  Pulling fs layer      <- the denominator, announced up front
        7x  Verifying Checksum
        7x  Download complete
        7x  Pull complete         <- the numerator
        1x  Already exists        <- a layer shared with an image already here: done on arrival

    So the bar moves in layer-sized steps rather than smoothly, which is honest — the wait really
    is lumpy — and it moves, which the indeterminate one never did.
    """

    def __init__(self) -> None:
        self.total = 0
        self.done = 0

    def feed(self, line: str) -> bool:
        """Take one line; True when the counts moved and the caller should redraw."""
        m = _LAYER.match((line or "").strip())
        if not m:
            return False
        what = m.group(2).lower()
        if what.startswith("pulling fs layer"):
            self.total += 1
            return True
        if what.startswith("already exists"):
            # Already on the machine, shared with another image. It counts on BOTH sides, or a
            # pull that is mostly cached would report a total it never reaches.
            self.total += 1
            self.done += 1
            return True
        if what.startswith("pull complete"):
            self.done += 1
            return True
        return False

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total else 0.0


def pull_one(ref: str, on_progress=None, run=None) -> bool:
    """Pull a single ref, reporting layer progress as it goes. True when it landed.

    Streamed rather than waited on, because a pull is minutes long and the whole point is to say
    something during it. Falls back to `run` (the plain, waited form) when no progress is wanted,
    which is what keeps `pull_images` testable with a fake that never spawns anything.
    """
    if on_progress is None:
        return (run or subprocess.run)(["docker", "pull", ref], timeout=1800).returncode == 0
    seen = PullProgress()
    try:
        proc = subprocess.Popen(["docker", "pull", ref], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
    except Exception:                          # noqa: BLE001 — no docker, no pull
        return False
    try:
        for line in proc.stdout:               # universal newlines: CRLF is already \n here
            if seen.feed(line):
                on_progress(seen.done, seen.total)
        return proc.wait(timeout=1800) == 0
    except Exception:                          # noqa: BLE001
        proc.kill()
        return False


def pull_images(refs, run=subprocess.run, on_progress=None) -> list[tuple[str, bool]]:
    """Pull each ref AND give it the plain local name; return [(ref, ok)].

    The tag is not cosmetic, and leaving it out made every pip install unable to Run anything.
    `docker pull` leaves an image under its REGISTRY reference, but nothing looks for it there:
    the orchestrator inspects `gini-grouter` and writes `image: gini-grouter` into the compose
    file (services/orchestrator.py), and the compiler writes `gini-xv6:latest` and
    `gini-oszoo:latest` (services/compiler.py). So a pull that reported success left nothing
    under the names anything runs, and the failure surfaced much later and somewhere else, as
    "the gRouter image isn't built yet" — pointing at a `backend/` that a wheel does not contain.

    A source build already produces exactly these names (BUILD_SPECS below, whose comment promises
    "a source build drops in exactly where a registry pull would"). This is the half that makes
    that true.

    A failed tag fails the image on purpose: a pulled-but-unreachable image is worse than an
    honest failure, because setup would write a marker saying this machine is ready.
    """
    out = []
    for ref in refs:
        try:
            ok = pull_one(ref, on_progress=on_progress, run=run)
            if ok:
                t = run(["docker", "tag", ref, local_name(ref)], timeout=60)
                ok = t.returncode == 0
            out.append((ref, ok))
        except Exception:
            out.append((ref, False))
    return out


# -- source builds (`gini-setup --build`) ---------------------------------------------------------#
# Each image's (build context, dockerfile) relative to the backend/ source tree. The tags are the
# plain local names the orchestrator resolves by default (gini-grouter, gini-pox, …), so a source
# build drops in exactly where a registry pull would.
BUILD_SPECS: dict[str, tuple[str, str]] = {
    # image name      context (rel.)  dockerfile (rel. to context)
    "gini-grouter": (".",       "grouter-build/Dockerfile"),  # context MUST be backend/ (COPY include/, src/)
    "gini-pox":     ("sdn",     "Dockerfile"),
    "gini-oszoo":   ("oszoo",   "Dockerfile"),
    "gini-xv6":     ("xv6",     "Dockerfile"),
}


def find_backend(hint: str | None = None) -> Path | None:
    """Locate the backend/ source tree for a source-based install.

    Tries, in order: an explicit --source path, $GINI_BACKEND, the repo-sibling layout of an
    editable install (src/gini -> frontend-ng -> repo root -> backend/), and ./backend or . from
    the current directory. A directory qualifies if the gRouter Dockerfile is in it."""
    import os
    cands: list[Path] = []
    if hint:
        cands.append(Path(hint).expanduser())
    if os.environ.get("GINI_BACKEND"):
        cands.append(Path(os.environ["GINI_BACKEND"]).expanduser())
    pkg = Path(__file__).resolve()
    if len(pkg.parents) > 4:                     # setup/ -> gini/ -> src/ -> frontend-ng/ -> repo root
        cands.append(pkg.parents[4] / "backend")
    cands.append(Path.cwd() / "backend")
    cands.append(Path.cwd())
    for c in cands:
        if (c / "grouter-build" / "Dockerfile").is_file():
            return c
    return None


def build_images(backend: Path, names=None, run=subprocess.run) -> list[tuple[str, bool]]:
    """Build each image from the backend tree with its orchestrator-expected local tag.
    Returns [(name, ok)]; a failure is captured, not raised, so the rest still build."""
    out = []
    for name, (ctx_rel, dockerfile) in BUILD_SPECS.items():
        if names and name not in names:
            continue
        ctx = backend / ctx_rel
        try:
            r = run(["docker", "build", "-f", str(ctx / dockerfile), "-t", name, str(ctx)],
                    timeout=3600)
            out.append((name, r.returncode == 0))
        except Exception:
            out.append((name, False))
    return out

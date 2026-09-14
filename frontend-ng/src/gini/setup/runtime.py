"""Container-runtime detection + per-OS install guidance.

The runtime can't be bundled, and it differs per OS (Colima is macOS/Linux only; Windows uses Docker
Desktop/Podman). We detect a working Docker-compatible CLI and, where we can, offer the auto-install
commands — but only after the user consents (we never silently run privileged installers).

Podman compatibility
--------------------
GINI talks to the container engine ONLY through the CLI — never through a socket, never through a
Python SDK. That makes engine support a pure string question: which binary sits at the front of
every ``subprocess.run`` call. The answer is resolved ONCE by ``detect_engine``, cached in the
module, and consumed everywhere through ``engine_cli()`` and ``compose_cli()``. No abstraction
layer, no strategy pattern — just a prefix list that is ``["docker"]`` on one machine and
``["podman"]`` on another.

Detection priority
------------------
The engine is chosen in this order:
1. ``GINI_ENGINE`` environment variable (highest priority, for development/testing)
2. Settings preference from ``~/.gini/config.json`` (user's chosen engine in the UI)
3. Auto-detection: Docker first, then Podman (for Podman-only lab machines)

The auto-detection order is deliberate: Docker first, because it is the documented path for every
platform except the Trottier lab machines and similar Podman-only environments. When a machine has
BOTH, Docker wins — Podman's ``docker`` compatibility shim (``podman-docker``) makes this the same
either way, and if they genuinely coexist (rare), Docker is the one with known-good compose
support.

Rootless Podman
---------------
For campus environments where users lack sudo access, rootless Podman is the recommended choice.
The ``_is_rootless_podman()`` function detects whether Podman is running in rootless mode by
checking ``podman info --format {{.Host.Security.Rootless}}``. Users can set their preferred
engine in Settings → Networking → Container engine, choosing between Auto-detect, Docker, or Podman.
"""
from __future__ import annotations

import os
import platform
import subprocess

# ---------------------------------------------------------------------------
# Engine detection — which container CLI does this machine actually have?
# ---------------------------------------------------------------------------
# Cached after the first call.  Every call site reads the result through
# ``engine_cli()`` / ``compose_cli()`` rather than re-probing, so a detection
# that contacts the daemon once at startup is tolerable and a detection that
# contacts it once per compose call would not be.
_ENGINE: str | None = None      # "docker" | "podman" | "missing"


def _probe_binary(binary: str, run) -> str | None:
    """``"ok"``, ``"stopped"``, or ``None`` if ``binary`` is not on PATH.

    ``None`` is FileNotFoundError only. A non-zero ``info`` (daemon down) is ``stopped``: the
    binary is here, and falling through to the other engine would send a student with a stopped
    Docker off to install Podman.
    """
    try:
        r = run([binary, "info"], capture_output=True, timeout=15)
    except FileNotFoundError:
        return None
    except Exception:                        # noqa: BLE001 — timeout / wedged daemon
        return "stopped"
    return "ok" if r.returncode == 0 else "stopped"


def _is_rootless_podman(run=subprocess.run) -> bool:
    """Detect if Podman is running in rootless mode.

    Rootless Podman is the preferred mode for campus machines where users lack sudo access.
    This checks the output of `podman info` for the rootless indicator.
    """
    try:
        r = run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], 
                capture_output=True, timeout=15, text=True)
        if r.returncode == 0:
            return "true" in r.stdout.lower().strip()
    except Exception:  # noqa: BLE001
        pass
    return False


def _forced_engine() -> str | None:
    """``GINI_ENGINE=podman`` or ``docker`` — an explicit choice, not a probe.

    A developer Mac often has both Docker Desktop and a Podman machine. Detection then always
    picks Docker, which makes it look like the Podman path is dead. The lab machines will not
    need this (they have no docker binary); it exists so we can test the path they will take.
    """
    raw = (os.environ.get("GINI_ENGINE") or "").strip().lower()
    return raw if raw in ("podman", "docker") else None


def _settings_engine() -> str | None:
    """Read container engine preference from settings if available.

    Returns "docker", "podman", or None if not set or set to "auto".
    This allows users to persist their engine choice in the UI settings dialog.
    """
    try:
        from gini.app.paths import load_config
        config = load_config()
        engine = config.get("container_engine", "auto").strip().lower()
        return engine if engine in ("podman", "docker") else None
    except Exception:  # noqa: BLE001
        return None


def detect_engine(run=subprocess.run) -> str:
    """``"docker"`` | ``"podman"`` | ``"missing"`` — which container CLI is here.

    Detection priority:
    1. GINI_ENGINE environment variable (highest priority for development/testing)
    2. Settings preference (from ~/.gini/config.json)
    3. Auto-detection (Docker first, then Podman)

    Docker is tried first in auto-detection because it is the recommended runtime on every platform.
    Podman is tried only when there is no ``docker`` binary at all, so a Podman-only machine (no
    ``podman-docker`` symlink) is still detected rather than reported as missing.

    A custom ``run`` (tests) is never cached: the cache is for the real process-wide probe.
    Call ``_reset_engine_cache()`` in tests that need to re-probe the default runner.
    """
    global _ENGINE  # noqa: PLW0603 — one write, at startup; every later read is fast
    use_cache = run is subprocess.run
    
    # Environment variable takes highest priority
    forced = _forced_engine()
    if forced:
        probe = _probe_binary(forced, run)
        result = forced if probe is not None else "missing"
        if use_cache:
            _ENGINE = result
        return result
    
    # Settings preference takes second priority
    settings_pref = _settings_engine()
    if settings_pref:
        probe = _probe_binary(settings_pref, run)
        result = settings_pref if probe is not None else "missing"
        if use_cache:
            _ENGINE = result
        return result
    
    # Auto-detection as fallback
    if use_cache and _ENGINE is not None:
        return _ENGINE
    docker = _probe_binary("docker", run)
    if docker is not None:
        result = "docker"
    else:
        podman = _probe_binary("podman", run)
        result = "podman" if podman is not None else "missing"
    if use_cache:
        _ENGINE = result
    return result


def _reset_engine_cache() -> None:
    """For tests only — forget the cached engine so the next call re-probes."""
    global _ENGINE  # noqa: PLW0603
    _ENGINE = None


def engine_cli(run=subprocess.run) -> list[str]:
    """The CLI prefix for container commands: ``["docker"]`` or ``["podman"]``.

    Falls back to ``["docker"]`` when nothing is detected, so error messages still name a
    binary the user can look up.
    """
    e = detect_engine(run=run)
    return ["podman"] if e == "podman" else ["docker"]


def compose_cli(run=subprocess.run) -> list[str]:
    """The CLI prefix for compose commands: ``["docker", "compose"]`` or ``["podman", "compose"]``.

    Podman 4+ ships ``podman compose`` as a pass-through to an external compose provider
    (either the official Docker Compose v2 binary or ``podman-compose``). This is the
    recommended path and the one tested in the Trottier lab.
    """
    e = detect_engine(run=run)
    if e == "podman":
        return ["podman", "compose"]
    return ["docker", "compose"]


def engine_name(run=subprocess.run) -> str:
    """Human-readable name of the detected engine: ``"Docker"`` or ``"Podman"``.

    Used in user-facing messages where saying "Docker" when the machine has Podman would be
    confusing.
    """
    e = detect_engine(run=run)
    return "Podman" if e == "podman" else "Docker"


def using_podman(run=subprocess.run) -> bool:
    """True only after a probe (or test cache) has named Podman."""
    return detect_engine(run=run) == "podman"


def detect_os() -> str:
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), "unknown")


def docker_state(run=subprocess.run) -> str:
    """`"ok"` | `"stopped"` | `"missing"` — because the two failures need OPPOSITE advice.

    This used to be a bare yes/no, so "Docker is not installed" and "Docker is installed but its
    engine is not running" produced the same message: install it. Telling somebody who already has
    Docker to install it again sends them off to fix the wrong thing, and never mentions the one
    action that would work.

    Now tries both ``docker`` and ``podman``, in that order. A machine with only Podman (no
    ``docker`` symlink) is detected rather than reported as missing — which was the whole reason
    the Trottier lab machines could not run GINI.

    Absence is read from ``run`` itself rather than from ``shutil.which``, so that ``run`` is the
    ONLY thing this function touches. It used to check ``which`` first, which is not injectable —
    so a caller that injected a perfectly healthy fake still got "missing" on a machine with no
    docker binary, and five tests in test_bootstrap/test_setup passed only because the developer
    happened to have Docker installed. They failed the moment the suite ran anywhere else. A
    missing executable already announces itself precisely, and from inside the seam:
    ``subprocess.run`` raises FileNotFoundError.
    """
    # An explicit GINI_ENGINE=podman must not be overruled by a docker binary that happens
    # to be on PATH (the developer-Mac case).
    if _forced_engine() == "podman":
        state = _probe_binary("podman", run)
        return state if state is not None else "missing"
    docker = _probe_binary("docker", run)
    if docker is not None:
        return docker
    podman = _probe_binary("podman", run)
    if podman is not None:
        return podman
    return "missing"


def compose_available(run=subprocess.run) -> bool:
    """Is compose (the v2 plugin or ``podman compose``) here?

    Asked SEPARATELY from the daemon, on purpose.

    They fail independently, and on Linux they constantly do: ``apt install docker.io`` gives a
    working engine and CLI with NO compose plugin. ``docker info`` then answers happily, preflight
    declares the machine ready, a student draws a topology, presses Run — and gets

        Run failed: unknown flag: --build
        Usage:  docker [OPTIONS] COMMAND [ARG...]

    because ``docker compose up --build -d`` reaches a CLI with no ``compose`` subcommand, and the
    top-level parser rejects the first flag it does not know. Nothing in that names the cause,
    and nobody would guess "install a plugin" from it.

    Invisible on macOS, which is why it lasted: Docker Desktop and Colima both bundle compose v2.
    """
    # Docker compose first, and only FileNotFoundError (no docker binary) falls through to
    # Podman. A docker CLI that exists but has no compose plugin must NOT be reported as
    # "compose available" just because `podman compose` happens to work — those are two
    # engines, and the rest of GINI will talk to the one detect_engine picked.
    # GINI_ENGINE=podman skips the docker compose probe entirely.
    first, fallback = (["podman", "compose"], None) if _forced_engine() == "podman" else (
        ["docker", "compose"], ["podman", "compose"])
    try:
        return (run([*first, "version"],
                    capture_output=True, timeout=15).returncode == 0)
    except FileNotFoundError:
        pass
    except Exception:            # noqa: BLE001 — no plugin; it cannot run
        return False
    if fallback is None:
        return False
    try:
        return (run([*fallback, "version"],
                    capture_output=True, timeout=15).returncode == 0)
    except Exception:            # noqa: BLE001
        return False


def docker_available(run=subprocess.run) -> bool:
    """True if a Docker-compatible CLI is present AND a daemon answers.

    Works for Docker (Docker Desktop / Colima / Docker Engine) and Podman alike — detection
    of which binary to use is handled by ``detect_engine``.
    """
    return docker_state(run=run) == "ok"


# Per-OS plan: the runtime we recommend, the commands we CAN auto-run (with consent), and the manual
# fallback text. Colima is macOS/Linux; Windows has no Colima.
_PLANS = {
    "macos": {
        "runtime": "Colima + docker CLI",
        "auto": ["brew install colima docker",
                 "colima start --cpu 2 --memory 4 --disk 30"],
        "needs": "Homebrew",
        "manual": ("Install Homebrew from https://brew.sh, then run:\n"
                   "    brew install colima docker\n"
                   "    colima start --cpu 2 --memory 4 --disk 30"),
        "start": "colima start --cpu 2 --memory 4 --disk 30\n(or just open Docker Desktop, if that is what you use)",
        "compose": ("Install the Compose plugin:\n"
                    "    brew install docker-compose\n"
                    "then make sure `docker compose version` prints v2. Docker Desktop and Colima "
                    "normally include it already."),
    },
    "linux": {
        "runtime": "Docker Engine or Podman (rootless recommended)",
        "auto": [],   # distro-specific + needs sudo -> we guide rather than run
        "needs": "sudo / your package manager",
        "manual": ("Install Docker Engine for your distro "
                   "(https://docs.docker.com/engine/install/) and add your user to the 'docker' "
                   "group:  sudo usermod -aG docker $USER  (then log out/in).\n\n"
                   "Alternatively, install Podman (rootless recommended for campus machines):\n"
                   "    sudo apt install podman              # Debian/Ubuntu\n"
                   "    sudo dnf install podman              # Fedora/RHEL\n"
                   "then enable the rootless socket:\n"
                   "    systemctl --user enable --now podman.socket\n"
                   "and install a Compose provider:\n"
                   "    sudo apt install podman-compose      # or docker-compose-v2"),
        "start": "sudo systemctl start docker\n(or, for rootless Podman: systemctl --user start podman.socket)",
        # The common one. Ubuntu's own `docker.io` package does NOT carry compose, and Docker's
        # repo splits it into its own package, so a perfectly working Docker often has no compose.
        "compose": ("Install a Compose provider, then log out and back in if needed:\n"
                    "    sudo apt install docker-compose-plugin    # Docker's own apt repo\n"
                    "    sudo apt install docker-compose-v2        # Ubuntu's docker.io package\n"
                    "    sudo apt install podman-compose           # for Podman\n"
                    "(Fedora/RHEL: sudo dnf install docker-compose-plugin)\n\n"
                    "Check it with:  docker compose version   (or: podman compose version)"),
    },
    "windows": {
        "runtime": "Docker Desktop (or Podman Desktop)",
        "auto": ["winget install -e --id Docker.DockerDesktop"],
        "needs": "winget + admin",
        "manual": ("Install Docker Desktop (https://www.docker.com/products/docker-desktop) or "
                   "Podman Desktop, then start it. (Colima is not available on Windows.)"),
        "start": "Start Docker Desktop (or Podman Desktop) from the Start menu.",
        "compose": ("Docker Desktop includes Compose. If `docker compose version` fails, repair or "
                    "reinstall Docker Desktop from https://www.docker.com/products/docker-desktop"),
    },
}


def runtime_plan(os_name: str) -> dict:
    return _PLANS.get(os_name, {"runtime": "a Docker-compatible runtime", "auto": [], "needs": "",
                                "manual": "Install Docker or Podman and start it.",
                                "start": "Start your container runtime, then launch gBuilder again."})


def run_shell(cmd: str, run=subprocess.run) -> int:
    """Run one auto-install command, streaming to the console. Returns the exit code."""
    try:
        return run(cmd, shell=True).returncode
    except Exception:
        return 1


#: Lines Podman prints on its own behalf, on stderr, for every single compose command.
#:
#:     >>>> Executing external compose provider "/usr/bin/podman-compose". Please refer to the
#:     documentation for details. <
#:
#: Podman 5 delegates `podman compose` to an external provider and announces it every time. It is
#: not an error and it is not about the command that was run, but it arrives on the same stream as
#: real failures and it arrives FIRST — so anything that reports "the first 120 characters of
#: stderr" reports the banner, on every Podman machine, for every failure. Reported from the
#: Trottier lab as "Name resolution could not be set up on M1: >>>> Executing external compose
#: provider", which names the wrong thing entirely and hides the one that mattered.
_NOISE = (
    "executing external compose provider",
    "please refer to the documentation",
    "podman-compose version",
    "exit code:",
)


def compose_error(raw, *, limit: int = 240) -> str:
    """The useful part of a failed compose command's stderr.

    Two fixes in one: drop the provider banner, and read from the END. A traceback or a usage
    message puts its point on the last line and its preamble on the first, so truncating the front
    keeps the preamble — which is how a real error becomes a quote of somebody's boilerplate.
    """
    text = raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    lines = [ln.strip() for ln in text.splitlines()]
    keep = [ln for ln in lines
            if ln and not any(n in ln.lower() for n in _NOISE)]
    if not keep:
        return ""
    out = " / ".join(keep[-2:])
    return out[:limit]

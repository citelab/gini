"""Container-runtime detection + per-OS install guidance.

The runtime can't be bundled, and it differs per OS (Colima is macOS/Linux only; Windows uses Docker
Desktop/Podman). We detect a working Docker socket and, where we can, offer the auto-install commands
— but only after the user consents (we never silently run privileged installers)."""
from __future__ import annotations

import platform
import shutil
import subprocess


def detect_os() -> str:
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), "unknown")


def docker_state(run=subprocess.run) -> str:
    """`"ok"` | `"stopped"` | `"missing"` — because the two failures need OPPOSITE advice.

    This used to be a bare yes/no, so "Docker is not installed" and "Docker is installed but its
    engine is not running" produced the same message: install it. Telling somebody who already has
    Docker to install it again sends them off to fix the wrong thing, and never mentions the one
    action that would work.
    """
    if not shutil.which("docker"):
        return "missing"
    try:
        r = run(["docker", "info"], capture_output=True, timeout=15)
        return "ok" if r.returncode == 0 else "stopped"
    except Exception:            # a timeout is a daemon that is starting or wedged, not an absent one
        return "stopped"


def docker_available(run=subprocess.run) -> bool:
    """True if a Docker-compatible CLI is present AND a daemon answers (Colima/Desktop/Engine/Podman)."""
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
    },
    "linux": {
        "runtime": "Docker Engine",
        "auto": [],   # distro-specific + needs sudo -> we guide rather than run
        "needs": "sudo / your package manager",
        "manual": ("Install Docker Engine for your distro "
                   "(https://docs.docker.com/engine/install/) and add your user to the 'docker' "
                   "group:  sudo usermod -aG docker $USER  (then log out/in). Podman also works."),
        "start": "sudo systemctl start docker",
    },
    "windows": {
        "runtime": "Docker Desktop (or Podman Desktop)",
        "auto": ["winget install -e --id Docker.DockerDesktop"],
        "needs": "winget + admin",
        "manual": ("Install Docker Desktop (https://www.docker.com/products/docker-desktop) or "
                   "Podman Desktop, then start it. (Colima is not available on Windows.)"),
        "start": "Start Docker Desktop (or Podman Desktop) from the Start menu.",
    },
}


def runtime_plan(os_name: str) -> dict:
    return _PLANS.get(os_name, {"runtime": "a Docker-compatible runtime", "auto": [], "needs": "",
                                "manual": "Install Docker or a compatible runtime and start it.",
                                "start": "Start your container runtime, then launch gBuilder again."})


def run_shell(cmd: str, run=subprocess.run) -> int:
    """Run one auto-install command, streaming to the console. Returns the exit code."""
    try:
        return run(cmd, shell=True).returncode
    except Exception:
        return 1

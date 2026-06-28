"""gLoader -- the GINI topology loader.

gBuilder draws a topology and saves it as a ``.gini`` spec; gLoader is the component
that turns that spec into a *running* network. It does its job in two steps that the
user never has to see:

  1. **Compile** the spec into a concrete runtime plan -- give each broadcast domain a
     subnet, hand every interface an IP/MAC, assign each link a UDP port, and work out
     the routes (this is :class:`~gini.services.compiler.RuntimeCompiler`).
  2. **Launch** the plan -- a Docker container per end system, a gRouter process per
     router, a switch process per switch, all wired together by Ethernet-over-UDP
     links (this is :class:`~gini.services.orchestrator.Orchestrator`). There is also
     an in-process simulator for a no-Docker quick run.

It accepts either a live in-memory :class:`Topology` (what gBuilder hands it when you
press Run) or a saved ``.gini`` file (handy from the command line).

Programmatic::

    GLoader(runtime_dir).up(topology, workdir)   # compile + launch on Docker
    GLoader(runtime_dir).simulate(topology)      # compile + run in-process

Command line::

    python -m gini.gloader topology.gini         # launch on Docker
    python -m gini.gloader topology.gini --sim   # run the in-process simulator
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..domain.topology import Topology
from .compiler import RuntimeCompiler, RuntimeConfig
from .orchestrator import Orchestrator, Sim, simulate
from .persistence import load_project

Spec = "Topology | RuntimeConfig | str | Path"


class GLoader:
    """Loads a topology spec and brings the network up (compile + launch)."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self._compiler = RuntimeCompiler()
        self._orch = Orchestrator(runtime_dir)

    # -- spec handling ------------------------------------------------------ #
    @staticmethod
    def read_spec(path: str | Path) -> Topology:
        """Read a saved ``.gini`` topology spec from disk."""
        return load_project(path)

    def compile(self, topo: Topology) -> RuntimeConfig:
        """Turn a topology into a concrete runtime plan (the hidden compile step)."""
        return self._compiler.compile(topo)

    def _as_config(self, spec) -> RuntimeConfig:
        if isinstance(spec, RuntimeConfig):
            return spec
        topo = spec if isinstance(spec, Topology) else self.read_spec(spec)
        return self.compile(topo)

    # -- launch ------------------------------------------------------------- #
    def up(self, spec, workdir: str | Path | None = None,
           auto_internet: bool = True) -> tuple[bool, str]:
        """Compile and launch the topology on Docker.

        ``spec`` may be a live :class:`Topology`, a pre-compiled :class:`RuntimeConfig`,
        or a path to a saved ``.gini`` file. ``auto_internet`` off makes the lab network
        ``internal`` (no outbound internet — faithful mode).
        """
        cfg = self._as_config(spec)
        workdir = workdir or tempfile.mkdtemp(prefix="gini-lab-")
        return self._orch.up(cfg, workdir, auto_internet=auto_internet)

    def redeploy_faas(self, spec, auto_internet: bool = True) -> tuple[bool, str]:
        """Re-deploy only the serverless runtime with the current function code (AWS-style
        'Deploy') — recreates just the `faas` container, leaving the rest of the lab up."""
        return self._orch.redeploy_faas(self._as_config(spec), auto_internet=auto_internet)

    def simulate(self, spec) -> Sim:
        """Compile and run the topology in-process (no Docker)."""
        return simulate(self._as_config(spec))

    def update_cpus(self, service: str, cpus: float) -> tuple[bool, str]:
        """Live-change a running container's CPU cap (vertical scaling), no restart."""
        return self._orch.update_cpus(service, cpus)

    def stats(self, service: str) -> dict | None:
        """One CPU%/memory sample for a running container (for the Live tab plots)."""
        return self._orch.stats(service)

    def stats_all(self) -> dict:
        """CPU/mem/net for every running container in one call (per-element Live history)."""
        return self._orch.stats_all()

    def runtime_available(self, name: str) -> bool:
        """Whether the active Docker backend has an OCI runtime (e.g. 'kata') registered."""
        return self._orch.runtime_available(name)

    def startup_times(self) -> dict:
        """Per-element startup time in ms (the VM-vs-container headline metric)."""
        return self._orch.startup_times()

    def k8s_apply(self, service: str) -> tuple[bool, str]:
        """Apply the generated K8s manifests once the k3s cluster is Ready."""
        return self._orch.k8s_apply(service)

    def k8s_pods(self, service: str) -> list:
        """Current pods in a k3s cluster (for canvas/status read-back)."""
        return self._orch.k8s_pods(service)

    def k8s_metrics(self, service: str) -> dict:
        """Per-deployment replicas / CPU% / target for the Live view."""
        return self._orch.k8s_metrics(service)

    def k8s_scale(self, service: str, deployment: str, replicas) -> tuple[bool, str]:
        return self._orch.k8s_scale(service, deployment, replicas)

    def k8s_set_hpa(self, service: str, hpa: str, target=None, mn=None, mx=None):
        return self._orch.k8s_set_hpa(service, hpa, target, mn, mx)

    def fabric_metrics(self) -> dict | None:
        """The cloud-fabric agent's normalized app-level metrics for the whole lab."""
        return self._orch.fabric_metrics()

    def drive_load(self, host_port: int, url: str, qps, conns=8) -> tuple[bool, str]:
        """(Re)start a Fortio load generator at `qps` against `url` — also the throttle."""
        return self._orch.drive_load(host_port, url, qps, conns)

    def stop_load(self, host_port: int) -> tuple[bool, str]:
        return self._orch.stop_load(host_port)

    def down(self) -> tuple[bool, str]:
        """Tear the running network down."""
        return self._orch.down()

    def status(self, workdir: str | Path | None = None) -> dict[str, str]:
        """Per-element run state of the launched network."""
        return self._orch.status(workdir)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="gini.gloader",
        description="Load a GINI topology spec (.gini) and bring the network up.")
    ap.add_argument("spec", help="path to a saved .gini topology spec")
    ap.add_argument("--sim", action="store_true",
                    help="run the in-process simulator instead of launching Docker")
    args = ap.parse_args(argv)

    from .. import runtime as _rt
    loader = GLoader(Path(_rt.__file__).parent)

    if args.sim:
        sim = loader.simulate(args.spec)
        sim.start()
        print(f"gLoader: simulating {args.spec} ({len(sim._nodes)} nodes). "
              f"Press Ctrl-C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    ok, msg = loader.up(args.spec)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

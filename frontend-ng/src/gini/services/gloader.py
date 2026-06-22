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

    def simulate(self, spec) -> Sim:
        """Compile and run the topology in-process (no Docker)."""
        return simulate(self._as_config(spec))

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

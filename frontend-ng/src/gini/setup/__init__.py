"""GINI setup — the `gini-setup` command that brings in the container runtime + images.

`pip install gini-toolkit` gives the desktop app; the heavy runtime (Docker/Colima) and the custom
container images live outside the wheel. `gini-setup` detects/guides the runtime install and pulls
the images from the registry, then writes a marker (~/.gini/setup.json) so `gbuilder` knows live Run
is ready (Demo mode always works without it).
"""
from __future__ import annotations

# Registry namespace for the published images. Placeholder until the GHCR org is created + CI pushes;
# override with the GINI_REGISTRY env var.
import os

REGISTRY = os.environ.get("GINI_REGISTRY", "ghcr.io/gini-toolkit")

# The custom images the app needs (third-party images like alpine/postgres are pulled by Compose).
IMAGES = ["gini-xv6", "gini-oszoo", "gini-grouter", "gini-pox"]

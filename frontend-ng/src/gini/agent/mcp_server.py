"""MCP server exposing the shared tool registry to external AI agents.

The tool *behavior* lives in one place — `tools.registry` — and is shared with the
in-app agent loop. Here we publish those same tools over MCP with explicit signatures
(so clients get correct schemas), each delegating to `registry.execute`. Install the
optional dep with: pip install gini-toolkit[agent].

Run standalone:  python -m gini.agent.mcp_server
"""
from __future__ import annotations

import json

from ..app import AppContext
from .api import GiniAPI
from .tools.registry import ToolRegistry, build_registry


def build_server(api: GiniAPI, registry: ToolRegistry | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("MCP server needs 'mcp'. Install: pip install gini-toolkit[agent]") from e

    reg = registry or build_registry(api)
    server = FastMCP("gini-toolkit")

    def call(name: str, **kwargs) -> str:
        return json.dumps(reg.execute(name, kwargs), default=str)

    @server.tool()
    def list_device_types() -> str:
        """List every device/element type GINI can place (networking + cloud)."""
        return call("list_device_types")

    @server.tool()
    def add_device(type_key: str, name: str = "", x: float = 0.0, y: float = 0.0) -> str:
        """Add a device. type_key e.g. 'router','switch','vpc','container','instance'."""
        return call("add_device", type_key=type_key, name=name, x=x, y=y)

    @server.tool()
    def connect_devices(a: str, b: str, label: str = "") -> str:
        """Create a link between two devices (by name or id)."""
        return call("connect_devices", a=a, b=b, label=label)

    @server.tool()
    def set_property(device: str, key: str, value: str) -> str:
        """Set a property on a device (by name or id)."""
        return call("set_property", device=device, key=key, value=value)

    @server.tool()
    def inspect_device(device: str) -> str:
        """Inspect a device's type, properties, neighbors, and degree."""
        return call("inspect_device", device=device)

    @server.tool()
    def get_topology() -> str:
        """Return the full topology as JSON (devices + links)."""
        return call("get_topology")

    @server.tool()
    def summarize_topology() -> str:
        """Counts of devices, links, and categories."""
        return call("summarize_topology")

    @server.tool()
    def explain_topology() -> str:
        """Explain the whole topology in plain language for a student."""
        return call("explain_topology")

    @server.tool()
    def explain_device(device: str) -> str:
        """Explain a single device in plain language for a student."""
        return call("explain_device", device=device)

    return server


def main() -> None:  # pragma: no cover
    ctx = AppContext()
    server = build_server(GiniAPI(ctx))
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()

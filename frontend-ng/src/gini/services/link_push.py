"""Push a link-cost change into a running lab (docs/design/link-properties.md, Phase 2).

When a link's cost changes while the lab runs, two things must reach the routers:

  1. Both ends learn the new cost: `ifconfig mod tunN -metric C` on each router the link touches.
     A routing protocol (RIP in Lua) reads it from interfaces() on its next tick. That is all
     DYNAMIC mode needs -- gBuilder computes no routes there.
  2. In STATIC mode the routes themselves change, because the cheapest path may now be another
     one. gBuilder recompiles the whole network and pushes only the differences: a route whose
     next hop moved is re-added (the router's `route add` replaces an entry for the same network
     in one step, so there is no window without it), a route no longer wanted is deleted by
     network (`route del -net ... -netmask ...`, never by table index, which shifts), and the
     rest is left alone. Only routes gBuilder installed are ever touched -- a route a student
     typed is theirs.

A failure does NOT recompute static routes. That is what static routing does, and it is the
contrast a failure lab is built on (Phase 3).

Pure: no Qt, no Docker. It plans commands; the main window runs them over each router's console.
"""
from __future__ import annotations


def structure(topology) -> tuple:
    """What the running lab's addressing depends on: devices and the links between them. If this
    differs from what was Run, interface numbers and subnets may have moved, and pushing routes
    computed from the new topology into the old lab would be wrong."""
    devs = tuple(sorted((d.id, d.type_key, d.name) for d in topology.devices.values()))
    links = tuple(sorted((l.id, l.source_id, l.target_id, l.kind)
                         for l in topology.links.values()))
    return devs, links


def link_costs(topology) -> dict:
    from ..domain import link_props as LP
    return {l.id: LP.get(l, LP.COST) for l in topology.links.values()}


def installed_routes(cfg) -> dict:
    """{router name: {(net, mask): (gw, dev)}} -- the static routes a compiled config installs."""
    return {r.name: {(rt["net"], rt["mask"]): (rt["gw"], rt["dev"]) for rt in r.routes}
            for r in cfg.routers}


def interfaces_on(cfg, link_id: str) -> list:
    """[(router name, tun index)] for every router interface that sits on `link_id`."""
    out = []
    for r in cfg.routers:
        for i, itf in enumerate(r.ifaces, start=1):        # run_grouter numbers tun1, tun2, ...
            if getattr(itf, "link_id", "") == link_id:
                out.append((r.name, i))
    return out


def plan(new_cfg, installed: dict, changed: dict, static: bool) -> tuple[list, dict, list]:
    """The commands that bring a running lab in line with `new_cfg`.

    `installed`: what gBuilder put in each router last ({router: {(net, mask): (gw, dev)}}).
    `changed`:   {link id: new cost} for the links whose cost changed.
    `static`:    the lab runs in static routing mode.

    Returns (commands, new installed table, messages). commands is [(router name, console
    command)], in order: costs first, then route changes per router. messages are one line per
    change, for the log.
    """
    cmds, msgs = [], []
    for lid, cost in changed.items():
        for rname, tun in interfaces_on(new_cfg, lid):
            cmds.append((rname, f"ifconfig mod tun{tun} -metric {int(cost)}"))
            msgs.append(f"{rname}: tun{tun} cost {int(cost)}")
    new_installed = installed_routes(new_cfg) if static else dict(installed)
    if not static:
        return cmds, new_installed, msgs
    for rname in sorted(set(installed) | set(new_installed)):
        old, new = installed.get(rname, {}), new_installed.get(rname, {})
        for key in sorted(new):
            if old.get(key) != new[key]:
                (net, mask), (gw, dev) = key, new[key]
                cmds.append((rname, f"route add -dev tun{dev} -net {net} -netmask {mask} "
                                    f"-gw {gw}"))
                msgs.append(f"{rname}: {net}/{_prefix(mask)} now via {gw}")
        for key in sorted(old):
            if key not in new:
                net, mask = key
                cmds.append((rname, f"route del -net {net} -netmask {mask}"))
                msgs.append(f"{rname}: {net}/{_prefix(mask)} withdrawn")
    return cmds, new_installed, msgs


def _prefix(mask: str) -> int:
    try:
        return sum(bin(int(o)).count("1") for o in mask.split("."))
    except ValueError:
        return 0

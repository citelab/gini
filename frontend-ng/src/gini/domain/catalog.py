"""The Game Catalog — pre-authored game archetypes the AI game master understands.

Because GINI's arena is bounded (finite palette, grammar, behavioral properties), the space of
meaningful challenges is enumerable, so we author them *once, ahead of time* as archetypes. Each
archetype is a parameterized challenge pattern: a concept it teaches, its **spirit** (what
counts as success, by ANY mechanism), its objectives (behavioral where possible, so alternative
valid solutions pass), common misconceptions, and difficulty knobs.

This is the assessment-dual of `recipes` (Recipes build; archetypes challenge + witness) and a
sibling KB asset to `concepts`/`recipes`. Objectives are written in the `objectives` predicate
language. `instantiate()` binds an archetype's ref placeholders to concrete canvas names to
produce a lesson's Objective list — used by explicit authoring now and the LLM resolver later.

Pure data; no Qt, no LLM. Phase 1 seeds a handful; `to get more games, grow the catalog`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .objectives import Objective


@dataclass(frozen=True)
class ObjectiveTemplate:
    id: str
    say: str
    kind: str = "structural"     # structural | behavioral
    check: str = ""              # structural predicate (may contain {ref} placeholders)
    probe: str = ""              # behavioral probe (may contain {ref} placeholders)


@dataclass(frozen=True)
class Archetype:
    id: str
    teaches: str                 # concepts.Concept.key
    spirit: str                  # what success means, mechanism-free (the game master reasons on this)
    summary: str
    params: tuple[str, ...] = ()          # ref names the objectives reference
    objectives: tuple[ObjectiveTemplate, ...] = ()
    misconceptions: tuple[str, ...] = ()
    complete_when: str = "all"            # all | any | at_least(n)
    difficulty: dict = field(default_factory=dict)


# -- the seed catalog ------------------------------------------------------- #
ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        id="basic-lan",
        teaches="networking-basics",
        spirit="two machines on one switched LAN can reach each other, with a router as the gateway "
               "off the subnet — any valid wiring that forms that shape counts.",
        summary="Build a switched LAN: two hosts on a switch, a router as gateway.",
        params=("h1", "h2", "sw", "gw"),
        objectives=(
            ObjectiveTemplate("has-switch", "There is a switch", "structural", check="exists(switch)"),
            ObjectiveTemplate("two-hosts", "At least two hosts", "structural", check="count(host) >= 2"),
            ObjectiveTemplate("has-gateway", "There is a router (gateway)", "structural",
                              check="exists(router)"),
            ObjectiveTemplate("hosts-reach", "The two hosts are connected", "structural",
                              check="connected({h1}, {h2})"),
            ObjectiveTemplate("gateway-on-lan", "The gateway is reachable from a host", "structural",
                              check="connected({h1}, {gw})"),
        ),
        misconceptions=("Wiring both hosts straight together with no switch.",
                        "Forgetting the router, so the LAN has no way off-subnet."),
        difficulty={"easy": "prebuilt hosts + switch", "hard": "empty canvas"},
    ),
    Archetype(
        id="reachability-boundary",
        teaches="vpc-networking",
        spirit="a protected service is reachable by its peers inside the boundary but NOT from the "
               "outside — achieved by a private subnet, a security group, or firewall placement; the "
               "mechanism is free, the reachability is the point.",
        summary="Keep a database reachable from the web tier but hidden from the Internet.",
        params=("inside", "protected", "outsider", "box"),
        objectives=(
            ObjectiveTemplate("in-boundary", "Web and DB both live inside the VPC", "structural",
                              check="contains({box}, {inside}) and contains({box}, {protected})"),
            ObjectiveTemplate("reaches", "Web app can reach the database", "behavioral",
                              probe="reach({inside} -> {protected}) == ok"),
            ObjectiveTemplate("shielded", "Database is unreachable from the Internet", "behavioral",
                              probe="reach({outsider} -> {protected}) == fail"),
        ),
        misconceptions=("Thinking 'private' means unreachable by everything (still reachable inside).",
                        "Exposing the DB to the Internet to 'make it work'."),
        difficulty={"easy": "prebuilt VPC + tiers", "hard": "empty canvas"},
    ),
    Archetype(
        id="put-in-vpc",
        teaches="vpc-networking",
        spirit="the workloads sit inside the VPC boundary — however the student draws the grouping.",
        summary="Place the given workloads inside a VPC.",
        params=("box", "a", "b"),
        objectives=(
            ObjectiveTemplate("a-in", "First workload is inside the VPC", "structural",
                              check="contains({box}, {a})"),
            ObjectiveTemplate("b-in", "Second workload is inside the VPC", "structural",
                              check="contains({box}, {b})"),
        ),
        misconceptions=("Leaving a workload outside the box but wired in — containment is membership.",),
        difficulty={"easy": "two workloads pre-placed", "hard": "build the tiers too"},
    ),
    Archetype(
        id="load-balanced-web",
        teaches="load-balancing",
        spirit="incoming traffic is spread across several backend replicas via a load balancer — "
               "any scheme counts as long as the fan-out exists and serves.",
        summary="Front several web replicas with a load balancer.",
        params=("lb", "backend"),
        objectives=(
            ObjectiveTemplate("has-lb", "There is a load balancer", "structural",
                              check="exists(load_balancer)"),
            ObjectiveTemplate("enough-backends", "At least two backends", "structural",
                              check="count(web_app) >= 2"),
            ObjectiveTemplate("serves", "The load balancer serves the backends", "behavioral",
                              probe="balances({lb}, >= 2)"),
        ),
        misconceptions=("A single backend behind the LB (nothing to balance).",),
        difficulty={"easy": "backends pre-placed", "hard": "build + wire everything"},
    ),
    # ---- SDN ------------------------------------------------------------- #
    Archetype(
        id="sdn-reactive",
        teaches="sdn",
        spirit="an OpenFlow switch with no built-in logic gets its forwarding rules from a "
               "controller — the switch MUST be wired to a controller for traffic to flow.",
        summary="Wire an OpenVSwitch to a controller so it can forward.",
        params=("ovs", "ctrl", "h1", "h2"),
        objectives=(
            ObjectiveTemplate("has-ovs", "There is an OpenVSwitch", "structural", check="exists(ovs)"),
            ObjectiveTemplate("has-ctrl", "There is a controller", "structural",
                              check="exists(controller)"),
            ObjectiveTemplate("ovs-controlled", "The switch is wired to the controller", "structural",
                              check="linked({ovs}, {ctrl})"),
            ObjectiveTemplate("hosts-flow", "Hosts can reach each other through the SDN switch",
                              "behavioral", probe="reach({h1} -> {h2}) == ok"),
        ),
        misconceptions=("Expecting an OVS to forward with no controller attached (it can't).",),
        difficulty={"easy": "hosts + OVS pre-placed", "hard": "empty canvas"},
    ),
    # ---- NFV / SFC ------------------------------------------------------- #
    Archetype(
        id="service-chain",
        teaches="sfc",
        spirit="traffic passes THROUGH a network function on its way to the destination — a "
               "firewall (or VNF) sits in the path, by any valid wiring.",
        summary="Steer a host's traffic through a firewall to the Internet.",
        params=("h1", "fw", "net"),
        objectives=(
            ObjectiveTemplate("has-fw", "There is a firewall in the path", "structural",
                              check="exists(firewall)"),
            ObjectiveTemplate("chained", "Host → firewall → Internet is wired in series", "structural",
                              check="linked({h1}, {fw}) and linked({fw}, {net})"),
            ObjectiveTemplate("passes", "The host reaches the Internet through the chain",
                              "behavioral", probe="reach({h1} -> {net}) == ok"),
        ),
        misconceptions=("Wiring the host straight to the Internet, bypassing the function.",),
        difficulty={"easy": "elements pre-placed", "hard": "empty canvas"},
    ),
    # ---- serverless ------------------------------------------------------ #
    Archetype(
        id="serverless-api",
        teaches="serverless",
        spirit="an API gateway fronts a function so an HTTP request to the gateway invokes the "
               "function — the gateway must route to the function.",
        summary="Put a function behind an API gateway.",
        params=("gw", "fn"),
        objectives=(
            ObjectiveTemplate("has-fn", "There is a function", "structural", check="exists(function)"),
            ObjectiveTemplate("has-gw", "There is an API gateway", "structural",
                              check="exists(api_gateway)"),
            ObjectiveTemplate("routed", "The gateway routes to the function", "structural",
                              check="linked({gw}, {fn})"),
        ),
        misconceptions=("A function with no gateway — nothing can invoke it over HTTP.",),
        difficulty={"easy": "function pre-placed", "hard": "empty canvas"},
    ),
    # ---- kubernetes ------------------------------------------------------ #
    Archetype(
        id="k8s-autoscale",
        teaches="kubernetes",
        spirit="a pod runs inside a cluster with an autoscaler attached so it can scale on load.",
        summary="Run a pod in a cluster with an autoscaler.",
        params=("cluster", "pod"),
        objectives=(
            ObjectiveTemplate("has-cluster", "There is a Kubernetes cluster", "structural",
                              check="exists(k8s_cluster)"),
            ObjectiveTemplate("has-pod", "There is a pod", "structural", check="exists(pod)"),
            ObjectiveTemplate("pod-in-cluster", "The pod lives in the cluster", "structural",
                              check="contains({cluster}, {pod})"),
            ObjectiveTemplate("has-hpa", "There is a pod autoscaler", "structural",
                              check="exists(instance_group)"),
        ),
        misconceptions=("A pod outside any cluster — a pod must live in a cluster.",),
        difficulty={"easy": "cluster pre-placed", "hard": "empty canvas"},
    ),
    # ---- security groups ------------------------------------------------- #
    Archetype(
        id="least-privilege",
        teaches="security-groups",
        spirit="workloads are protected by a default-deny security group that opens only the "
               "ports they need — least privilege by design.",
        summary="Protect a workload with a security group.",
        params=("sg",),
        objectives=(
            ObjectiveTemplate("has-sg", "There is a security group", "structural",
                              check="exists(security_group)"),
        ),
        misconceptions=("Leaving a datastore open to the world instead of scoping ingress.",),
        difficulty={"easy": "workload pre-placed", "hard": "build the whole tier"},
    ),
    # ---- messaging ------------------------------------------------------- #
    Archetype(
        id="decouple-with-queue",
        teaches="messaging-queue",
        spirit="a producer and a consumer are decoupled by a queue — the producer publishes to "
               "the queue and the consumer reads from it, so neither waits on the other.",
        summary="Decouple a producer and consumer with a queue.",
        params=("producer", "queue", "consumer"),
        objectives=(
            ObjectiveTemplate("has-queue", "There is a queue", "structural", check="exists(queue)"),
            ObjectiveTemplate("produces", "The producer is wired to the queue", "structural",
                              check="linked({producer}, {queue})"),
            ObjectiveTemplate("consumes", "The consumer is wired to the queue", "structural",
                              check="linked({queue}, {consumer})"),
        ),
        misconceptions=("Wiring the producer straight to the consumer (no decoupling).",),
        difficulty={"easy": "endpoints pre-placed", "hard": "empty canvas"},
    ),
    # ---- datastores ------------------------------------------------------ #
    Archetype(
        id="cache-in-front",
        teaches="datastores",
        spirit="a cache sits in front of a database to cut read load — the cache and database "
               "both exist and are wired together.",
        summary="Put a cache in front of a database.",
        params=("cache", "db"),
        objectives=(
            ObjectiveTemplate("has-cache", "There is a cache", "structural", check="exists(cache)"),
            ObjectiveTemplate("has-db", "There is a database", "structural", check="exists(database)"),
            ObjectiveTemplate("fronted", "The cache is wired in front of the database", "structural",
                              check="linked({cache}, {db})"),
        ),
        misconceptions=("A cache not connected to anything it fronts.",),
        difficulty={"easy": "database pre-placed", "hard": "empty canvas"},
    ),
    # ---- observability --------------------------------------------------- #
    Archetype(
        id="observe-it",
        teaches="observability",
        spirit="a dashboard is fed by a metrics source so the system can be watched — the "
               "dashboard must connect to a metrics collector.",
        summary="Wire a dashboard to a metrics source.",
        params=("dash", "metrics"),
        objectives=(
            ObjectiveTemplate("has-metrics", "There is a metrics source", "structural",
                              check="exists(metrics)"),
            ObjectiveTemplate("has-dash", "There is a dashboard", "structural",
                              check="exists(dashboard)"),
            ObjectiveTemplate("wired", "The dashboard reads from the metrics source", "structural",
                              check="linked({dash}, {metrics})"),
        ),
        misconceptions=("A dashboard with no metrics source has nothing to show.",),
        difficulty={"easy": "metrics pre-placed", "hard": "empty canvas"},
    ),
)

_BY_ID = {a.id: a for a in ARCHETYPES}

# default ref→name bindings so a seed archetype can be launched for a quick preview without the
# resolver (Phase 3) or a staged canvas. Real lessons bind these via the resolver / stage.gini.
DEMO_PARAMS: dict[str, dict] = {
    "basic-lan": {"h1": "M1", "h2": "M2", "sw": "S1", "gw": "R1"},
    "reachability-boundary": {"inside": "WEB1", "protected": "DB1", "outsider": "NET", "box": "VPC1"},
    "put-in-vpc": {"box": "VPC1", "a": "WEB1", "b": "DB1"},
    "load-balanced-web": {"lb": "LB1", "backend": "WEB1"},
    "sdn-reactive": {"ovs": "OVS1", "ctrl": "CTRL1", "h1": "M1", "h2": "M2"},
    "service-chain": {"h1": "M1", "fw": "FW1", "net": "NET"},
    "serverless-api": {"gw": "GW1", "fn": "FN1"},
    "k8s-autoscale": {"cluster": "K8S1", "pod": "POD1"},
    "least-privilege": {"sg": "SG1"},
    "decouple-with-queue": {"producer": "APP1", "queue": "Q1", "consumer": "FN1"},
    "cache-in-front": {"cache": "CACHE1", "db": "DB1"},
    "observe-it": {"dash": "DASH1", "metrics": "MET1"},
}


def get(archetype_id: str) -> Archetype | None:
    return _BY_ID.get(archetype_id)


def demo_params(archetype_id: str) -> dict:
    return dict(DEMO_PARAMS.get(archetype_id, {}))


def all_archetypes() -> list[Archetype]:
    return list(ARCHETYPES)


# -- instantiation ---------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _bind(text: str, params: dict) -> str:
    """Replace {ref} placeholders with the bound concrete names."""
    return _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)


def instantiate(archetype: Archetype, params: dict) -> list[Objective]:
    """Bind an archetype's ref placeholders to concrete canvas names → concrete Objectives.
    `params` maps each ref (e.g. 'inside') to a placed device name (e.g. 'WEB1')."""
    out: list[Objective] = []
    for t in archetype.objectives:
        out.append(Objective(
            id=t.id, say=t.say, kind=t.kind,
            check=_bind(t.check, params) if t.check else "",
            probe=_bind(t.probe, params) if t.probe else "",
        ))
    return out


def unbound_refs(archetype: Archetype, params: dict) -> list[str]:
    """Placeholders in the archetype's objectives not covered by `params` (validation)."""
    refs: set[str] = set()
    for t in archetype.objectives:
        refs.update(_PLACEHOLDER.findall(t.check))
        refs.update(_PLACEHOLDER.findall(t.probe))
    return sorted(r for r in refs if r not in params)

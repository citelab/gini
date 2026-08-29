"""Certified activity patterns — the catalogue an AOP is assembled from.

A pattern is one teachable activity shape: what must be present, what must be true when it works,
in what order those facts depend on one another, and a single public sentence a student may be
shown (design §11 — pattern-level disclosure, expectation-level secrecy).

**Harvested, not invented.** Each pattern comes from a chapter of the book whose experiment is
already authored, sequenced and taught. That buys a property worth more than convenience: an
activity the generator proposes is always one the textbook supports.

The LLM never writes expectations — it picks patterns from here and binds their parameters, and
`aop_assemble` expands the result deterministically. This module is therefore plain data plus the
small function that turns a pattern into expectations.

**Free-form means unscoped tokens.** Slot scoping (`host@lanA`) is a composition artifact: only
`compose.py` ever tags a device with a slot, so on a blank canvas every device has `slot=""` and a
scoped token matches nothing. Patterns for free-form activities use plain type keys and lean on the
`all` quantifier when they mean "every one of them". `aop.validate()` enforces this.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .aop import Expectation


@dataclass(frozen=True)
class Pattern:
    """One certified activity shape, described three times for three different readers.

    A pattern has to speak to a machine, to a student, and to the reasoning engine that drafts
    plans, and those are not the same text:

    * ``build`` — the **machine** format. Expectations gBuilder can actuate. Authored once, by a
      person who knows the probe language.
    * ``summary`` — the **student** line, shown only when the teacher turns guidance on. A syllabus
      sentence: what will be looked at, never what to build or how to satisfy it.
    * ``observes`` / ``choose_when`` / ``not_covered`` — the **reasoning engine's** view. Plain
      English, no probe syntax.

    That third group is the whole bridge. The teaching AI reads a teacher's intent in prose and has
    to land on a machine-actuatable plan; it does that by matching intent against these
    descriptions, never by writing probes. So the model needs no knowledge of the probe grammar,
    cannot invent an expectation, and cannot produce anything the assembler will not accept — while
    the teacher gets a drafted plan instead of a catalogue to tick through.

    ``not_covered`` matters as much as ``observes``. Without it a model will happily claim a
    pattern handles something adjacent, and the teacher then approves a plan that quietly does not
    watch what they asked for.
    """
    key: str
    title: str
    source: str                      # the chapter this was harvested from
    summary: str                     # student-facing, guidance-on only
    observes: str = ""               # LLM-facing: what facts this actually checks
    choose_when: str = ""            # LLM-facing: the intents this fits
    not_covered: str = ""            # LLM-facing: what it deliberately does NOT observe
    param_help: dict = field(default_factory=dict)   # param -> plain-English meaning
    params: dict = field(default_factory=dict)
    build: object = None             # (pattern, params) -> [Expectation]

    def expectations(self, **overrides) -> list[Expectation]:
        p = dict(self.params)
        p.update(overrides)
        return list(self.build(self, p)) if self.build else []

    def brief(self) -> str:
        """This pattern as the reasoning engine sees it. Plain English only — deliberately no
        probe strings, so the model is never tempted to author one."""
        lines = [f"### {self.key}", f"Title: {self.title}  (from {self.source})",
                 f"Observes: {self.observes or self.summary}"]
        if self.choose_when:
            lines.append(f"Choose when: {self.choose_when}")
        if self.not_covered:
            lines.append(f"Does NOT observe: {self.not_covered}")
        if self.params:
            lines.append("Parameters:")
            for k, v in sorted(self.params.items()):
                lines.append(f"  - {k} (default {v!r}): {self.param_help.get(k, '')}".rstrip())
        else:
            lines.append("Parameters: none")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Ch.16 §16.1 — Single LAN IP networks
# --------------------------------------------------------------------------- #
def _single_lan(pattern: Pattern, p: dict) -> list[Expectation]:
    """Stations sharing one broadcast domain through a switch.

    The chapter's arc is: build it, prove every station reaches every other, then take the LAN
    apart — hub vs learning switch, addressing inside and outside the subnet, a duplicate address.
    Only the parts GINI can *witness* become expectations. The ARP-cache reading and the
    "explain why" questions are real work and are deliberately absent: nothing in the runtime can
    observe them, and inventing a proxy would be worse than the report saying nothing.
    """
    stations = int(p.get("stations", 3))
    switches = int(p.get("switches", 1))
    out = [
        Expectation(
            id="lan-stations", layer="L2", pattern=pattern.key,
            say=f"At least {stations} stations are on the canvas",
            check=f"count('host') >= {stations}"),
        Expectation(
            id="lan-switch", layer="L2", pattern=pattern.key,
            say=f"The LAN is built around {'a switch' if switches == 1 else f'{switches} switches'}",
            check=f"count('switch') >= {switches}"),
        Expectation(
            id="lan-wired", layer="L2", pattern=pattern.key,
            say="Every station is wired into the LAN rather than left floating",
            check="all_linked('host', 'switch')",
            requires=("lan-stations", "lan-switch")),
        # The behavioural heart of the section. `all` matters: with the existential reading one
        # working pair would mask a station that never came up.
        Expectation(
            id="lan-reach-all", layer="L3", pattern=pattern.key,
            say="Every station reaches every other station on the LAN",
            probe="reach(host -> host, all) == ok",
            requires=("lan-wired",)),
    ]
    if p.get("grown_lan"):
        # The section closes by growing one broadcast domain across several switches.
        out.append(Expectation(
            id="lan-grown", layer="L2", pattern=pattern.key,
            say="The LAN was grown across several switches and is still one broadcast domain",
            check="count('switch') >= 3 and count('host') >= 10",
            requires=("lan-switch",)))
        out.append(Expectation(
            id="lan-grown-reach", layer="L3", pattern=pattern.key,
            say="Every station on the grown LAN still reaches every other",
            probe="reach(host -> host, all) == ok",
            requires=("lan-grown",)))
    return out


SINGLE_LAN = Pattern(
    key="single-lan",
    title="Single LAN IP network",
    source="Ch.16 §16.1",
    summary="Building one LAN and showing that every station on it can reach every other.",
    observes=(
        "That the student built one broadcast domain — stations attached to a switch — and that "
        "every station on it can reach every other station once the lab is running."),
    choose_when=(
        "The activity is about a single flat network: one LAN, one subnet, no routing. Typical "
        "phrasings are 'build a LAN', 'connect some machines with a switch', 'get three "
        "machines talking to each other', or any first-network exercise. Also choose this when "
        "the activity is about ARP, about hubs versus learning switches, or about what happens "
        "when addresses on one subnet are changed or duplicated — the construction and "
        "reachability are the observable part of all of those."),
    not_covered=(
        "Anything that needs a router or a second subnet — use multi-lan for that. It also does "
        "not observe ARP cache contents, hub-versus-switch capture differences, or the effect of "
        "changing an address, because none of those can be witnessed from outside the student's "
        "terminal. If the activity is mainly about those, this pattern still observes that the "
        "LAN was built and works, and the rest is for the teacher to read in the narration."),
    param_help={
        "stations": "how many machines the LAN must have at minimum",
        "switches": "how many switches at minimum (1 for a plain LAN)",
        "grown_lan": ("True when the activity ends by growing the LAN across several switches — "
                      "the chapter's 'at least three switches and ten stations' close-out"),
    },
    params={"stations": 3, "switches": 1, "grown_lan": False},
    build=_single_lan,
)


# --------------------------------------------------------------------------- #
# Ch.16 §16.2 — Multiple LAN IP networks
# --------------------------------------------------------------------------- #
def _multi_lan(pattern: Pattern, p: dict) -> list[Expectation]:
    """Segments joined by routers, with traffic proven to cross a routed boundary.

    The load-bearing expectation is `multi-crosses-router`. Without it a student could satisfy
    every reachability claim with one flat LAN and the report would read as a pass — the exact
    "all-green on the wrong thing" failure a construction-derived plan cannot catch and an
    intent-derived plan must.
    """
    lans = int(p.get("lans", 5))
    routers = int(p.get("routers", 3))
    return [
        Expectation(
            id="multi-segments", layer="L2", pattern=pattern.key,
            say=f"The network is built from at least {lans} LAN segments",
            check=f"count('switch') >= {lans}"),
        Expectation(
            id="multi-routers", layer="L3", pattern=pattern.key,
            say=f"At least {routers} routers interconnect the segments",
            check=f"count('router') >= {routers}"),
        Expectation(
            id="multi-stations", layer="L2", pattern=pattern.key,
            say="Each segment carries at least one station",
            check=f"count('host') >= {lans}",
            requires=("multi-segments",)),
        Expectation(
            id="multi-wired", layer="L2", pattern=pattern.key,
            say="Stations are attached to segments rather than left floating",
            check="all_linked('host', 'switch')",
            requires=("multi-stations",)),
        # Structural proof that this is genuinely multi-LAN and not one flat network.
        #
        # NOT `through('router', 'host', 'host')`, which reads right and is wrong: `through` means
        # EVERY host-to-host path crosses the gate, and two stations on the same switch reach each
        # other without one. It is therefore false for any realistic multi-LAN topology — a
        # correctly-built network scored MISS on it until an end-to-end run caught this. `through`
        # is a chokepoint predicate ("all traffic must pass the firewall"), not a routing one.
        #
        # `all_linked('switch','router')` says every segment has a way off it — no LAN is stranded —
        # which is both satisfiable and the thing actually worth asserting. Cross-LAN traffic is
        # then proven behaviourally by `multi-reach-all`, where it belongs.
        Expectation(
            id="multi-crosses-router", layer="L3", pattern=pattern.key,
            say="Every LAN segment is attached to a router, so no segment is stranded",
            check="all_linked('switch', 'router')",
            requires=("multi-routers", "multi-wired")),
        Expectation(
            id="multi-reach-all", layer="L3", pattern=pattern.key,
            say="Every station reaches every other station, including across LAN boundaries",
            probe="reach(host -> host, all) == ok",
            requires=("multi-crosses-router",)),
    ]


MULTI_LAN = Pattern(
    key="multi-lan",
    title="Multiple LAN IP network",
    source="Ch.16 §16.2",
    summary=("Building a network of several LANs joined by routers, and showing that stations "
             "reach each other across the routed boundaries."),
    observes=(
        "That the student built several LAN segments joined by routers, that a path between "
        "stations genuinely passes through a router rather than being one flat network, and that "
        "stations reach each other across those routed boundaries when the lab runs."),
    choose_when=(
        "The activity involves more than one subnet, or any mention of routers, routing, "
        "gateways, forwarding, or traffic crossing between LANs. Typical phrasings are 'connect "
        "two LANs with a router', 'build a routed network', 'show traffic crossing a router', or "
        "anything about TTL, hop counts, or MAC rewriting at each hop — the routed topology is "
        "the observable part of all of those."),
    not_covered=(
        "It does not observe packet captures, TTL values, MAC address rewriting, traceroute "
        "output, or anything read inside a station's terminal. Those are the student's evidence "
        "to gather and the teacher's to read. It also says nothing about routing protocols — a "
        "network with static routes and one running RIP look identical to this pattern."),
    param_help={
        "lans": "how many LAN segments the network must have at minimum",
        "routers": "how many routers must interconnect them at minimum",
    },
    params={"lans": 5, "routers": 3},
    build=_multi_lan,
)


# --------------------------------------------------------------------------- #
# Ch.16 §16.2 — the link-delay close-out
# --------------------------------------------------------------------------- #
def _link_delay(pattern: Pattern, p: dict) -> list[Expectation]:
    """The chapter ends by making the path *longer* rather than thinner (`delay egress`).

    Exactly one expectation, deliberately.

    The obvious second one — "stations still reach each other with the delay in place" — is
    `reach(host -> host, all) == ok`, which is *character for character* what the multi-LAN pattern
    already asserts. Without temporal ordering (v1 has none, design §6.1) there is no way to say
    "still, now that delay is on": the two would be the same measurement, always agreeing, costing
    two `docker exec` calls to learn one fact. `validate()` refuses such pairs outright.

    The measurement half of the experiment — round trip near 80 ms, iperf3 slowing, the
    bandwidth-delay product — stays with the student, which is where the chapter puts it anyway.

    This pattern is what `property_type` was added for. Written with the name-based `property()`
    it would have to guess the student called their router R2, and would silently observe nothing.
    """
    return [
        Expectation(
            id="delay-configured", layer="policy", pattern=pattern.key,
            say="A router on the path was given egress delay",
            check="property_type('router', 'delay')"),
    ]


LINK_DELAY = Pattern(
    key="link-delay",
    title="A longer path, not a narrower one",
    source="Ch.16 §16.2",
    summary="Adding delay to a router on the path and seeing what a longer round trip changes.",
    observes=(
        "That a router in the network was configured with egress delay — that the student "
        "actually reached into a router and set it, rather than only reading about it."),
    choose_when=(
        "The activity asks the student to add latency, delay or jitter to a link, or to explore "
        "round-trip time, the bandwidth-delay product, or why a longer path slows a transfer "
        "without any link being made narrower."),
    not_covered=(
        "It observes NO measurement. Not the round-trip time, not a throughput drop, not a "
        "before-and-after comparison — those are claims about time, and this version of the "
        "system deliberately has none. The student measures and explains; the plan only witnesses "
        "that the delay was configured. Pair this with multi-lan so the network itself is "
        "observed too."),
    params={},
    build=_link_delay,
)


CATALOGUE = {p.key: p for p in (SINGLE_LAN, MULTI_LAN, LINK_DELAY)}


def get(key: str) -> Pattern:
    try:
        return CATALOGUE[key]
    except KeyError:
        raise KeyError(f"no certified pattern {key!r}; have: "
                       f"{', '.join(sorted(CATALOGUE))}") from None


def summaries(keys) -> list[str]:
    """The public, pattern-level guidance lines for a selection (design §11). This is the ONLY
    function that may feed a student-facing surface — expectation text never leaves the report."""
    return [get(k).summary for k in keys if k in CATALOGUE]


def catalogue_brief(patterns=None) -> str:
    """The whole catalogue in plain English — what the teaching AI is given to choose from.

    This is the model's entire view of what GINI can observe. It contains no probe syntax, no
    predicate names and no schema: the model's job is to understand a teacher's intent and match it
    against these descriptions, and everything after that is deterministic. Keeping the brief free
    of machine format is what makes the model's output space small enough to validate.
    """
    keys = sorted(patterns or CATALOGUE)
    return "\n\n".join(get(k).brief() for k in keys)

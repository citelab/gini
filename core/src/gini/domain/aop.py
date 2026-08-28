"""Activity Observation Plan — the schema, its hash, and the gate that keeps bad plans out.

An AOP is the fixed instrument a cohort is observed against: the teacher describes an activity, a
model *selects* certified patterns, and a deterministic assembler expands that selection into this
object. Students never see it. See ACTIVITY_OBSERVATION_PLAN_DESIGN.md for the whole design.

This module is the contract every other AOP component rides on, so it is deliberately the dullest:
dataclasses, a canonical serialization, a hash, and a validator. No Qt, no Docker, no model.

**Why the validator is a hard gate rather than a lint.** Plans are machine-generated. A malformed
probe that reaches a student does not announce itself — it evaluates to "not satisfied" and is read
as *the student didn't do the work*. Every rule in `validate()` exists to convert a silent
misattribution into a loud authoring failure, at the Teaching Center, before any code is minted.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field, replace

from . import objectives as _obj
from . import probes as _probes
from .devices import REGISTRY
from .proof import canonical_json

AOP_VERSION = 1

# An expectation's place in the stack. Drives evaluation order (cheap and foundational first) and
# the §8.1 cost split. Deliberately coarse: finer layering buys ordering precision nobody needs.
LAYERS = ("L2", "L3", "L4", "policy")

POSITIVE, NEGATIVE = "positive", "negative"
SENSES = (POSITIVE, NEGATIVE)

# Predicates whose arguments are element TYPE keys (mirrors objectives._TYPE_ARG_FUNCS). These are
# the ones whose tokens we can check against the palette.
_TYPE_ARG_FUNCS = frozenset(_obj._TYPE_ARG_FUNCS)

# Where slot scoping (`host@lanA`) is NOT yet honoured, and therefore must be refused rather than
# silently evaluated as false. Two known gaps, both recorded in the design note §5:
#   * probes `balances(...)` / `flow_installed(...)` — their regexes accept `\w+`, so `@` fails
#     to parse at all;
#   * objectives `through(...)` — `through_types()` compares `type_key` raw instead of using the
#     slot-aware matcher its siblings use, so a scoped token matches nothing and returns False.
# Delete each entry here as the corresponding gap is closed.
_NO_SLOT_SUPPORT = frozenset({"balances", "flow_installed", "through"})

# Predicates whose arguments are device NAMES the student chose (`linked("M1","S1")`). A free-form
# plan is written before any device exists and cannot know what anything will be called, so naming
# one is always a mistake — and a silent one, since the lookup simply finds nothing and reports
# unmet. Refused outright on a blank canvas; allowed when GINI composed the starter topology and
# therefore chose the names itself.
_NAME_ARG_FUNCS = frozenset(_obj._PREDICATES) - _TYPE_ARG_FUNCS

# Starting points (header param `starting_point`). "blank" is the free-form default: the student
# draws everything. "composed" means the activity hands out a starter topology GINI assembled.
BLANK, COMPOSED = "blank", "composed"


def _starting_point(header) -> str:
    return str((header.params or {}).get("starting_point", BLANK) or BLANK)

# --- the no-time rule (design §6.1) ---------------------------------------- #
# v1 has NO temporal expectations: no windows, no settling periods, no orderings in time. This is
# not a convention to be observed politely — a model asked for "check the routes converge within 30
# seconds" will cheerfully emit exactly that, so it is refused mechanically.
#
# Deliberately conservative. A false rejection costs the author a rephrase; a false acceptance ships
# an expectation nothing can evaluate. When these fire on something legitimate, the fix is to
# rewrite the expectation without the time, which is the point.
_TIME_WORDS = re.compile(
    r"\b(within|converg\w*|sustain\w*|settl\w*|elapsed|deadline|duration|timeout|"
    r"stabili[sz]\w*|eventually|afterwards)\b", re.I)
_TIME_LITERAL = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|second|seconds|"
                           r"m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b", re.I)


class AopError(ValueError):
    """An AOP that cannot be accepted. Carries the defects (see `Defect`)."""

    def __init__(self, defects) -> None:
        self.defects = list(defects)
        super().__init__("; ".join(str(d) for d in self.defects) or "invalid AOP")


@dataclass(frozen=True)
class Defect:
    """One reason a plan is rejected, addressed to whoever authored it.

    `where` is the expectation id (or "" for a header/plan-wide defect) so the Teaching Center can
    point at the offending row rather than printing a wall of text."""
    rule: str
    where: str
    detail: str

    def __str__(self) -> str:
        at = f" [{self.where}]" if self.where else ""
        return f"{self.rule}{at}: {self.detail}"


@dataclass(frozen=True)
class Observation:
    """How often, and on what, behavioural expectations are evaluated.

    This lives in the *plan*, not in the recorder's configuration, and that placement is the whole
    point: if two students' work were sampled on different schedules, identical work could produce
    different reports — the same bias a fixed instrument exists to remove, reappearing in time.
    Continuous fixed-cadence sampling is also what lets v1 drop temporal expectations entirely: a
    behaviour that takes 40s to settle is simply caught at the next tick.

    There is deliberately **no verification-strategy field**. An earlier draft carried one, offering
    a choice between inferring reachability from transitivity (cheap, n−1 probes) and probing every
    pair (sound, n(n−1) probes). Measurement dissolved the choice: batching a whole host's probes
    into ONE exec gives complete n(n-1) coverage in n process spawns, so the sound option is also
    the fast one. A setting whose second value should never be chosen is worse than no setting — it
    invites someone to choose it. See `domain.reach_strategy`.
    """
    cadence_s: float = 20.0
    behavioural_on: tuple = ("run_state", "cadence", "check")


@dataclass(frozen=True)
class Expectation:
    """One checkable claim about the activity.

    Exactly one of `probe` / `check` is set. They differ in cost by about six orders of magnitude —
    a `check` walks the in-memory graph, a `probe` shells into a container — which is what the
    evaluation split in §8.1 is built on.
    """
    id: str
    say: str                       # teacher-facing prose for the report; NEVER shown to a student
    layer: str = "L3"
    sense: str = POSITIVE
    probe: str = ""                # behavioural — needs a live lab
    check: str = ""                # structural — a predicate over the graph
    requires: tuple = ()           # ids that must hold first; gives ordering AND the `blocked` verdict
    pattern: str = ""              # which certified pattern emitted this

    @property
    def is_behavioural(self) -> bool:
        return bool(self.probe)

    def to_dict(self) -> dict:
        return {"id": self.id, "say": self.say, "layer": self.layer, "sense": self.sense,
                "probe": self.probe, "check": self.check,
                "requires": list(self.requires), "pattern": self.pattern}

    @classmethod
    def from_dict(cls, d: dict) -> "Expectation":
        return cls(id=str(d.get("id", "")), say=str(d.get("say", "")),
                   layer=str(d.get("layer", "L3")), sense=str(d.get("sense", POSITIVE)),
                   probe=str(d.get("probe") or ""), check=str(d.get("check") or ""),
                   requires=tuple(d.get("requires") or ()), pattern=str(d.get("pattern", "")))


@dataclass(frozen=True)
class Header:
    """What the plan is for, and how it is to be applied.

    `deadline_s` is the ONLY time bound anywhere in the system, and it scopes the whole session
    rather than any expectation (§4). Work past it is marked out-of-window in the report and is
    never discarded or evaluated differently.
    """
    intent: str = ""
    params: dict = field(default_factory=dict)
    patterns: tuple = ()
    answers: tuple = ()                 # ({"q":…, "a":…}, …) from the clarification loop
    created: float = 0.0
    gini_version: str = ""
    observation: Observation = field(default_factory=Observation)
    deadline_s: float | None = None
    guidance: tuple = ()                # pattern-level public summaries; NEVER expectation text

    def to_dict(self) -> dict:
        return {"intent": self.intent, "params": dict(self.params),
                "patterns": list(self.patterns), "answers": [dict(a) for a in self.answers],
                "created": self.created, "gini_version": self.gini_version,
                "observation": {"cadence_s": float(self.observation.cadence_s),
                                "behavioural_on": list(self.observation.behavioural_on)},
                # Numbers are coerced so an int and a float of the same value cannot hash
                # differently: `plan_hash` is only useful if a plan written here and the same plan
                # read back off disk agree, and JSON renders 3600 and 3600.0 as different text.
                "created": float(self.created),
                "deadline_s": None if self.deadline_s is None else float(self.deadline_s),
                "guidance": list(self.guidance)}

    @classmethod
    def from_dict(cls, d: dict) -> "Header":
        o = d.get("observation") or {}
        return cls(intent=str(d.get("intent", "")), params=dict(d.get("params") or {}),
                   patterns=tuple(d.get("patterns") or ()),
                   answers=tuple(dict(a) for a in (d.get("answers") or ())),
                   created=float(d.get("created", 0.0) or 0.0),
                   gini_version=str(d.get("gini_version", "")),
                   observation=Observation(
                       cadence_s=float(o.get("cadence_s", 20.0) or 20.0),
                       behavioural_on=tuple(o.get("behavioural_on")
                                            or ("run_state", "cadence", "check"))),
                   deadline_s=(None if d.get("deadline_s") in (None, "")
                               else float(d["deadline_s"])),
                   guidance=tuple(d.get("guidance") or ()))


@dataclass(frozen=True)
class Aop:
    """A frozen Activity Observation Plan."""
    header: Header
    expectations: tuple
    aop_version: int = AOP_VERSION

    def to_dict(self, *, with_hash: bool = True) -> dict:
        d = {"aop_version": self.aop_version, "header": self.header.to_dict(),
             "expectations": [e.to_dict() for e in self.expectations]}
        if with_hash:
            d["plan_hash"] = plan_hash(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Aop":
        return cls(header=Header.from_dict(d.get("header") or {}),
                   expectations=tuple(Expectation.from_dict(e)
                                      for e in (d.get("expectations") or ())),
                   aop_version=int(d.get("aop_version", AOP_VERSION)))

    def by_id(self, eid: str):
        for e in self.expectations:
            if e.id == eid:
                return e
        return None


def plan_hash(aop: Aop) -> str:
    """The plan's identity: sha256 over its canonical form, excluding the hash field itself.

    A ticket is minted against this, and a proof's genesis records it, so a submission can be
    verified against *the exact plan the student worked to* rather than whatever plan the
    instructor happens to have loaded. Regenerating a plan therefore always means new codes —
    which is the intent, not a limitation.
    """
    return hashlib.sha256(canonical_json(aop.to_dict(with_hash=False)).encode("utf-8")).hexdigest()


# -- token extraction -------------------------------------------------------- #
def _split_slot(token: str) -> tuple[str, str]:
    """`host@lanA` → ("host", "lanA"). An unscoped token has an empty slot."""
    base, _, slot = str(token).partition("@")
    return base, slot


def probe_tokens(probe: str) -> list[tuple[str, str]]:
    """(token, function) pairs naming element types in a probe string.

    Returns [] for an unparseable probe — parse failure is reported by its own rule, and one defect
    per problem reads better than a cascade.
    """
    try:
        p = _probes.parse(probe)
    except _probes.ProbeError:
        return []
    fn = p.kind
    if p.kind in (_probes.REACH, _probes.PING, _probes.HTTP):
        return [(p.src, fn), (p.dst, fn)]
    if p.kind == _probes.BALANCES:
        return [(p.src, "balances")]
    if p.kind == _probes.FLOW:
        return [(p.src, "flow_installed")]
    if p.kind == _probes.MEASURE:
        return [(p.src, "measure")]
    return []


def check_tokens(check: str) -> list[tuple[str, str]]:
    """(token, predicate) pairs naming element types in a structural check.

    Only the type-argument predicates are collected: `linked("M1","S1")` takes device *names* the
    student chose, which a plan cannot know about and must not constrain.
    """
    try:
        tree = _obj.parse_check(check)
    except _obj.PredicateError:
        return []
    out: list[tuple[str, str]] = []
    for node in ast.walk(ast.Expression(body=tree)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        fn = node.func.id
        if fn not in _TYPE_ARG_FUNCS:
            continue
        # Only the LEADING args of some predicates name element types — `property_type` takes a
        # property key and value after its type. `objectives._TYPE_ARITY` is the one place that
        # knows; reading it here keeps the two from drifting apart.
        for arg in node.args[:_obj._TYPE_ARITY.get(fn, len(node.args))]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append((arg.value, fn))
            elif isinstance(arg, ast.Name):          # bare token, e.g. exists(router)
                out.append((arg.id, fn))
    return out


def _named_predicates(check: str) -> list[str]:
    """Name-taking predicates used in a check, in source order and without duplicates."""
    try:
        tree = _obj.parse_check(check)
    except _obj.PredicateError:
        return []
    out: list[str] = []
    for node in ast.walk(ast.Expression(body=tree)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _NAME_ARG_FUNCS and node.func.id not in out):
            out.append(node.func.id)
    return out


def _mentions_time(text: str) -> str:
    """The offending fragment when `text` carries a time bound, else ""."""
    m = _TIME_WORDS.search(text or "") or _TIME_LITERAL.search(text or "")
    return m.group(0) if m else ""


# -- validation -------------------------------------------------------------- #
def validate(aop: Aop, *, known_types=None, observability=None) -> list[Defect]:
    """Every reason this plan must not reach a student. Empty list == acceptable.

    `known_types` defaults to the palette registry; pass a set to validate against a different
    catalogue (another domain — see design §14). `observability`, when supplied, must expose
    `possible(expectation) -> bool`: the §7.3 precondition oracle answering "could this *ever* be
    observed", which rejects e.g. a tshark expectation on a toolkit that has no tshark. It is
    optional so the schema can ship and be tested before that oracle exists.
    """
    types = set(known_types) if known_types is not None else set(REGISTRY)
    defects: list[Defect] = []
    add = lambda rule, where, detail: defects.append(Defect(rule, where, detail))  # noqa: E731
    free_form = _starting_point(aop.header) == BLANK

    if aop.aop_version != AOP_VERSION:
        add("version", "", f"plan is version {aop.aop_version}, this build speaks {AOP_VERSION}")
    if not aop.expectations:
        add("empty", "", "a plan with no expectations observes nothing")

    obs = aop.header.observation
    if obs.cadence_s <= 0:
        add("cadence", "", f"observation cadence must be positive, got {obs.cadence_s}")
    if aop.header.deadline_s is not None and aop.header.deadline_s <= 0:
        add("deadline", "", "a deadline must be positive, or null for no deadline")

    seen: set[str] = set()
    for e in aop.expectations:
        w = e.id or "<no id>"

        if not e.id:
            add("id", "", "every expectation needs an id")
        elif e.id in seen:
            add("duplicate-id", w, "two expectations share this id")
        seen.add(e.id)

        if not e.say.strip():
            add("say", w, "every expectation needs teacher-facing prose")
        if e.layer not in LAYERS:
            add("layer", w, f"unknown layer {e.layer!r}; expected one of {', '.join(LAYERS)}")
        if e.sense not in SENSES:
            add("sense", w, f"unknown sense {e.sense!r}; expected one of {', '.join(SENSES)}")

        # -- exactly one of probe / check --------------------------------- #
        if bool(e.probe) == bool(e.check):
            add("probe-xor-check", w,
                "set exactly one of probe (behavioural) or check (structural); "
                + ("both are set" if e.probe else "neither is set"))

        # -- they must parse ------------------------------------------------ #
        if e.probe:
            try:
                _probes.parse(e.probe)
            except _probes.ProbeError as exc:
                add("probe-parse", w, str(exc))
        if e.check:
            try:
                _obj.parse_check(e.check)
            except _obj.PredicateError as exc:
                add("check-parse", w, str(exc))

        # -- no time bounds anywhere (§6.1) --------------------------------- #
        for fieldname, text in (("probe", e.probe), ("check", e.check), ("say", e.say)):
            hit = _mentions_time(text)
            if hit:
                add("no-temporal", w,
                    f"{fieldname} carries a time bound ({hit!r}). v1 has no temporal "
                    f"expectations — state what must be true, not when or how fast.")

        # -- type tokens must exist, and slots must be honoured ------------- #
        for token, fn in (probe_tokens(e.probe) + check_tokens(e.check)):
            base, slot = _split_slot(token)
            if base and base not in types:
                add("unknown-type", w,
                    f"{fn}({token}) names {base!r}, which is not an element type")
            if slot and fn in _NO_SLOT_SUPPORT:
                add("slot-unsupported", w,
                    f"{fn}() does not resolve slot scoping yet, so {token!r} would silently "
                    f"match nothing; drop the @slot or fix {fn}() first")
            elif slot and free_form:
                # Slots are a COMPOSITION artifact: only compose.py ever tags a device, so on a
                # blank canvas every device has slot="" and `slot_match("", "lanA")` is False.
                # A scoped token in a free-form plan therefore matches nothing and reports unmet —
                # blaming the student for the plan's mistake. Say so at authoring time instead.
                add("slot-on-free-form", w,
                    f"{token!r} is slot-scoped, but this activity starts from a blank canvas, "
                    f"where the student's devices carry no slot and nothing would ever match. "
                    f"Use the plain type ({base!r}), with `all` when you mean every one of them.")

        # -- a free-form plan may not name devices --------------------------- #
        if e.check and free_form:
            for fn in _named_predicates(e.check):
                add("names-on-free-form", w,
                    f"{fn}() takes device names the student picked, which this plan cannot know; "
                    f"on a blank canvas it silently matches nothing. Use a type-based predicate "
                    f"({', '.join(sorted(_TYPE_ARG_FUNCS))}) instead.")

        # -- could this ever be observed? (§7.3, optional oracle) ----------- #
        if observability is not None and not observability.possible(e):
            add("unobservable", w,
                "no legal construction could ever satisfy this expectation's preconditions")

    # -- two expectations that can never disagree ---------------------------- #
    # Identical observation + identical sense means one measurement reported twice. In a plan built
    # from several patterns this is easy to introduce by accident and impossible to notice later:
    # the pair always agrees, so it looks like corroboration while being pure noise, and it doubles
    # the `docker exec` cost of a behavioural check. It is also a signal the author meant something
    # temporal ("still reaches, now that delay is on") which v1 cannot express — so saying it twice
    # is the closest they could get, and the honest answer is to say it once.
    by_observation: dict[tuple, str] = {}
    for e in aop.expectations:
        obs = (e.probe or e.check, e.sense)
        if not obs[0]:
            continue
        first = by_observation.setdefault(obs, e.id)
        if first != e.id:
            add("duplicate-observation", e.id or "<no id>",
                f"makes the same observation as {first!r} ({obs[0]}), so the two can never "
                f"disagree; keep one")

    # -- dependency graph ---------------------------------------------------- #
    ids = {e.id for e in aop.expectations if e.id}
    for e in aop.expectations:
        for dep in e.requires:
            if dep == e.id:
                add("self-dependency", e.id or "<no id>", "an expectation cannot require itself")
            elif dep not in ids:
                add("dangling-requires", e.id or "<no id>",
                    f"requires {dep!r}, which is not an expectation in this plan")
    for cycle in _cycles(aop):
        add("cyclic-requires", cycle[0], "dependency cycle: " + " -> ".join(cycle + [cycle[0]]))

    return defects


def _cycles(aop: Aop) -> list[list[str]]:
    """Every `requires` cycle, reported once each.

    A cycle is not merely invalid — it would deadlock evaluation ordering, and every expectation in
    it would report `blocked` forever with no root cause to point at. Iterative DFS so a
    pathological generated plan cannot blow the stack.
    """
    graph = {e.id: [d for d in e.requires if d != e.id] for e in aop.expectations if e.id}
    found: list[list[str]] = []
    seen_sets: list[frozenset] = []
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in graph}

    for root in graph:
        if colour[root] != WHITE:
            continue
        stack = [(root, iter(graph[root]))]
        path = [root]
        colour[root] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
                path.pop()
                continue
            if nxt not in graph:                  # dangling; its own rule reports it
                continue
            if colour[nxt] == GREY:               # back-edge → cycle
                cyc = path[path.index(nxt):]
                key = frozenset(cyc)
                if key not in seen_sets:
                    seen_sets.append(key)
                    found.append(cyc)
                continue
            if colour[nxt] == WHITE:
                colour[nxt] = GREY
                path.append(nxt)
                stack.append((nxt, iter(graph[nxt])))
    return found


def validate_or_raise(aop: Aop, **kw) -> Aop:
    """The plan, or `AopError` carrying every defect. The Teaching Center's gate."""
    defects = validate(aop, **kw)
    if defects:
        raise AopError(defects)
    return aop


# -- ordering ---------------------------------------------------------------- #
def evaluation_order(aop: Aop) -> list[Expectation]:
    """Expectations in the order they should be evaluated: dependencies first, lowest layer first.

    Bottom-up ordering is what makes root cause fall out for free. A failed L3 expectation sitting
    on a broken L2 one is not an independent finding, it is a consequence — so its dependency is
    evaluated first and it reports `blocked` rather than adding a second failure the teacher has to
    correlate by hand.

    A *global* layer preference, not a per-wave one. Ordering wave by wave (everything currently
    unblocked, sorted by layer, then the next wave) looks equivalent and is not: it interleaves
    patterns, so a plan combining two patterns reads as L2, L3, policy, L2, L3 — which is
    unreadable in a report and puts an L3 measurement before the L2 structure it rests on. Kahn's
    algorithm with a layer-keyed priority queue keeps every available low-layer expectation ahead
    of every higher one, subject to dependencies.

    Cyclic plans never reach this function (`validate` rejects them), but a cycle here degrades to
    "emit the remainder in layer order" rather than looping.
    """
    import heapq

    rank = {name: i for i, name in enumerate(LAYERS)}
    by_id = {e.id: e for e in aop.expectations if e.id}
    key = lambda e: (rank.get(e.layer, 99), e.id)                       # noqa: E731

    # Only dependencies present in the plan can gate anything; a dangling one has its own defect.
    deps = {e.id: {d for d in e.requires if d in by_id and d != e.id} for e in by_id.values()}
    dependents: dict[str, list[str]] = {i: [] for i in by_id}
    for eid, ds in deps.items():
        for d in ds:
            dependents[d].append(eid)

    outstanding = {eid: len(ds) for eid, ds in deps.items()}
    heap = [(*key(by_id[eid]), eid) for eid, n in outstanding.items() if n == 0]
    heapq.heapify(heap)

    out: list[Expectation] = []
    while heap:
        *_, eid = heapq.heappop(heap)
        out.append(by_id[eid])
        for child in dependents[eid]:
            outstanding[child] -= 1
            if outstanding[child] == 0:
                heapq.heappush(heap, (*key(by_id[child]), child))

    if len(out) < len(by_id):                       # a cycle held some back
        emitted = {e.id for e in out}
        out.extend(sorted((e for e in by_id.values() if e.id not in emitted), key=key))
    # Expectations with no id are invalid but must not silently vanish from a dry run.
    out.extend(e for e in aop.expectations if not e.id)
    return out


def with_expectations(aop: Aop, expectations) -> Aop:
    """A copy carrying different expectations — the assembler's building block. The hash follows
    automatically, since it is computed from content rather than stored."""
    return replace(aop, expectations=tuple(expectations))

# Link properties: cost and failure

**Status: agreed design (Mahesh, 2026-10-10). Not yet implemented.**

## The problem

A link in GINI is a bare edge: two endpoints, a label nothing displays, and a kind (cable or
"runs on" attachment). It has nothing to be configured with. So every path computation in GINI
is hop count — static routes are a breadth-first search over routers, and a distance-vector
script can only add 1 per hop because the router gives it nothing else to add. The classic
weighted-graph exercise, where the cheapest path is not the shortest, cannot be built. Nor can
a failure lab without someone typing `ifconfig down` on both ends at the right moment.

This design gives a link two properties — a **cost** and a **failure model** — and makes the
routers, the routing protocols the students write, the static routes and the multicast
forwarders all see them.

## Principles

1. **Cost is abstract.** It is an administrative metric — a price, a policy preference — and has
   no relation to delay or bandwidth. A costly link is not a slow link, and nothing in GINI
   derives one from the other. Real protocols sometimes tie cost to bandwidth (OSPF's reference
   bandwidth); GINI deliberately does not, for now. Every place cost appears says so.
2. **Delay, jitter and loss stay on the router.** The gRouter's ingress and egress delay lines
   already emulate wide-area behaviour and work; links do not duplicate them.
3. **The link is the source of truth, keyed by link id.** Interface numbers (`tunN`) and UDP
   ports come from the order links were drawn, so nothing is ever keyed by them. The compiler
   resolves a link to its two endpoints at compile time.
4. **A link has two ends, and both obey it.** A property is delivered to, and a failure is
   enforced at, both endpoints — router, machine or switch.
5. **One clock per link.** Failures are timed in gBuilder, so both ends fail at the same instant.
6. **Default is today.** Cost 1 and no failures reproduce current behaviour exactly; an old
   project opens and runs unchanged.

## The properties

### Cost

| | |
|---|---|
| Values | integer **1–15**; default **1** |
| Meaning | abstract routing cost of crossing this link, the same in both directions |
| Applies to | any link with a router on at least one end; on a cable into a switch, it is the router's cost onto that LAN |

The range matches RIP: 16 is RIP's infinity, so a **path** whose costs sum to 16 or more is
unreachable to RIP. That is RIP's real rule and part of the lesson, and the inspector's help says
so. A wider range (with infinity derived from the network) is possible later if a lab needs it.

### Failure

| Field | Meaning | Default |
|---|---|---|
| `FailAfter` X | mean time to failure, in seconds of running time; **0 = never fails** | 0 |
| `RepairAfter` Y | mean time to repair; **0 = stays failed until restored by hand** | 0 |

Both times are drawn from an **exponential distribution** with the given mean — the memoryless
classic for component failure. With Y > 0 the link alternates: up for an exponential time of mean
X, down for an exponential time of mean Y, up again — a link that flaps, which is the best test a
distance-vector protocol can get (withdrawal, count-to-infinity, reconvergence, repeatedly).

- Clocks start when the Run completes and stop when the lab stops. Each link draws
  independently.
- Draws come from a per-Run seed written to the log, so a run can be replayed. A topology may pin
  the seed, so a whole class sees the same failure schedule.
- The link's context menu offers **Fail now** and **Restore** for live demonstration. A manual
  restore draws the next failure time afresh.
- The clock lives in gBuilder: if gBuilder exits, no further failures occur.
- Configured **per link**, in the link's Properties tab in the inspector. There is no
  topology-wide default: a new link has no faults until someone gives it some.

## What a failure is

A failure is **carrier loss at both ends, at the same moment** — a pulled cable:

- **Router**: the interface goes down; nothing is sent or received on it, and its **connected
  route is withdrawn** (re-added when the link recovers), as a real router does when an interface
  loses carrier. The failure is visible in `route show`.
- **Machine**: its interface shows `NO-CARRIER` (the shuttle drops carrier on the TAP device),
  exactly what `ip link` shows on a real host when its cable is pulled.
- **Switch**: that port goes dead.
- **Canvas**: the link is drawn as failed; the activity log and the proof chain record
  `link_failed` and `link_restored`.

A silent cut (the link stays up but carries nothing) is out of scope for now; see below.

## Who reacts, and how

### The router hands links to Lua

The control-plane API is where a student's protocol meets the link:

- `interfaces()` entries gain `cost` and `up` (and `prefix`, so scripts stop assuming /24 — see
  Phase 0).
- New optional callback `on_link_change(iface, up, cost)`, called on the control thread when a
  link fails, recovers or changes cost. A protocol may react at once, or ignore it and detect the
  failure by timeout — and the difference is a lesson.
- `rip_reference.lua` changes `cost + 1` to `cost + <the interface's cost>`.

### Static routing

- **At Run**: paths are computed by Dijkstra over link costs instead of hop count.
- **On a cost change while running**: gBuilder recomputes the whole network and pushes only the
  differences to each router — a changed route is re-added (the router's `route add` replaces an
  entry for the same network in one step), a route no longer wanted is deleted, the rest is left
  alone. Each push is logged ("R2: 10.0.4.0/24 now via 10.0.3.2, cost 4").
- **On a failure: nothing.** Static routes keep pointing at the dead link and traffic
  black-holes. That is what static routing does, and it is the contrast a failure lab is built
  on. Recomputing on failure would make gBuilder a central routing controller — a different
  lesson. Floating static routes (a pre-installed backup at a higher distance, the real way
  static routing survives a failure) are a later addition.
- gBuilder remembers which routes it installed and only ever changes or removes those, so a
  route a student added by hand is never touched.
- A cost change while the lab is stopped just refreshes the inspector's Routes preview; the next
  Run uses it.

### Dynamic routing

On a cost change gBuilder sets the new cost on the interface at both ends and stops there; the
protocol picks it up (next tick, or at once through `on_link_change`). gBuilder computes no
routes in dynamic mode, as today.

### Multicast

- The built-in forwarder (`gpipe mcast join`) does not copy onto a down interface.
- `mcast_tree.lua` gets the same `up` flag and callback, so it can prune a dead branch; on a
  topology with a redundant path its per-tick re-announcements graft the tree the other way.
- A script building shortest-path trees (reverse-path forwarding) can use `cost`.

### Network HUD and path tracing

The HUD labels each link with its **cost only** — never delay, which would read as the same kind
of number and confuse the two. Failed links are drawn as broken. `trace_path` and "best path"
mean lowest cost.

## How properties reach the running lab

**At Run.** The compiler writes `link_id`, `cost` and initial state into each endpoint's port
entry in the runtime configuration (router `ifaces[i]`, machine `ifaces[i]`, switch `ports[k]`).
`run_grouter.py` turns them into `ifconfig add … -metric N` (and `ifconfig down` for a link that
starts failed). Values emitted into compose files stay numeric: the configs are embedded in
single-quoted YAML.

**Live.** One gBuilder service, `set_link(link_id, props)`, resolves both endpoints and uses each
kind's channel:

| Endpoint | Channel | Status |
|---|---|---|
| gRouter / OVS | the router control socket (as the Router Lab uses) | exists |
| switch | the fabric control socket (`runtime/control.py`) | exists; gains `port N up/down` |
| machine | a small control socket in the shuttle, on the same model | new |

## Router changes (C)

- `interface_t` gains `metric` (default 1), appended per the struct's own rule.
- `ifconfig add` and `ifconfig mod` accept `-metric N` (1–15); `ifconfig show` appends it as a
  column (existing parsers tolerate trailing columns).
- A control-plane service `iface_info(iface)` returning cost, up and prefix; `interfaces()` uses
  it. A control-plane event queue raised by up/down/metric changes and delivered on the control
  thread as `on_link_change` — never entering Lua from the CLI thread.
- `route del -net X -netmask Y`, so an automated push never depends on table indices, which shift.
- The built-in multicast forwarder skips down interfaces.
- An interface going down, for any reason (a link failure or `ifconfig down`), withdraws its
  connected route; coming up re-adds it. This needs the interface's netmask (Phase 0). Static and
  dynamic routes through the interface are not touched by the router: static ones black-hole by
  design, dynamic ones are the protocol's to withdraw. The `ifconfig` help page changes with it —
  it documents today that routes stay.

## Phases

**Phase 0 — groundwork. No visible change except fixes.**
- Loading tolerates unknown link fields and keeps them. This ships first, in gini-core, so a
  newer project file never crashes an older gBuilder (today `Link(**l)` raises on any unknown
  key: remote run server, Teaching Center markers, students who have not upgraded).
- Paths that rebuild links from endpoints carry properties through: fragments
  (`fragment_manager`), `compose.py`, `staging.py`, and Wizard recipes (whose links are unpacked
  as exact pairs). These already drop the link label today.
- Router "down" made sound: keep the receive thread and discard while down (no stale burst on
  up, no asynchronous cancel inside `malloc`); free the packets dropped on a down interface
  (they leak today); make `up` idempotent (a second `up` starts a second reader, after which
  `down` no longer stops reception).
- The router learns each interface's netmask, so `send()` and scripts stop assuming /24.
- The Router Lab's delay settings are re-applied at Run (today they are lost on restart).

**Phase 1 — the properties.** `Link.props`; selecting a link opens it in the inspector (selection,
`link_changed` signal, deferred rebuild as the device editor does); cost on the canvas; AI and MCP
tools to read and set link properties; proof events; compiled into each endpoint's configuration.

**Phase 2 — cost.** Router metric, Lua `cost`, Dijkstra for static routes, live recompute and
diff push, HUD cost labels, `rip_reference` uses cost.

**Phase 3 — failure.** gBuilder's failure clocks (exponential, seeded), carrier loss at all three
endpoint kinds, `on_link_change`, Fail now / Restore, multicast skip, failed-link drawing.

Each phase ships when its own tests pass, gini-core first with the version floor raised, and
gBuilder warns when the router image is older than the feature (an old image would silently
ignore the new fields).

## Tests

End-to-end on real routers, in the style of `rip_test.py` and `bcast_mcast_test.py`:

- **Cost**: a topology with two paths where the cheaper one is longer in hops. Static mode and
  `rip_reference.lua` must both choose it; changing a cost while running must move static
  routes (diff only) and RIP routes alike.
- **Failure**: cut the cheap path. RIP fails over within its timers and recovers on repair;
  static mode black-holes, by design; a machine on a failed link shows `NO-CARRIER`; a flapping
  link (RepairAfter > 0) drives repeated withdrawal and reconvergence.
- **Compatibility**: a project file with link properties opens in the previous release's loader
  logic (unknown keys kept); an old file opens with defaults; recipes, fragments and composed
  labs keep properties.
- The failure clock alone, with a pinned seed, produces a known schedule.

## Out of scope, deliberately

- Delay, jitter, loss and bandwidth on links — they stay on the router.
- Different cost in each direction (OSPF-style per-interface cost).
- Costs above 15.
- Silent cuts (link up, carrying nothing — the failure mode behind a switch, detectable only by
  timeout).
- Floating static routes and recomputing static routes on failure.

## Decisions

Agreed with the maintainer on 2026-10-10:

- Cost is abstract, integer 1–15, default 1, the same in both directions; the HUD shows cost only.
- Delay, jitter and loss stay on the router's ingress and egress.
- Failure: exponential time to failure (mean `FailAfter`, 0 = never) and to repair (mean
  `RepairAfter`, 0 = stays down until restored); configured per link in its Properties tab; no
  faults by default.
- A link failure is carrier loss at both ends, and the router withdraws the connected route.
- Static routes are recomputed on a cost change, not on a failure.

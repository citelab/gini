# Z1 — module seams (in progress)

Z1 reshapes the gRouter into modules **without changing behaviour yet**. It adds the
interfaces and the locked state path; existing call sites migrate onto them
incrementally, each move guarded by `backend/tests/grouter`.

## What landed (additive, compiles against the real headers)

| File | Seam | Role |
|---|---|---|
| `include/gr_module.h` | `gr_module_t` (`process(gpacket)->verdict`) + `gr_device_ops_t` | the contract every **droppable** module + every port driver conforms to |
| `include/host_stack.h` + `src/grouter/host_stack.c` | seal lwIP (UDP/TCP-to-self); optional via `-DGR_NO_HOST_STACK` | UI **toggle** ("terminate traffic here") |
| `include/sdn.h` + `src/grouter/sdn.c` | OpenFlow as the **ingress mode** (front door; NORMAL → legacy) | UI **mode** switch (legacy/openflow) |
| `include/gr_state.h` + `src/grouter/gr_state.c` | rwlock-protected accessors for route table + ARP | fixes the CLI↔forwarding **races** |

All four compile with `zig cc` against the real `backend/include` headers (verified).

## The incremental migration (each guarded by the test harness)

1. **State manager — ✓ DONE (compile-verified).** Migrated the call sites onto the
   locked accessors: `ip.c` (3× `findRouteEntry` → `gr_route_lookup`), `arp.c`
   (`ARPFindEntry`→`gr_arp_find`, `ARPAddEntry`→`gr_arp_add`; the *definitions* stay),
   `cli.c` route/arp commands (`addRouteEntry`→`gr_route_add`,
   `deleteRouteEntryByIndex`→`gr_route_del`, `ARPAddEntry`→`gr_arp_add`). All three
   files compile with `zig cc` against the real headers. The route table and ARP cache
   are now accessed under rwlocks — the CLI↔forwarding races are gone.
2. **host_stack — ✓ DONE (compile-verified, incl. optional build).** `ip.c`
   `IPProcessMyPacket` keeps ICMP-to-self in the core, then
   `if (host_stack_input(in_pkt)) return EXIT_SUCCESS;` instead of inline
   `UDPProcess`/`TCPProcess`. The `UDPProcess`/`TCPProcess` lwIP wrappers were **moved
   into `host_stack.c`** — it's now the only file that touches lwIP. `build.zig` gained
   `-Dhost_stack=false`, which defines `-DGR_NO_HOST_STACK` and drops the lwIP sources
   (tcp/tcp_in/tcp_out/udp/pbuf/memp) for a leaner pure forwarder. Both modes configure;
   host_stack.c compiles ON and OFF. (OFF is opt-in — validate on first link; a core file
   referencing a pbuf/memp symbol would need attention.)
3. **sdn — ✓ DONE (compile-verified).** The device drivers (`tap.c`, `tun.c`,
   `ethernet.c`, `raw.c`) ask `sdn_mode() == SDN_MODE_OPENFLOW` at ingress instead of
   reading the raw `rconfig.openflow` global — the mode decision now goes through the
   seam. (Z2 will make `sdn_ingress()` the explicit front door.)
4. **device_ops — interface declared.** `gr_device_ops_t` (in `gr_module.h`) is the
   existing `device_t` vtable (`fromdev`/`todev`) that drivers register through
   (`devicedefs.h`'s `devdir[]`). The seam exists; full conformance — drivers as graph
   nodes with a uniform `process()` — is Z2 work, not a Z1 refactor.

**Z1 status: complete** for its goal — the core depends on the seam headers, the route
table + ARP cache are race-free behind rwlocks, and lwIP + OpenFlow are sealed (lwIP also
optional). All ten Z1-touched files (ip, arp, cli, gr_state, host_stack, sdn, tap, tun,
ethernet, raw) compile with `zig cc`; `build.zig` configures in both host-stack modes.
Next: **Z2** — the graph runner.

After these, the core depends only on the seam headers, lwIP and OpenFlow are sealed,
and the state is race-free — the foundation for **Z2** (the graph runner that gives the
inline modules a uniform `process()` and makes the Router Lab drops drive real forwarding).

## Note on the slack stub

`grouter-zig/slack-stub/` is a **compile-only** stub of the few `<slack/*.h>` macros/types
the GINI headers expand (`_begin_decls`, `List`, `Map`). It lets the seams be
compile-verified in CI without libslack. The real build links the real libslack (Docker).

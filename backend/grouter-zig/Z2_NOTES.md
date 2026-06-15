# Z2 — the module-graph runner (started)

Z2 gives the inline modules a uniform `process()` and a runner that chains them, so the
Router Lab's drag-and-drop maps directly onto real packet processing.

## What landed (runnable, tested)

| File | What |
|---|---|
| `include/gr_pipeline.h` + `src/grouter/gr_pipeline.c` | the runner: an ordered series of `gr_module_t`; `gr_pipeline_run()` walks them with verdict semantics (CONTINUE → next; DROP/FORWARD/TO_HOST/CONSUMED → terminal) |
| `include/gr_modules.h` + `src/grouter/gr_modules.c` | built-in inline modules conforming to the ABI: `gr_mod_acl()` (firewall/drop) and `gr_mod_counter()` (tap/log) |
| `grouter-zig/tests/test_pipeline.c` | **runs in CI (libc only):** ACL drops a denied dst (counter after it never fires), allowed dst CONTINUEs to base forwarding, and reordering proves order matters |

Result of the test:
```
dst 10.0.3.10 (denied)  -> DROP
dst 10.0.9.9  (allowed) -> CONTINUE (-> base forwarding), counter=1
reordered [counter, acl], denied pkt -> DROP, counter=1
Z2 pipeline runner: ALL PASS
```

The runner is pure `gpacket_t` + libc — no globals, no slack — so it links and runs on
its own. Added to `build.zig` (CORE_SRCS).

## The Router Lab maps onto this 1:1

The editor's ordered inline list = a `gr_pipeline`. "Drop a module" = `gr_pipeline_add`.
Reorder = reorder the array. The step-through debugger = call `gr_pipeline_run` one
module at a time and report each verdict. The local Python trace in `domain/router_modules.py`
is the stand-in until the control protocol exposes the real pipeline.

## Z2 increments — status

1. **Integrate — ✓ DONE (compile-verified).** `ip.c`'s `IPProcessForwardingPacket` runs
   `gr_pipeline_run(gr_default_pipeline(), in_pkt)` after parse, before route lookup:
   `DROP` → drop; non-`CONTINUE` terminal → the module owns it; `CONTINUE` (incl. the
   empty pipeline) → base forwarding unchanged. Legacy / NORMAL path; OpenFlow stays the
   ingress front door. `gr_default_pipeline()` is the process-wide pipeline.
2. **Control surface — ✓ DONE (runs in CI).** `gr_control.c` (`include/gr_control.h`)
   parses `add acl <cidr> | add counter | list | clear | trace <ip>` against the default
   pipeline; `trace` is the step-through debugger. `cli.c` gained a thin `gpipe` command
   that forwards to it. `test_control_z2.c` runs it end-to-end (ALL PASS).
3. **Lua `script` module — ✓ DONE (compile-verified; Docker-linked).** `gr_mod_lua.c`:
   a `gr_module_t` calling a student `process(pkt, ctx)` Lua hook (fields: `dst`, `proto`;
   verdicts `CONTINUE/DROP/CONSUMED/TO_HOST/FORWARD`). Built with `zig build -Dlua=true`
   (adds the source + links `lua5.4`); Docker needs `liblua5.4-dev`.
4. **More modules — ✓ DONE.** `gr_mod_nat(snat_ip)` (source-IP rewrite + IP checksum;
   in gr_modules.c, runnable-tested) and `gr_mod_filter()` (the legacy firewall as a
   module, wrapping `filteredPacket(filter,…)`; in `gr_mod_legacy.c`, built with
   `GR_LEGACY_MODULES`). Both exposed via `gpipe add nat|filter`. (Classifier/QoS stays
   in the packetcore scheduler — not an inline verdict module, by design.)

**Router Lab GUI bound to the pipeline (✓).** The Python runtime router speaks the same
`gpipe` vocabulary over its control socket (`grouter.py._gpipe`, tested). The Router Lab,
when the topology is running, drives the REAL router: `command_fn` sends `gpipe clear / add … / trace`
via the per-element console (`element_query`) and shows the router's response; offline it
uses the local trace. The C gRouter's `gr_control` speaks the identical vocabulary, so
swapping the real C router into the fabric needs no GUI change.

Runnable proof: `test_pipeline.c` (+NAT) and `test_control_z2.c` pass (libc only); the
frontend `test_control.py` exercises `gpipe` over the Python router's socket; a GUI smoke
confirms the live binding. All Z2 files compile with `zig cc`; `build.zig` configures
with `-Dhost_stack`, `-Dlua`, `-DGR_LEGACY_MODULES`. **Z2 complete.**

# SDN controller apps: what is exposed, and what is still deferred

**Status: partly implemented (2026-09-02). The App field is done; two items below are open.**

This is the place to look up what GINI's OpenFlow controller can be told to run, and what is
known to be missing or wrong about it. It exists because the two deferred items below are both
*invisible* — nothing on screen or in a log would ever mention them — so without a written record
they are lost rather than postponed.

## How the App field works now

The controller element has one `App` property. It is a **POX command line**, not a value from a
fixed set: several modules separated by spaces, each optionally carrying its own `--flags`.
`backend/sdn/run-pox.sh` deliberately leaves `$POX_APP` unquoted so the shell word-splits it, and
POX binds each flag to the module *preceding* it.

The Inspector renders it as a **text field with a preset picker beside it**
(`DeviceType.open_properties`). The listed entries are starting points, and each GINI app is
listed with its parameters at their own defaults — so the picker is the documentation:

    gini.samples.ids --threshold=10 --block=false

Writing a default out is exactly equivalent to omitting it. POX only runs argument values through
`ast.literal_eval` for a launch function decorated with `_pox_eval_args`, and none of the GINI
apps are — so every value arrives as the plain string the signature already defaults to.

That last point is load-bearing rather than trivia: `--sequence=1111,2222,3333` is valid Python
for a *tuple*, and under `eval_args` it would reach `port_knock`'s `sequence.split(",")` as one
and crash.

**No validation on GINI's side, deliberately.** POX refuses an unknown flag by name and prints the
module's real parameters with their defaults and current values, which is a better message than
anything restated here could be — and one that cannot drift from the app it describes.

`tests/test_controller_app.py` pins the presets against the apps' actual `launch()` signatures, so
renaming a parameter in `backend/sdn/pox/ext/gini/samples/` fails the suite rather than shipping a
dropdown entry POX will reject.

## The apps

| app | parameters | defaults |
|---|---|---|
| `switch` | — | L2 learning switch; the default |
| `sfc` | — | service function chain — **not offered, see below** |
| `packet_loss` | `loss` | `0.3` |
| `ids` | `threshold`, `block` | `10`, `false` |
| `port_knock` | `server`, `port`, `sequence` | `10.0.1.10`, `23`, `1111,2222,3333` |
| `l4_lb` | `vip`, `backends` | `10.0.1.100`, `10.0.1.11,10.0.1.12` |
| `redirect` | `server`, `port`, `vnf` | `10.0.1.10`, `80`, `10.0.1.20` — **see below** |

The address defaults are not arbitrary: `services/compiler.py` numbers segments
`10.0.{n+1}.0/24`, routers and VNFs from `.1`, and hosts from `.10`. So on a single-subnet
topology `port_knock --server=10.0.1.10` really is the first machine, and `l4_lb`'s backends
really are the next two.

Note the corollary — on a **multi-subnet** topology the defaults point at the wrong segment, and
the app will run happily while doing nothing visible. That is now fixable by editing the field,
which is the whole reason it was opened up.

---

## Deferred 1 — `sfc` is not offered in the dropdown

`backend/sdn/pox/ext/gini/samples/sfc.py` ships in the image and is reachable by typing
`gini.samples.sfc`, but it is not in `property_choices`, so nobody would discover it.

Not added yet because it has not been exercised on a current topology, and putting an untried app
in front of students is worse than leaving it out. What is needed before listing it:

- run it on a topology with a VNF in the path and confirm it still chains correctly;
- decide whether it needs parameters at all — `launch()` takes none today, which for a *service
  chain* is suspicious: the chain members look hard-coded.

`tests/test_controller_app.py::test_an_app_with_parameters_shows_them` deliberately skips apps
that are not listed, so adding `sfc` to the dropdown will not fail the suite — but if it is added
*with* parameters, the drift tests will start covering it automatically.

## Deferred 2 — `redirect`'s `--vnf` default is probably wrong

`redirect` defaults to `vnf=10.0.1.20`. The compiler assigns VNF addresses in the same pass as
routers — `.1`, `.2`, … — while `.10` upward belongs to hosts. So a generated topology never has
a VNF at `.20`, and the default cannot match anything GINI builds.

It has been that way for as long as the app has existed, and it was harmless while invisible.
Now that the dropdown prints it, it is a wrong answer in front of every student who opens the
list — which is an argument for fixing it, not for hiding it again.

Not changed yet because the right value depends on a decision nobody has made: whether `redirect`
should default to *the first VNF on the segment* (`10.0.1.2`, matching how the compiler numbers
them) or keep a value that says "you must set this", in which case the default should be something
obviously unset rather than something plausibly real.

To settle it, build a topology with a VNF and read the address the compiler gives it — that is the
number the default should be, if it is to have one.

---

## Related, and easy to trip over

**The field is a QLineEdit, not an editable QComboBox** — and that is not a style choice. An
editable combo was tried first and crashed on the *first keystroke*: `currentTextChanged` fires
per character, each commit emits `device_changed`, and the inspector rebuilds on that — deleting
the widget being typed into ("Internal C++ object (QComboBox) already deleted"). With a lab
running it also restarted the POX container once per character. `Inspector._on_changed` already
defers its rebuild to survive a discrete change; nothing survives being rebuilt on every letter.

So the field commits on `editingFinished`, exactly like every other text property in that form,
and the presets live in a separate `QToolButton` menu that writes into it. The menu also sizes to
its entries, so a long POX command line is readable in full — a dropdown constrained to the form's
column would elide exactly the parameters the entry exists to show.

**Flags bind to the module before them.** With `App = "openflow.discovery forwarding.l2_multi"`,
appending `--threshold=5` gives it to `l2_multi`, not `discovery`. POX refuses it and names the
module, so it is discoverable rather than dangerous — but it is not obvious.

**The controller restarts when App changes.** `main_window._live_ctrl_app` compares the whole
string and calls `set_controller_app`, which rewrites `POX_APP` in the compose file and restarts
the container. Editing a parameter therefore applies live; the switches reconnect and discovery
needs a few seconds. Any future change that splits App into several properties **must** fold them
all into that comparison, or editing a parameter alone becomes a silent no-op until Stop/Run.

**Two writers escape the value.** `POX_APP` is written into the generated compose file in two
places — `_compose` and `set_controller_app` — and both go through `orchestrator._yamlish`, which
doubles `'` (it would end the YAML scalar) and `$` (Compose substitutes it before the YAML is
parsed). Neither character appears in a plausible POX argument, which is exactly why it would go
unnoticed until the one student who typed one.

**`backend/sdn/README.md`** documents every app's flags, and is the source this page's table was
checked against.

# gini-core

The shared foundation of [GINI](https://github.com/citelab/gini): the topology model and the
tamper-evident **proof format**. Pure Python, **no Qt**, no container runtime.

You do not normally install this yourself — `gini-toolkit` (the gBuilder desktop app) and
`gini-teaching-center` (the course server) both depend on it.

```bash
pip install gini-core
```

## Why it is its own package

The Teaching Center runs on a headless VM and must verify student submissions. Verification needs
the proof format; it does not need a GUI. Without this split, installing the course server would
drag ~400MB of PySide6 onto a server that never draws a pixel — so the server install is 2.3MB
instead.

The second reason matters more. gBuilder *writes* proofs and the Teaching Center *verifies* them.
Those two implementations must agree exactly, forever — a hash chain that disagrees by one field is
a student's work rejected. Keeping the format in one distribution both sides depend on means there
is no second copy to drift.

## What is in it

```
gini/domain/
  topology.py     Topology, DeviceInstance, Link — the model gBuilder saves and loads
  devices.py      the device/element registry (networking + cloud)
  proof.py        the hash chain, ticket codes, and receipts
  persistence.py  the on-disk .gini project format
```

**`gini.domain.proof`** is the interesting one. A proof is an append-only chain in which every
entry commits to the one before it, so a recorded session cannot be edited after the fact without
breaking the chain. It is bound to the activity code it was recorded under, and its `submit` entry
carries a SHA-256 of the topology — which is what makes a stolen project file worthless: the file
and the code are cryptographically the same claim.

The **receipt** a student reads off the screen is 40 bits of the chain's MAC, rendered as
`XXXX-XXXX`. gBuilder and the server derive it identically from the proof itself, so a receipt
handed to an instructor before the upload lands is still the right receipt after it does.

## Namespace package

`gini` is an implicit **namespace** package shared with `gini-toolkit`: this distribution owns
`gini.domain`, the app owns `gini.ui`, `gini.services` and the rest. Neither ships a
`gini/__init__.py` — adding one to either would shadow the other and break the import.

## License & links

Part of the GINI project — https://github.com/citelab/gini

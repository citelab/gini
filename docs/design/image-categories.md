# Pulling only the images a student needs

**Status: proposal (Mahesh + Claude, 2026-09-03). Targeted at 6.7.0. Not implemented.**

First run pulls all four custom images before a student can Run anything. That is **2.16 GB**, and
which of it any given student needs depends entirely on their course. A networking student waits
for 1.85 GB they will never open; an OS student waits for 310 MB they will not.

The proposal: let the first-run panel offer the images in categories, so each student takes what
their course actually uses.

## What each image costs and unlocks

Measured from a real pull of 6.5.2 on arm64:

| image | size | palette elements it enables |
|---|---|---|
| `gini-grouter` | 154 MB | Router, OpenVSwitch |
| `gini-pox` | 156 MB | OpenFlow Controller |
| `gini-xv6` | 1.02 GB | xv6 Machine |
| `gini-oszoo` | 830 MB | FreeDOS, KolibriOS, MenuetOS, MS-DOS, Mac 7, Win 3.1, Classic OS (BYO) |

Networking alone is 310 MB — **14%** of what is pulled today.

## Two findings that shape the work

### There is no "basic" set that must always come down

The floor is **zero**, not "some basics". A plain two-machine LAN needs none of these four:
Machine and fabric images are *built locally* at Run time from the Dockerfiles
`orchestrator.write_project` writes into the working directory, not pulled from the registry.
`setup/images.py::BUILD_SPECS` covers only these same four, for source checkouts.

So every one of the four is genuinely optional, and a student who wants only Machines and Switches
can skip setup entirely.

### `xv6` and `oszoo` have no Run-time guard — and that is the actual work

`Orchestrator.up()` checks two images before starting anything:

```python
if config.routers or config.ovs_switches:   # routers & OVS use the gRouter image
    ok, msg = self._ensure_grouter_image()
if config.controllers:                       # SDN controllers use the POX image
    ok, msg = self._ensure_pox_image()
```

Both end in a sentence telling you how to fetch it. **Nothing guards `gini-xv6` or
`gini-oszoo`.** The compiler simply writes `image: gini-xv6:latest` and `image: gini-oszoo:latest`
into the compose file, so if either is absent `docker compose up` fails with a raw Docker error.

That path is unreachable today because everyone has all four. Make the pull selective and it
becomes **the main way a student discovers they skipped a category** — as an inscrutable Compose
error rather than "the OS Zoo images are not on this machine; fetch them with …".

So the guards are the load-bearing half of this feature, not the checkboxes. Do them first; they
are worth having even if the categories are never built.

## Three decisions still open

### 1. What the categories are called

Proposed, named after their contents so they map onto what a student sees in the palette:

```
  Networking   310 MB   Routers, OpenVSwitch, OpenFlow controllers
  xv6          1.0 GB   The xv6 teaching kernel
  OS Zoo       830 MB   MS-DOS, Win 3.1, Mac 7, FreeDOS, KolibriOS, MenuetOS
```

The original sketch had Networks / OS / **Other**, with the Zoo as "Other". Avoided here: "Other"
never ages well — the next image has nowhere obvious to go — and the Zoo is arguably OS too. The
distinction that matters is not importance but *content*: xv6 is the kernel students modify, the
Zoo is museum operating systems under emulation.

### 2. What is ticked by default

This decides whether the feature saves anything in practice.

| default | saves | risk |
|---|---|---|
| all three ticked | nothing, unless a student unticks | none; identical to today |
| Networking + xv6 | 830 MB for everyone | Zoo users take one extra step |
| nothing ticked | up to 2.16 GB | a student may pull nothing and meet the Run-time message first |

**Recommended: Networking + xv6.** Most students never open the Zoo, and once the Run-time guards
exist, "nothing ticked" is safe too — just a larger behaviour change to the one screen every new
student meets.

### 3. How a declined category is remembered

`bootstrap.plan()` currently asks `missing_locally(image_refs(version))` — *all four* — and offers
setup when any is absent. With categories that must become "missing from what was chosen", or the
panel nags for ever about a Zoo the student deliberately declined.

`marker.write_marker` already records `images: [what landed]`. The cheapest honest version is to
record the declined set alongside it, and have the panel present those as *also available* rather
than as an unfinished setup. Note `marker.is_setup_done()` is `bool(read_marker().get("images"))`,
which stays correct — one category counts as done.

## Scope

- a category map in `setup/` (image → category, with sizes for the panel)
- checkboxes in `ui/first_run.py`, with sizes shown
- `plan()` / marker comparing against the chosen set
- **two Run-time guards** for xv6 and oszoo, each naming the category to fetch
- `gini-setup --only networking,xv6` for the CLI path

## Why this was deferred out of 6.6.0

6.6.0 was ready and tested; this touches first run, the one screen every new student meets, and
wants a testing pass on a machine where an image can actually be deleted and the message watched.
Shipping the finished thing beat holding it for the unfinished one.

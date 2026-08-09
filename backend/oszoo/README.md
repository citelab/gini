# gini-oszoo — the OS Zoo container

One image that boots a real historical OS under emulation and serves its screen as a web page
(noVNC), so gBuilder's **Zoo Lab** can embed it. The guest is chosen at run time by `ZOO_OS`, so
the same image serves every OS Zoo palette element and `docker compose` builds it once.

## Build (Mac / desktop)

```
docker build -t gini-oszoo:latest .
```

The compiler emits this image for every OS Zoo element (`freedos`, `plan9`, `reactos`,
`oszoo_byo`), publishing container port **6080** (the noVNC web console the Zoo Lab embeds) and
**5900** (raw VNC).

## Run-time knobs (set by the compiler from the element's properties)

| env | meaning |
|---|---|
| `ZOO_OS` | `freedos` \| `kolibri` \| `menuet` \| `byo` — which guest to boot |
| `ZOO_PERSIST` | `0` ephemeral (default) \| `1` keep changes in a qcow2 overlay |
| `ZOO_EMULATOR` | BYO only — `qemu` (x86) \| `dosbox` (DOS/Win 3.x) \| `basilisk` (68k Mac) |
| `ZOO_ARCH` | `x86` \| `x86_64` \| `68k` (BYO only) |
| `ZOO_IMAGE` | BYO only — the student's image/folder, bind-mounted read-only at `/zoo/byo.img` |
| `ZOO_ROM` | BYO Basilisk only — a Mac ROM, bind-mounted read-only at `/zoo/rom` |

**Two display pipelines.** QEMU guests use QEMU's built-in VNC server. DOSBox and Basilisk II are
SDL apps with no VNC, so they render into a virtual X display (`Xvfb`) that `x11vnc` serves on
:5900 — the same port websockify bridges. One guest per container either way.

Full OSes are **freely redistributable** and fetched on first boot into `/zoo/cache` (bind-mounted
to `~/.gini/oszoo-cache` on the host, so every guest downloads once). URLs are pinned in `boot_zoo.sh`:

| OS | image | media | first-boot download |
|---|---|---|---|
| KolibriOS | `builds.kolibrios.org/en_US/latest-img.7z` → `kolibri.img` | floppy | ~1.3 MB (assembly GUI; boots in seconds) |
| MenuetOS | archive.org `menuetos/M32-086B.IMG` (GPL 32-bit) | floppy | ~1.4 MB (assembly GUI; boots in seconds) |
| FreeDOS | ibiblio `1.4/FD14-LiveCD.zip` → `FD14LIVE.iso` | CD | ~280 MB (LiveCD; live DOS prompt) |

All are x86 and boot fast even under software emulation. (Plan 9 and ReactOS were removed: full
32-bit desktops are painfully slow without CPU acceleration, which Docker-for-Mac can't provide.)

**Bring-your-own fast vintage:** DOSBox (Windows 3.x) and Basilisk II (System 7 / Mac OS 8 on 68k)
are the *fast* routes for real vintage Windows/Mac — both use purpose-built emulators, not
full-system x86, so they stay quick. You supply the image (and, for Mac, a ROM); GINI boots a
writable working copy so your original is never touched.

Gotchas already handled in `boot_zoo.sh`:

- **All fetches use curl's default User-Agent** — do not send a browser UA (ibiblio doesn't need
  one, and some mirrors *403* it).
- **ibiblio** `1.3/official/` path 403s programmatic downloads → we use the `1.4/` path.
- **FreeDOS** LiteUSB/FullUSB images are *installers* that loop in Setup → we use the **LiveCD**,
  whose isolinux `DEFAULT live` entry boots a live DOS session with no install. (50 s menu timeout
  before auto-booting live; press Enter to skip.) It needs ~1 GB RAM for its writable C: RAM drive.
- **KolibriOS** ships as a `.7z` holding `kolibri.img`; the image bind-mounts `/zoo/cache` to the
  host (`~/.gini/oszoo-cache`) so every guest downloads once. `p7zip-full` is installed to unpack
  the `.7z`; the floppy boots with `-fda … -boot a`.

A failed or incomplete download never takes the container down: `boot_zoo.sh` still launches
QEMU (with no disk, so you see a "no bootable device" screen) and still starts noVNC, so the Zoo
Lab always shows a screen and the reason in the container logs — instead of "connection refused".
Bump a URL in the table to move to a newer release.

## Legal posture

GINI ships only freely-redistributable images (FreeDOS, 9front/Plan 9, ReactOS). Proprietary
OSes (Windows 95, Mac System 7) never appear here: they arrive as `ZOO_OS=byo` with an image the
**student** supplies and legally owns (`ZOO_IMAGE`), bind-mounted read-only. GINI hosts nothing
copyrighted and auto-fetches no proprietary image or ROM. See `../../OS_ZOO_DESIGN.md` §8.

## v1 vs v2

v1 is **display-only** (`-net none`). v2 adds fabric networking (`-netdev tap` onto the GINI
fabric via the TAP↔UDP shuttle; the per-OS `nic` in the table is already the right adapter for
each guest's built-in driver).

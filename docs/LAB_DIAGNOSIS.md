# One machine works, thirty do not — finding out why

`tr-open-12` runs GINI. The machines rolled out after it mostly do not, and they all have podman
and podman compose. "Nothing is special about tr-open-12" is almost certainly true — and that is
the point: whatever differs is something nobody chose, so nobody remembers it.

`scripts/gini-doctor.sh` collects the same ~80 facts on every machine and then shows only the
ones that disagree. It needs nothing installed: `--fanout` pipes the script itself over ssh.

## Run it

**As the user who runs gBuilder, not as root.** Half of what matters here is per-user — subuid
mappings, `XDG_RUNTIME_DIR`, lingering, the rootless storage root — and a report taken as root
describes a machine nobody uses.

```bash
# one machine, human summary + a report file
sh scripts/gini-doctor.sh

# thirty machines: collect and diff in one go
printf '%s\n' tr-open-10 tr-open-11 tr-open-12 … > hosts.txt
sh scripts/gini-doctor.sh --fanout hosts.txt reports/
```

`--fanout` writes `reports/<host>.txt`, then prints the comparison. To re-compare later, or to
compare machines you collected by hand:

```bash
sh scripts/gini-doctor.sh --compare reports/tr-open-12.txt reports/tr-open-10.txt
sh scripts/gini-doctor.sh --compare reports/*.txt          # all of them at once
```

Put **tr-open-12 first** — it is the reference, and the comparison reads as "what the others have
instead".

The run creates and destroys one throwaway compose project (`ginidoctor<pid>`) using an image
already on the machine. It never touches `gini-lab`, and it removes its own containers even if
`down` fails. `--no-run` skips that part if you would rather it touched nothing.

## Picking what to run

At a terminal, `sh scripts/gini-doctor.sh` with no arguments puts up a menu. Everything in it is
also a flag, because thirty machines are not driven from a menu:

```bash
sh scripts/gini-doctor.sh --list                 # the groups and what each covers
sh scripts/gini-doctor.sh --only engine,perf     # just those
sh scripts/gini-doctor.sh --all                  # everything, including the xv6 feed test
sh scripts/gini-doctor.sh --fanout hosts.txt reports/ perf,xv6   # one group across the lab
```

The menu appears only when there is a terminal on both ends, so a pipe, a cron job or
`ssh host sh -s` never sits waiting for somebody to choose something.

`xv6` is not in the default set: it boots a real kernel and polls it, which takes about fifty
seconds and is worth asking for deliberately rather than thirty times during a fanout.

## Reading the diff

Only differing fields are printed. Two that always differ and are suppressed — free disk and the
image count — come back with `--compare-all`.

| Field that differs | What it means | Fix |
|---|---|---|
| `compose.provider` | **The most likely answer.** `podman compose` is a pass-through, and which provider answers is a real behavioural fork: podman-compose and docker-compose v2 disagree about container naming, about what `ps --format json` prints, and about whether exit 0 means anything. Two machines "both having podman compose" are not the same machine. | Install the same provider everywhere. The docker-compose v2 binary is the better one. |
| `compose.podman_compose.version` | 1.0.6 names containers `project_service_1`; later versions moved to `project-service-1`. | Pin one version across the lab. |
| `podman.network.backend` | `cni` vs `netavark`. Container-to-container DNS differs between them, which is exactly what a drawn topology depends on. | Standardise on `netavark`. |
| `podman.idmap.matches` | **`NO` is the one that hides.** Podman records its id mapping when the storage is first created, so a machine whose subuid range was assigned or changed afterwards keeps the old one — and every visible field still looks right. It surfaces only when a layer wants a high uid: *"potentially insufficient UIDs or GIDs available … lchown /home: invalid argument"*, which reads as a subuid problem and is not one. The range is fine; the storage is stale. | `podman system migrate` (as the user, no admin). |
| `subuid` / `subgid` | `MISSING` means rootless podman cannot map users and every container fails at start. Ranges **differing between machines is normal** and not itself a fault — what matters is `podman.idmap.matches`. | `usermod --add-subuids 100000-165535 --add-subgids 100000-165535 <user>` (admin). |
| `xdg.runtime.exists` | `NO` — usually an ssh login with no systemd user session. Podman falls back to other paths for its socket and auth file, so behaviour changes with how you logged in. | `loginctl enable-linger <user>` (admin), and check `linger` in the report. |
| `linger` | Without it, the user's containers are killed at logout. | `loginctl enable-linger <user>`. |
| `cgroup2.user.controllers` | No `cpu` here means a CPU cap cannot be applied, and sized elements ask for one. | Enable cgroup delegation for the user slice. |
| `podman.storage.driver` | `vfs` instead of `overlay` is far slower and much larger on disk; usually means `fuse-overlayfs` is missing. | Install `fuse-overlayfs`. |
| `auth…config.json` / `auth…auth.json` | `creds=1` on a file nobody meant to create turns an anonymous pull into `invalid username/password`. On a lab with **shared home directories this breaks every machine at once**, while the one whose local image store was filled in beforehand never notices. Check `live.pull.without_creds`. | Move the file aside (it is in the user's own home — no admin). |
| `reg…unqualified` / `shortname` | Decides whether a bare `alpine` resolves. A machine that cannot resolve short names cannot build the machine image. | Match tr-open-12's `/etc/containers/registries.conf`. |
| `lib.libxcb-cursor` | `0` means gBuilder will not start at all, with an error naming a Qt platform plugin rather than a package. | `apt install libxcb-cursor0` (admin). |
| `pkg.gini-*` | Different GINI versions, or one machine on an editable checkout and the rest on PyPI. Worth ruling out before blaming the OS. | `./scripts/dev.sh check` on each. |
| `live.exec.compose` | Fails where `live.exec.engine` succeeds → the compose provider cannot map a service to its container. gBuilder no longer depends on this, so it is a **signal, not a fault**. | None needed; it explains earlier failures. |
| `live.compose.ps_q` | `EMPTY` while `live.container.by_label` found the container is why a healthy lab used to report "did not start". Also fixed. | None needed. |

## What the live round trip proves

Four facts, in order, and the first one that fails is the real problem:

0. `live.pull` — can it reach a registry and unpack what it gets? Two different failures wear
   the same "pull failed" label: a credential (`live.pull.without_creds` says so) and a stale id
   mapping (`podman.idmap.matches` says so). Fixing the first can reveal the second.
1. `live.run` — can this machine run a container at all?
2. `live.container.by_label` — did `compose up` actually create one? (Exit 0 does not mean it did.)
3. `live.exec.engine` — can we run a command inside it the way gBuilder now does?
4. `live.exec.compose` — can the compose provider do the same? Expected to fail on podman-compose.

A machine where 1–3 pass and only 4 fails is **fine for current GINI** and would have been broken
before the label-based exec landed.

## A feed that stutters

The Machine Lab's live faces — the trap feed on CPU & Registers especially — are polls of the
in-container agent, drawn as they arrive. So the feed is exactly as steady as the CPU time that
container is getting, and "it looks like the processor went to sleep" is close to literally true.

The thing to understand first: **xv6 is a RISC-V kernel on an x86 host, so QEMU is emulating, not
virtualising.** No KVM, no hardware assist — one host core, flat out, in software. That makes the
Machine Lab far more sensitive to CPU conditions than anything else in GINI, and it is why the
same build feels smooth on a developer laptop and jerky on a lab desktop.

```bash
sh scripts/gini-doctor.sh --only perf,xv6
```

`perf.xv6.poll` is the measurement that matches the complaint — 24 polls of the running agent,
timed from inside the container:

```
n=24 min=6 med=7 p95=9 max=354 stalls>1s=0 failed=0      ← smooth (a Mac, for reference)
n=24 min=8 med=240 p95=3100 max=9000 stalls>1s=7         ← what a student is describing
```

`stalls>1s` is the number a student can see happen: the lab redraws a few times a second, so a
poll past a second is a visible hole. Run it on a machine where the feed is smooth and on one
where it is not, and compare — a single machine's numbers mean much less than the pair.

| Field | What it means | Fix |
|---|---|---|
| `perf.cpu.governor` = `powersave` | The most likely cause. A governor that keeps deciding this load is not worth ramping up for produces exactly a feed that is fine, then is not. | `cpupower frequency-set -g performance`, or the distro's tuned profile (admin). |
| `perf.cpu.throttle.count` > 0 | Thermal throttling, and it is counted by the kernel rather than inferred. Dusty lab desktops do this. | Hardware; nothing GINI can do, but it ends the argument. |
| `perf.host.cpu` `spread=` large | The machine's speed is changing under a fixed workload. Governor, thermal, or something else on the box. | Look at the two rows above, then `perf.loadavg`. |
| `perf.host.cpu` `median=` large | The machine is simply slower. TCG emulation is the most CPU-bound thing GINI runs, so this shows up here first. | Fewer xv6 elements per machine; a bigger time-slice makes the feed coarser but steadier. |
| `perf.cgroup.user.cpu_max` not `max` | The user slice has a hard CPU cap, and a container inside it cannot exceed that however its own limits are written. | Raise or remove the slice's cap (admin). |
| `perf.loadavg` high | Something else is using the machine. | Find it before blaming GINI. |

If `perf.xv6.poll` is steady but the lab still stutters, the problem is on the gBuilder side
rather than the guest's — say so, and include the report, because that is a different hunt.

## If everything matches

Then the difference is not on this list, and the next thing to compare is the report's raw facts
with `--compare-all`, followed by the two things the doctor cannot see: what the *user account*
differs in (group membership, quota) and whether the machines were imaged from the same base at
the same time. Say so and the probe list can grow — adding a fact is three lines.

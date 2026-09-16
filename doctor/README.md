# gini-doctor

Answers one question: **is this machine able to run GINI, and if a machine next to it can, what
is different?**

```bash
pipx install gini-doctor      # or: pip install --user gini-doctor
gini-doctor                   # a menu of probe groups
```

It probes the container engine and which compose *provider* actually answers, the rootless
prerequisites that fail late (subuid mapping, `XDG_RUNTIME_DIR`, lingering, cgroup delegation,
and whether podman's storage still matches `/etc/subuid`), registries and stored credentials, the
interpreter that really runs gBuilder and the X libraries Qt needs, the GINI images, a live
container round trip, and — for the Machine Lab feeling sluggish — CPU governor, thermal
throttling and a direct measurement of how steady the lab's live feed is.

## The part that matters with more than one machine

A single report says less than a comparison. `--fanout` collects them over ssh and prints only the
fields on which the machines disagree:

```bash
printf '%s\n' lab-01 lab-02 lab-03 > hosts.txt
gini-doctor --fanout hosts.txt reports/
gini-doctor --compare reports/*.txt
```

Nothing has to be installed on those hosts: `--fanout` pipes the script itself over ssh.

Run it **as the user who runs gBuilder, not as root** — subuid mappings, `XDG_RUNTIME_DIR` and
lingering are per-user, and a root report describes a machine nobody uses.

## No dependencies, deliberately

The machine that needs a doctor is one where something is already wrong, so anything this had to
install first is a way for it to be unavailable exactly when it is wanted. It does not depend on
`gini-core`, and emphatically not on `gini-toolkit`, which carries PySide6 — checking thirty lab
machines should not mean installing Qt on them.

The engine is a POSIX shell script (`sh`, `dash` and `busybox ash` all run it) shipped inside the
wheel; the `gini-doctor` command hands it to `sh`. So the same file runs from a pipx install, from
a git checkout, or piped over ssh onto a machine with nothing installed at all — including
straight off the repository:

```bash
curl -fsSL https://raw.githubusercontent.com/citelab/gini/master/doctor/src/gini_doctor/gini-doctor.sh | sh
```

## Where the rest is written up

What a differing field means and what to do about it: [`docs/LAB_DIAGNOSIS.md`](../docs/LAB_DIAGNOSIS.md).

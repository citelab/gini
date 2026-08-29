# GINI Teaching Center

Courses, lab codes, and proof-of-activity submissions. A small threaded HTTP server over SQLite,
with no AI and no external services.

```
pip install gini-teaching-center
gini-teaching-center --data ./tc-data --port 8080
```

Two commands are installed: `gini-teaching-center` and the shorter `gini-tc`.

One dependency, `gini-core` — the shared domain model that carries the proof format, the ticket
codes and the narration. It is pure Python (PyYAML its only dependency), so the whole install is a
couple of MB and needs no Qt and no compiler. It is deliberately **not** `gini-toolkit`: that would pull PySide6
and a Chromium build onto a server to run a web application that never opens a window.

---

## Installing on a server

Written for `gini.cs.mcgill.ca`, but nothing here is McGill-specific.

### 1. A service account and a home

The database holds staff password hashes and every student's submitted work, on a VM the whole
school can log into. Give it its own user and keep the data directory private — this is the step
worth not skipping.

```bash
sudo useradd --system --home /opt/gini-tc --shell /usr/sbin/nologin gini-tc
sudo mkdir -p /opt/gini-tc/{data,logs}
sudo chown -R gini-tc:gini-tc /opt/gini-tc
sudo chmod 700 /opt/gini-tc/data          # nobody else on the VM reads student work
```

### 2. A virtualenv

```bash
sudo -u gini-tc python3 -m venv /opt/gini-tc/venv
sudo -u gini-tc /opt/gini-tc/venv/bin/pip install --upgrade pip
sudo -u gini-tc /opt/gini-tc/venv/bin/pip install gini-teaching-center
```

### 3. The first admin

Start it once by hand. With no `ADMIN_PASSWORD` set, it prints a one-time claim token and waits —
so the portal is never standing open on a port with no password, which is what "first password
wins" would mean on a shared machine.

```bash
sudo -u gini-tc /opt/gini-tc/venv/bin/gini-teaching-center --data /opt/gini-tc/data --port 8080
```

```
GINI Teaching Center  ·  http://127.0.0.1:8080/

  FIRST RUN — claim the admin account:
    username     admin
    claim token  DcKSF9pURy143seE
```

Open the console, sign in with the username **and that token**, and choose a password. Then stop it
(Ctrl-C) and hand it to a process manager.

### 4. Keep it running

**pm2**, since you asked — it works fine for a Python process:

```bash
pm2 start /opt/gini-tc/deploy/ecosystem.config.js
pm2 save && pm2 startup          # prints one sudo command; run it, and it survives reboots
pm2 logs gini-tc
```

Two settings in that file are load-bearing: `instances: 1` and `exec_mode: "fork"`. SQLite wants a
single writer, and pm2's cluster mode would start several processes over one database file.

**systemd** is the alternative, and on a school-managed VM it is what I would pick: it starts at
boot with no per-user `pm2 startup`, survives the account logging out, and the unit file carries
sandboxing (`ProtectSystem=strict`, `UMask=0077`) that keeps the data directory out of reach even
if a permission is set wrongly later. `deploy/gini-tc.service` is ready to copy.

Either way, restarts are a backstop rather than a routine event: an exception inside a request is
caught and answered as a 500, so a bad request cannot take the process down.

### 5. Turn on TLS

**Staff sign in with a password.** Over plain HTTP on a VM the whole school can log into, that
password is readable by anyone else on the wire or on the box. This is the one step not to skip.

Two ways to do it. Both are supported; the difference is who holds the certificate.

**a. nginx in front — what I would pick on a school-managed VM.**

The server binds `127.0.0.1` by default precisely so this works with nothing else to change:

```nginx
server {
    listen 443 ssl;
    server_name gini.cs.mcgill.ca;
    ssl_certificate     /etc/ssl/certs/gini.crt;
    ssl_certificate_key /etc/ssl/private/gini.key;

    client_max_body_size 32m;          # submitted topologies and course handouts

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server { listen 80; server_name gini.cs.mcgill.ca; return 301 https://$host$request_uri; }
```

nginx wins on the two things that bite later: it can bind **443** (a privileged port, which the
`gini-tc` account cannot), and certbot renewal already knows how to reload it. The private key stays
readable only by root — the Teaching Center process never sees it.

**b. Built-in TLS — when there is no nginx, or no root to configure one.**

```bash
gini-teaching-center --data /opt/gini-tc/data --host 0.0.0.0 --port 8443 \
    --tls-cert /etc/ssl/certs/gini.crt --tls-key /etc/ssl/private/gini.key
```

or, in `/opt/gini-tc/env`, `TLS_CERT=…` and `TLS_KEY=…`. TLS 1.2 is the floor.

Two things to know before choosing this:

* **The `gini-tc` user must be able to read the key.** Keys usually live root-only; loosening that
  on a shared VM puts the key within reach of anything that account runs. Prefer a key file owned
  `gini-tc:gini-tc`, mode `0600`, outside `/etc/ssl/private`.
* **An unprivileged process cannot bind 443.** Use 8443 (and tell students the port), or grant
  `CAP_NET_BIND_SERVICE`.

**Giving only one half of the pair is refused at startup.** A cert with no key, or a key with no
cert, exits rather than quietly falling back to HTTP — because that fallback looks like a clean
start while every password goes out in the clear.

Either way, point gBuilder at `https://gini.cs.mcgill.ca` (or `…:8443`) in Settings.

**About self-signed certificates.** They work, but every student machine will reject the connection
until the certificate is trusted, and gBuilder says so in as many words: it reports a *certificate*
problem and tells the student their instructor has to fix it, rather than "is the server running?"
— which would send them chasing an outage that is not happening. If you go this route, plan how the
certificate reaches student machines before the first lab, not during it.

If you cannot get a certificate quickly, keep the VPN restriction you planned and treat the window
before TLS as a testing period rather than a term.

---

## Upgrading

```bash
sudo -u gini-tc /opt/gini-tc/venv/bin/pip install --upgrade gini-teaching-center
pm2 restart gini-tc          # or: sudo systemctl restart gini-tc
```

The database migrates itself on open: missing columns are added, and retired `NOT NULL` columns
are relaxed by rebuilding the table without dropping data. Downgrading is not supported — take a
copy of `data/gini.db` first if you are trying a version out mid-term.

## Backups

Everything is one SQLite file plus the uploaded materials:

```bash
sudo -u gini-tc sqlite3 /opt/gini-tc/data/gini.db ".backup '/opt/gini-tc/data/backup.db'"
```

`.backup` rather than `cp`, because the server is running and WAL mode means a plain copy can catch
a torn moment. The Site Reset in the console also writes a full JSON snapshot to `data/backups/`
before it removes anything.

## Running from a source checkout

For development on your own machine — no install, edits take effect immediately:

```bash
./teaching-center/run.sh                     # localhost:8080, data in ./tc-data
PORT=9000 ADMIN_PASSWORD=secret ./teaching-center/run.sh
```

It puts `core/src` and `teaching-center/src` on `PYTHONPATH` and runs the package with `-m`. That
last part matters: the modules import each other as a package now, so running `server.py` as a
path breaks every one of those imports.

If you would rather have the `gini-tc` command while still editing the checkout, install it
editable:

```bash
pip install -e ./core -e ./teaching-center
```

## Configuration

Every flag falls back to an environment variable, so a unit file, a pm2 config and an old shell
script all work.

| Flag | Env | Default | |
|---|---|---|---|
| `--data` | `COURSE_ROOT` | `./tc-data` | courses, submissions, backups |
| `--port` | `PORT` | `8080` | |
| `--host` | `HOST` | `127.0.0.1` | bind address — leave it local, proxy in front |
| `--admin` | `ADMIN_ID` | `admin` | the portal admin's username |
| | `ADMIN_PASSWORD` | *(unset)* | authoritative on every boot when set; otherwise a claim token is printed |

`ADMIN_PASSWORD` is read on **every** start, not just the first, so setting it later reconciles a
forgotten password. Keep it out of any file that goes into git: put it in `/opt/gini-tc/env`
(`chmod 600`), or use the claim token and never store a password at all.

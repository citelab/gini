# GINI Teaching Center

Courses, lab codes, and proof-of-activity submissions. A small threaded HTTPS server over SQLite,
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

> **Upgrading a box that once had `pip install --user`?** pipx will not overwrite the console
> scripts that install left in `~/.local/bin`, and says so — then `gini-tc` keeps running the OLD
> version, with no other symptom than flags it does not recognise. Clear the way and let pipx
> place its own:
>
> ```bash
> python3 -m pip uninstall -y gini-teaching-center     # this owns ~/.local/bin/gini-tc
> pipx install --force gini-teaching-center
> which -a gini-tc && gini-tc --version                # expect ONE path, and the new version
> ```

### 3. The first admin

Start it once by hand. With no `ADMIN_PASSWORD` set, it prints a claim token and waits — so the
portal is never standing open on a port with no password, which is what "first password wins" would
mean on a shared machine.

The token is reprinted on every start **until it is claimed**, so missing it in a scrollback is not
a lockout. If you would rather not use it at all, `ADMIN_PASSWORD` is authoritative on every boot
and reconciles an existing account's password:

```bash
ADMIN_PASSWORD='…' gini-tc --data /opt/gini-tc/data --port 8443 \
    --tls-cert … --tls-key …
```

```bash
sudo -u gini-tc /opt/gini-tc/venv/bin/gini-teaching-center --data /opt/gini-tc/data --port 8443 \
    --tls-cert /etc/ssl/certs/gini.crt --tls-key /opt/gini-tc/gini.key
```

There is no HTTP mode — see [TLS](#5-tls-is-not-optional) below — so a certificate has to exist
before the first start. For your own machine, `--make-cert` makes one and serves with it:

```bash
gini-teaching-center --data ./tc-data --port 8443 --make-cert
```

It writes `<data>/tls/cert.pem` and `key.pem`, **reuses them if they are already there** (a fresh
certificate would throw away any trust you had given the old one), and prints the two ways to make
gBuilder trust it. `./teaching-center/run.sh` does the same thing for a checkout.

```
GINI Teaching Center  ·  https://127.0.0.1:8443/

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

### 5. TLS is not optional

**The Teaching Center serves HTTPS and nothing else**, and gBuilder refuses a `http://` course
server address. There is no flag to turn either off.

It used to be optional, with a printed warning when the bind was reachable — but a warning is not
a control: the server still came up, staff still typed passwords into it, and the twelve-hour
session token that came back rode every later request in clear text, along with every student's
assignment code and submitted work. The one argument for keeping an HTTP mode was that you cannot
always have a certificate; loopback can hold one exactly like a public name, so it does not hold.

Two ways to do it. Both are supported; the difference is who holds the certificate.

**a. nginx in front — what I would pick on a school-managed VM.**

The server binds `127.0.0.1` by default precisely so this works with nothing else to change. Give
the backend its own loopback certificate (`./teaching-center/run.sh` will make one, or see the
`openssl` recipe the server prints if you start it without one):

```nginx
server {
    listen 443 ssl;
    server_name gini.cs.mcgill.ca;
    ssl_certificate     /etc/ssl/certs/gini.crt;
    ssl_certificate_key /etc/ssl/private/gini.key;

    client_max_body_size 32m;          # submitted topologies and course handouts

    location / {
        # https, because the backend has no HTTP mode. nginx does not verify an upstream
        # certificate by default, so a self-signed loopback cert on the backend is enough.
        proxy_pass https://127.0.0.1:8443;
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

**Half a pair, or no pair, is refused at startup.** A cert with no key, a key with no cert, or
neither, exits with the `openssl` command that would fix it. Nothing falls back to HTTP, because a
fallback looks like a clean start while every password goes out in the clear.

Either way, point gBuilder at `https://gini.cs.mcgill.ca` (or `…:8443`) in Settings.

**Where the certificate comes from.** For a real course server on a real name, either a school
certificate (McGill has an institutional service — ask IT first; those are usually 1-year and your
sysadmins already know the renewal path) or Let's Encrypt.

If you use Let's Encrypt, two things decide how:

* **Validation needs reachability.** HTTP-01 wants inbound `:80` and TLS-ALPN-01 wants `:443`. A VM
  reachable only inside the campus VPN can satisfy neither — use **DNS-01**, which proves control of
  the name through the DNS zone and needs no inbound connection at all.
* **Renewal does not reach a running server.** The certificate is read once, at startup. With nginx
  in front this is a non-issue: certbot reloads nginx and the backend keeps its own loopback cert.
  Serving the public certificate from this process instead, you must restart it on renewal —

  ```bash
  certbot renew --deploy-hook 'systemctl restart gini-tc'
  ```

  Without that hook it keeps serving the old certificate and expires 90 days in, mid-term.

**About self-signed certificates.** They encrypt, but they are not trusted, and gBuilder verifies
properly — so every student machine rejects the connection until the certificate is trusted. It
says so in as many words: a *certificate* problem, for the instructor to fix, rather than "is the
server running?", which would send a student chasing an outage that is not happening.

For a class, get a real certificate for a real name. Self-signed is for your own machine, where
either of these makes it trusted:

```bash
SSL_CERT_FILE=/path/to/cert.pem gbuilder        # per-launch, nothing installed
mkcert -install && mkcert localhost 127.0.0.1   # a local CA, once, in the system trust store
```

The certificate **must carry a subjectAltName**. A bare `CN=localhost` is rejected by OpenSSL 3 and
by macOS however it is signed, and it fails looking exactly like a trust problem.

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

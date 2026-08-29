// pm2 process file for the GINI Teaching Center.
//
//   pm2 start deploy/ecosystem.config.js
//   pm2 logs gini-tc
//   pm2 save && pm2 startup      # survive a reboot — prints a command to run once with sudo
//
// pm2 restarts the server if it exits. That is worth having, but note WHY it is rarely needed:
// an exception inside a request is caught and answered as a 500, so a bad request cannot take the
// process down. What pm2 protects against is the rest — OOM, a bug in startup, an operator's stray
// kill — and, mainly, coming back after a reboot.
module.exports = {
  apps: [{
    name: "gini-tc",
    script: "gini-teaching-center",
    // Full path if pm2 runs without your shell's PATH — which it usually does under `pm2 startup`.
    // Change this to wherever the venv lives:
    //   script: "/opt/gini-tc/venv/bin/gini-teaching-center",
    // TLS is required — there is no HTTP mode — so the certificate is part of the command.
    // Loopback gets one too: nginx proxies to https://127.0.0.1:8443 and does not verify an
    // upstream certificate, so a self-signed cert made on the box is enough here, and the real
    // certificate stays with nginx where root owns the key.
    // To serve the public certificate from this process instead, use --host 0.0.0.0 (README §5b
    // covers binding 443 unprivileged, and the Let's Encrypt renewal hook — a renewed certificate
    // does NOT reach a running process, which reads it once at startup).
    args: "--data /opt/gini-tc/data --port 8443 --host 127.0.0.1" +
          " --tls-cert /opt/gini-tc/tls/gini.crt --tls-key /opt/gini-tc/tls/gini.key",
    interpreter: "none",              // it is an installed console script, not a .js file

    instances: 1,                     // MUST stay 1: SQLite with one writer, one process
    exec_mode: "fork",                // never "cluster" — see above
    autorestart: true,
    max_restarts: 10,
    min_uptime: "20s",                // a process that dies faster than this is broken, not unlucky
    restart_delay: 2000,
    max_memory_restart: "300M",

    env: {
      // ADMIN_PASSWORD is deliberately NOT here: this file belongs in git. Put it in
      // /opt/gini-tc/env (chmod 600) and load it, or set the admin password once through the
      // one-time claim token the server prints on first run and never store it at all.
      ADMIN_ID: "admin",
      // TLS_CERT / TLS_KEY work here too, if you would rather not put them in args.
      PYTHONUNBUFFERED: "1",          // or logs arrive in 4KB lumps, hours late
    },

    out_file: "/opt/gini-tc/logs/out.log",
    error_file: "/opt/gini-tc/logs/err.log",
    time: true,                       // timestamp every line; you will want this at 2am
  }],
};

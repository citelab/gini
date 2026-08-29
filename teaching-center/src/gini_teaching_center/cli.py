"""`gini-teaching-center` — the command a service manager runs.

Flags beat environment variables here, because that is what a unit file or a pm2 config actually
passes. Every flag still falls back to its environment variable, so an existing `run.sh` keeps
working unchanged.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from .version import __version__
    ap = argparse.ArgumentParser(
        prog="gini-teaching-center",
        description="Run the GINI Teaching Center.")
    ap.add_argument("--data", metavar="DIR", default=os.environ.get("COURSE_ROOT", "./tc-data"),
                    help="where courses, submissions and backups live (env: COURSE_ROOT)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")),
                    help="port to listen on (env: PORT)")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                    help="address to bind. Defaults to LOCALHOST — either terminate TLS in a "
                         "proxy in front, or pass --tls-cert/--tls-key and bind 0.0.0.0 "
                         "(env: HOST)")
    ap.add_argument("--admin", default=os.environ.get("ADMIN_ID", "admin"),
                    help="the portal admin's username (env: ADMIN_ID)")
    ap.add_argument("--tls-cert", default=os.environ.get("TLS_CERT", ""), metavar="FILE",
                    help="serve HTTPS with this certificate chain (env: TLS_CERT). Needs "
                         "--tls-key too. Without it, bind localhost and proxy TLS in front")
    ap.add_argument("--tls-key", default=os.environ.get("TLS_KEY", ""), metavar="FILE",
                    help="the private key for --tls-cert (env: TLS_KEY)")
    ap.add_argument("--make-cert", action="store_true",
                    help="make a self-signed certificate for localhost if there is not one yet, "
                         "and serve with it. For your own machine and for the loopback backend "
                         "behind a proxy — a class wants a real certificate for a real name")
    ap.add_argument("--version", action="version", version=f"gini-teaching-center {__version__}")
    args = ap.parse_args(argv)

    # The server reads its configuration from the environment at import time, so set it before
    # importing. Doing this the other way round is a silent misconfiguration: the flags would be
    # parsed and then ignored.
    os.environ["COURSE_ROOT"] = str(Path(args.data).expanduser().resolve())
    os.environ["PORT"] = str(args.port)
    os.environ["ADMIN_ID"] = args.admin

    tls_cert, tls_key = args.tls_cert, args.tls_key
    if args.make_cert:
        # Requiring a certificate is only reasonable if getting one is trivial; otherwise it is a
        # rule people route around. Defaults sit beside the course data so --data moves both.
        from . import certs
        if not (tls_cert and tls_key):
            tls_cert, tls_key = (str(x) for x in certs.default_paths(os.environ["COURSE_ROOT"]))
        path, made = certs.ensure(tls_cert, tls_key, certs.hosts_for(args.host))
        # Existing files are reused, never regenerated: a fresh certificate would silently throw
        # away whatever trust the old one had been given.
        print(f"  cert     {path}  ({'made just now' if made else 'already there'}, self-signed)")
        print(f"  trust    SSL_CERT_FILE={path} gbuilder")
        print(f"           or once, for everything:  mkcert -install && "
              f"mkcert -cert-file {tls_cert} -key-file {tls_key} localhost 127.0.0.1\n")

    from . import server
    try:
        server.serve(host=args.host, port=args.port,
                     tls_cert=tls_cert, tls_key=tls_key)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())

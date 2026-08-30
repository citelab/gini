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


#: The xv6 book, ready to paste. Not hard-coded as a special case — `index-reference` takes any
#: LaTeXML/BookML book — but the licence line is exact and getting it wrong is a licence problem,
#: so it is written down once, here, rather than retyped from memory on a server at midnight.
XV6 = {
    "id": "xv6-riscv-book",
    "url": "https://xv6-guide.github.io/xv6-riscv-book/index.html",
    "title": "xv6: a simple, Unix-like teaching operating system",
    "licence": "MIT",
    "attribution": ("Copyright (c) 2006-2024 Russ Cox, Frans Kaashoek, Robert Morris. "
                    "Used under the MIT licence."),
}


def _index_reference(argv: list[str]) -> int:
    """`gini-tc index-reference <start-url> …` — read a book into the search index.

    An administrative command, run once, and deliberately not something the server does while a
    student waits: it fetches a page at a time from somebody else's site, some ninety times.

    `--attribution` is REQUIRED and there is no default. Most of what is worth indexing is
    permissively licensed *provided the notice travels with it*, and a flag that could be skipped
    would be skipped. Refusing here is the cheapest place to make that impossible.
    """
    import argparse
    import os
    from pathlib import Path as _P

    ap = argparse.ArgumentParser(
        prog="gini-tc index-reference",
        description="Read a book (LaTeXML/BookML) into the course search index.",
        epilog="For the xv6 book, --preset xv6 fills in every flag below.")
    ap.add_argument("url", nargs="?", default="", help="the page to start walking from")
    ap.add_argument("--preset", choices=("xv6",), help="a known book, with its licence line")
    ap.add_argument("--id", default="", help="short stable id, e.g. xv6-riscv-book")
    ap.add_argument("--title", default="")
    ap.add_argument("--licence", default="")
    ap.add_argument("--attribution", default="",
                    help="the copyright line, carried into every citation (REQUIRED)")
    ap.add_argument("--attach", action="append", default=[], metavar="COURSE",
                    help="attach to this course (repeatable); without it nothing can see it yet")
    ap.add_argument("--data", metavar="DIR", default=os.environ.get("COURSE_ROOT", "./tc-data"))
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args(argv)

    if a.preset == "xv6":
        a.url = a.url or XV6["url"]
        a.id = a.id or XV6["id"]
        a.title = a.title or XV6["title"]
        a.licence = a.licence or XV6["licence"]
        a.attribution = a.attribution or XV6["attribution"]
    if not (a.url and a.id):
        print("Give a start URL and --id (or --preset xv6).")
        return 2
    if not a.attribution.strip():
        print("--attribution is required: it is the copyright notice, and carrying it is a "
              "condition of using most of what is worth indexing.")
        return 2

    from . import references
    from .store import Store
    root = _P(a.data).expanduser().resolve()
    store = Store(str(root))
    print(f"  reading {a.url}")
    rows = references.crawl(
        a.url, ref=a.id, max_pages=a.max_pages or references.MAX_PAGES,
        on_page=lambda n, t: print(f"    {n:8} {t}"))
    if not rows:
        print("  nothing was read — check the URL, and that the book is LaTeXML/BookML.")
        return 1
    import time as _t
    store.reference_put({"id": a.id, "title": a.title or a.id, "source_url": a.url,
                         "licence": a.licence, "attribution": a.attribution.strip(),
                         "indexed": _t.time(), "sections": len(rows)})
    store.sections_put(a.id, rows)
    words = sum(len(r["body"].split()) for r in rows)
    print(f"\n  indexed {len(rows)} sections, {words:,} words, into {root}")
    for course in a.attach:
        store.course_ref_set(course, a.id, True)
        print(f"  attached to {course}")
    if not a.attach:
        print("  NOT attached to any course yet — nothing will find it until you attach it "
              "(--attach COURSE, or the Content tab in the console).")
    return 0


#: Subcommands. `gini-tc` on its own still runs the server, because that is what is in every
#: unit file and run.sh already written.
_COMMANDS = {"index-reference": _index_reference}


def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    args = list(argv) if argv is not None else _sys.argv[1:]
    if args and args[0] in _COMMANDS:
        return _COMMANDS[args[0]](args[1:])
    return _serve(args)


def _serve(argv: list[str] | None = None) -> int:
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

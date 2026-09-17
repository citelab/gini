"""``registry`` — can this machine resolve and reach an image, anonymously and with whatever
credentials its engine is configured with? Read-only.

The legacy doctor answered this with a real pull, which writes to the image store. This asks the
same two questions without storing anything:

* ``anonymous``: the registry's token endpoint and a HEAD of the manifest, over HTTPS from the
  doctor itself. Proves the network path, proxy and TLS.
* ``engine``: ``<engine> manifest inspect`` — the engine resolving the name and talking to the
  registry with its own configuration and stored credentials.

Anonymous works and the engine is refused → a stored credential is what blocks pulls. That is the
lab failure where a file nobody meant to create turns an anonymous pull into "invalid username/
password". Credential files themselves are never opened.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .. import platforms
from . import probe
from ._common import answering_engine, read_text

REPOSITORY = "library/busybox"
TAG = "latest"
IMAGE = "docker.io/%s:%s" % (REPOSITORY, TAG)
AUTH_WORDS = ("unauthorized", "authentication", "username", "password", "denied", "401", "403")


def anonymous_manifest(timeout: float = 10.0):
    """(ok, detail). Docker Hub's anonymous token flow, then HEAD the manifest."""
    token_url = ("https://auth.docker.io/token?service=registry.docker.io&scope=repository:%s:pull"
                 % REPOSITORY)
    try:
        with urllib.request.urlopen(token_url, timeout=timeout) as resp:   # noqa: S310
            token = json.loads(resp.read(65536).decode("utf-8")).get("token", "")
        req = urllib.request.Request(
            "https://registry-1.docker.io/v2/%s/manifests/%s" % (REPOSITORY, TAG), method="HEAD",
            headers={"Authorization": "Bearer %s" % token,
                     "Accept": "application/vnd.oci.image.index.v1+json, "
                               "application/vnd.docker.distribution.manifest.list.v2+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
            return True, "HTTP %d" % resp.status
    except urllib.error.HTTPError as e:
        return False, "HTTP %d %s" % (e.code, e.reason)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, str(getattr(e, "reason", e))


def config_lines(path: str):
    """The two registries.conf settings that decide whether a bare ``alpine`` resolves."""
    text = read_text(path)
    if text is None:
        return None
    found = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        low = line.lower()
        for key in ("unqualified-search-registries", "short-name-mode"):
            if low.startswith(key) and key not in found:
                found[key] = line
    return found


@probe("registry", platforms=platforms.ALL,
       describe="registries.conf, and whether an image is reachable anonymously and via the engine (read-only)")
def registry(ctx):
    if ctx.platform == platforms.LINUX:
        for scope, path in (("system", "/etc/containers/registries.conf"),
                            ("user", os.path.join(os.path.expanduser("~"), ".config", "containers",
                                                  "registries.conf"))):
            found = config_lines(path)
            if found is None:
                ctx.absent("conf.%s" % scope)
                continue
            ctx.ok("conf.%s" % scope, "present")
            for key in ("unqualified-search-registries", "short-name-mode"):
                if key in found:
                    ctx.ok("conf.%s.%s" % (scope, key), found[key])
                else:
                    ctx.absent("conf.%s.%s" % (scope, key))
        for name in ("containers.conf", "storage.conf"):
            ctx.ok("conf.%s" % name, os.path.isfile("/etc/containers/%s" % name))
    else:
        for key in ("conf.system", "conf.user", "conf.containers.conf", "conf.storage.conf"):
            ctx.na(key)

    # Where credentials WOULD come from, by name only: these redirect the engine's auth lookup.
    for var in ("DOCKER_CONFIG", "REGISTRY_AUTH_FILE"):
        ctx.ok("env.%s" % var, "set" if os.environ.get(var) else "(unset)")

    ctx.ok("image", IMAGE)
    anon_ok, anon_detail = anonymous_manifest(timeout=min(ctx.timeout, 15))
    if anon_ok:
        ctx.ok("anonymous", "reachable (%s)" % anon_detail)
    else:
        ctx.error("anonymous", anon_detail)

    engine = answering_engine(ctx)
    if engine is None:
        ctx.absent("engine", "no engine answering")
        ctx.absent("credential_blocks")
        return
    argv = [engine, "manifest", "inspect", IMAGE if engine == "podman" else "busybox:latest"]
    res = ctx.run(argv, timeout=min(ctx.timeout, 30))
    if res.ok:
        ctx.ok("engine", "reachable via %s" % engine)
        ctx.ok("credential_blocks", False)
    else:
        detail = res.evidence()
        ctx.error("engine", "%s: %s" % (engine, detail))
        refused = any(w in detail.lower() for w in AUTH_WORDS)
        if anon_ok and refused:
            ctx.ok("credential_blocks", True)
        elif anon_ok:
            ctx.ok("credential_blocks", False)
        else:
            ctx.absent("credential_blocks", "the registry is not reachable anonymously either")

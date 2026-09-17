"""The report: one JSON document per run, the same shape wherever it was produced.

Stage 0 writes this shape too (with only ``stage0.*`` facts) when it finds no usable Python, so a
machine that cannot run Stage 1 still produces something the Health Center and ``compare`` read.

Shape (schema ``gini-doctor/1``)::

    {
      "schema": "gini-doctor/1",
      "doctor": {"engine": "python", "version": "…"},
      "host": "tr-open-12",
      "collected_at": "2026-09-17T14:03:11Z",
      "platform": "linux",
      "policy": {"source": "builtin", "version": "builtin-1"},
      "groups": ["system"],
      "facts": {
        "system.os.name": {"value": "Ubuntu 24.04.1 LTS", "status": "ok"},
        "system.memory.total_mb": {"value": 15890, "status": "ok"},
        "engine.subuid.range": {"status": "n/a"},
        "engine.podman.version": {"status": "absent"},
        "engine.docker.info": {"status": "error", "detail": "Cannot connect to the Docker daemon"}
      }
    }

A fact's ``status`` is what makes machines comparable: ``ok`` carries a value; ``absent`` means
the thing looked for is not there; ``error`` means looking failed, with the machine's own words
in ``detail``; ``n/a`` means the fact does not exist on this platform and never will.
"""
from __future__ import annotations

import datetime as _dt
import json
import socket
from typing import Any, Dict, List, Optional

from . import ENGINE_VERSION, SCHEMA

OK = "ok"
ABSENT = "absent"
ERROR = "error"
NA = "n/a"
STATUSES = (OK, ABSENT, ERROR, NA)

_SCALARS = (str, int, float, bool, type(None))


def _clean(value: Any) -> Any:
    """Facts are JSON scalars or flat lists of them. Anything else is stringified rather than
    rejected: a probe that returns something odd still produces a fact, not a crash."""
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, (list, tuple)):
        return [v if isinstance(v, _SCALARS) else str(v) for v in value]
    return str(value)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def hostname() -> str:
    try:
        return socket.gethostname() or "unknown"
    except Exception:
        return "unknown"


class Report:
    def __init__(self, platform: str, policy_source: str = "builtin",
                 policy_version: str = "builtin", host: Optional[str] = None,
                 collected_at: Optional[str] = None):
        self.schema = SCHEMA
        self.doctor = {"engine": "python", "version": ENGINE_VERSION}
        self.host = host if host is not None else hostname()
        self.collected_at = collected_at or utc_now()
        self.platform = platform
        self.policy = {"source": policy_source, "version": policy_version}
        self.groups: List[str] = []
        self.facts: Dict[str, Dict[str, Any]] = {}

    # -- recording -------------------------------------------------------- #
    def set(self, key: str, status: str, value: Any = None, detail: Optional[str] = None) -> None:
        if status not in STATUSES:
            raise ValueError("unknown fact status %r" % status)
        fact: Dict[str, Any] = {"status": status}
        if status == OK:
            fact["value"] = _clean(value)
        if detail:
            fact["detail"] = str(detail)[:500]
        self.facts[key] = fact

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.facts.get(key)

    def value(self, key: str, default: Any = None) -> Any:
        f = self.facts.get(key)
        return f.get("value", default) if f and f.get("status") == OK else default

    # -- JSON ------------------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "doctor": dict(self.doctor),
            "host": self.host,
            "collected_at": self.collected_at,
            "platform": self.platform,
            "policy": dict(self.policy),
            "groups": list(self.groups),
            "facts": {k: dict(self.facts[k]) for k in sorted(self.facts)},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Report":
        schema = data.get("schema")
        if schema != SCHEMA:
            raise ValueError("not a %s report (schema=%r)" % (SCHEMA, schema))
        policy = data.get("policy") or {}
        r = cls(platform=str(data.get("platform", "unknown")),
                policy_source=str(policy.get("source", "unknown")),
                policy_version=str(policy.get("version", "unknown")),
                host=str(data.get("host", "unknown")),
                collected_at=str(data.get("collected_at", "")))
        r.doctor = dict(data.get("doctor") or {})
        r.groups = [str(g) for g in data.get("groups") or []]
        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            raise ValueError("facts must be an object")
        for k, f in facts.items():
            if not isinstance(f, dict) or f.get("status") not in STATUSES:
                raise ValueError("fact %r is malformed" % k)
            r.facts[str(k)] = dict(f)
        return r

    @classmethod
    def load(cls, path: str) -> "Report":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.to_json())

    def default_filename(self) -> str:
        stamp = self.collected_at.replace(":", "").replace("-", "")
        safe_host = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in self.host)
        return "gini-doctor-%s-%s.json" % (safe_host, stamp)

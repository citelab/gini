"""GINI Cloud Fabric agent — the cloud telemetry & coordination plane.

One small container watches every cloud service in the lab. It reads FABRIC_CONFIG (the
list of services + how to reach them), and on a timer asks each one for its *native*
application metrics through a per-type adapter — Redis INFO, the RabbitMQ management API,
Postgres pg_stat, Traefik / NATS / nginx endpoints. It normalizes everything to one
schema and serves it at ``/metrics.json`` for gBuilder to poll (and ``/metrics`` in
Prometheus text so Grafana can graph the app metrics too).

Stdlib-only at import time (urllib + raw sockets); psycopg2 is imported lazily inside the
Postgres adapter, so the parse helpers below are safe to unit-test anywhere.

FABRIC_CONFIG = {"services":[
   {"name":"redis1","type":"cache","host":"redis1","port":6379,"creds":{}},
   {"name":"rabbit1","type":"queue","host":"rabbit1","port":15672,
    "creds":{"user":"guest","password":"guest"}}, ... ]}
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

POLL = float(os.environ.get("FABRIC_POLL", "2.0"))
PORT = int(os.environ.get("FABRIC_PORT", "9099"))


# --------------------------------------------------------------------------- #
# Pure parse helpers (unit-tested — no network)
# --------------------------------------------------------------------------- #
def parse_prometheus(text: str) -> dict[str, float]:
    """A Prometheus text exposition -> {metric_name: summed_value}. Labels are ignored
    (samples for the same metric name are summed), which is what we want for totals."""
    out: dict[str, float] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # name{labels} value   OR   name value
        name = line.split("{", 1)[0].split(" ", 1)[0]
        val = line.rsplit(" ", 1)[-1]
        try:
            out[name] = out.get(name, 0.0) + float(val)
        except ValueError:
            continue
    return out


def parse_redis_info(text: str) -> dict[str, str]:
    """Redis INFO 'key:value' lines -> dict."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def redis_kpis(info: dict) -> list[dict]:
    def num(k, d=0.0):
        try:
            return float(info.get(k, d))
        except (TypeError, ValueError):
            return d
    hits, misses = num("keyspace_hits"), num("keyspace_misses")
    hit_rate = 100.0 * hits / (hits + misses) if (hits + misses) > 0 else 0.0
    return [
        {"label": "ops", "value": round(num("instantaneous_ops_per_sec"), 1), "unit": "/s"},
        {"label": "hit rate", "value": round(hit_rate, 1), "unit": "%"},
        {"label": "clients", "value": int(num("connected_clients")), "unit": ""},
        {"label": "memory", "value": round(num("used_memory") / 1048576, 1), "unit": "MiB"},
    ]


def rabbit_kpis(overview: dict) -> list[dict]:
    qt = overview.get("queue_totals", {}) or {}
    ms = overview.get("message_stats", {}) or {}
    pub = (ms.get("publish_details") or {}).get("rate", 0.0)
    dlv = (ms.get("deliver_get_details") or ms.get("deliver_details") or {}).get("rate", 0.0)
    return [
        {"label": "queued", "value": int(qt.get("messages", 0)), "unit": "msgs"},
        {"label": "publish", "value": round(float(pub), 1), "unit": "/s"},
        {"label": "deliver", "value": round(float(dlv), 1), "unit": "/s"},
    ]


def nats_kpis(varz: dict, rates, ts: float, key: str) -> list[dict]:
    inm = float(varz.get("in_msgs", 0)); outm = float(varz.get("out_msgs", 0))
    return [
        {"label": "conns", "value": int(varz.get("connections", 0)), "unit": ""},
        {"label": "in", "value": round(rates.rate(key + ":in", inm, ts), 1), "unit": "msg/s"},
        {"label": "out", "value": round(rates.rate(key + ":out", outm, ts), 1), "unit": "msg/s"},
    ]


def parse_nginx_status(text: str) -> dict[str, float]:
    """nginx stub_status -> {active, accepts, handled, requests}. The three counters are
    on the line *after* the 'server accepts handled requests' header."""
    out: dict[str, float] = {}
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if "Active connections:" in line:
            try:
                out["active"] = float(line.split(":")[1].strip().split()[0])
            except (IndexError, ValueError):
                pass
        if "accepts" in line and "handled" in line and "requests" in line:
            nums = [t for t in lines[i + 1].split() if t.isdigit()] if i + 1 < len(lines) else []
            if len(nums) >= 3:
                out["accepts"], out["handled"], out["requests"] = map(float, nums[:3])
    return out


# --------------------------------------------------------------------------- #
# Rate tracker (counter -> per-second), and fetch helpers
# --------------------------------------------------------------------------- #
class Rates:
    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, float]] = {}

    def rate(self, key: str, value: float, ts: float) -> float:
        prev = self._prev.get(key)
        self._prev[key] = (value, ts)
        if not prev:
            return 0.0
        pv, pt = prev
        dt = ts - pt
        return max(0.0, (value - pv) / dt) if dt > 0 else 0.0


def _http_get(url: str, timeout: float = 3.0, auth: tuple | None = None) -> str | None:
    req = urllib.request.Request(url)
    if auth:
        import base64
        tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _tcp_up(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _redis_info_raw(host: str, port: int, timeout: float = 2.0) -> str | None:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.sendall(b"INFO\r\n")
            s.settimeout(timeout)
            chunks = []
            while True:
                b = s.recv(8192)
                if not b:
                    break
                chunks.append(b)
                if len(b) < 8192:
                    break
            return b"".join(chunks).decode("utf-8", "replace")
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Adapters: type -> collect(svc, rates, ts) -> {"up", "kpis", [rps], [latency_ms]}
# --------------------------------------------------------------------------- #
def collect_cache(svc, rates, ts):
    raw = _redis_info_raw(svc["host"], svc.get("port", 6379))
    if raw is None:
        return {"up": _tcp_up(svc["host"], svc.get("port", 6379)), "kpis": []}
    return {"up": True, "kpis": redis_kpis(parse_redis_info(raw))}


def collect_queue(svc, rates, ts):
    c = svc.get("creds", {})
    url = f"http://{svc['host']}:{svc.get('port', 15672)}/api/overview"
    txt = _http_get(url, auth=(c.get("user", "guest"), c.get("password", "guest")))
    if txt is None:
        return {"up": _tcp_up(svc["host"], 5672), "kpis": []}
    try:
        return {"up": True, "kpis": rabbit_kpis(json.loads(txt))}
    except (ValueError, KeyError):
        return {"up": True, "kpis": []}


def collect_database(svc, rates, ts):
    host, port = svc["host"], svc.get("port", 5432)
    c = svc.get("creds", {})
    try:
        import psycopg2  # lazy: only this adapter needs it
        conn = psycopg2.connect(host=host, port=port, connect_timeout=2,
                                 user=c.get("user", "gini"), password=c.get("password", "gini"),
                                 dbname=c.get("db", "postgres"))
        try:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(sum(xact_commit),0), COALESCE(sum(numbackends),0) "
                        "FROM pg_stat_database")
            commits, conns = cur.fetchone()
        finally:
            conn.close()
        return {"up": True, "kpis": [
            {"label": "TPS", "value": round(rates.rate(svc["name"] + ":tx",
                                                        float(commits), ts), 1), "unit": "/s"},
            {"label": "connections", "value": int(conns), "unit": ""}]}
    except Exception:                       # noqa: BLE001 — driver missing or unreachable
        return {"up": _tcp_up(host, port), "kpis": []}


def collect_proxy(svc, rates, ts):
    """Traefik exposes Prometheus metrics at :8080/metrics when started with
    --metrics.prometheus. req/s from the request counter, latency from the sum/count."""
    txt = _http_get(f"http://{svc['host']}:{svc.get('port', 8080)}/metrics")
    if txt is None:
        return {"up": _tcp_up(svc["host"], svc.get("port", 8080)), "kpis": []}
    m = parse_prometheus(txt)
    reqs = sum(v for k, v in m.items() if k.endswith("requests_total"))
    rps = rates.rate(svc["name"] + ":req", reqs, ts)
    kpis = [{"label": "req", "value": round(rps, 1), "unit": "/s"}]
    # average latency over the interval = Δ(duration_sum) / Δ(request_count)
    dur = sum(v for k, v in m.items() if k.endswith("request_duration_seconds_sum"))
    cnt = sum(v for k, v in m.items() if k.endswith("request_duration_seconds_count"))
    d_dur = rates.rate(svc["name"] + ":durs", dur, ts)
    d_cnt = rates.rate(svc["name"] + ":durc", cnt, ts)
    out = {"up": True, "rps": round(rps, 1)}
    if d_cnt > 0:
        ms = 1000.0 * d_dur / d_cnt
        kpis.append({"label": "latency", "value": round(ms, 1), "unit": "ms"})
        out["latency_ms"] = round(ms, 1)
    out["kpis"] = kpis
    return out


def collect_loadbalancer(svc, rates, ts):
    """nginx stub_status at /nginx_status (enabled via a generated conf)."""
    txt = _http_get(f"http://{svc['host']}:{svc.get('port', 80)}/nginx_status")
    if txt is None:
        return {"up": _tcp_up(svc["host"], svc.get("port", 80)), "kpis": []}
    st = parse_nginx_status(txt)
    rps = rates.rate(svc["name"] + ":req", st.get("requests", 0.0), ts)
    return {"up": True, "rps": round(rps, 1), "kpis": [
        {"label": "req", "value": round(rps, 1), "unit": "/s"},
        {"label": "active", "value": int(st.get("active", 0)), "unit": ""}]}


def collect_nats(svc, rates, ts):
    txt = _http_get(f"http://{svc['host']}:{svc.get('port', 8222)}/varz")
    if txt is None:
        return {"up": _tcp_up(svc["host"], 4222), "kpis": []}
    try:
        return {"up": True, "kpis": nats_kpis(json.loads(txt), rates, ts, svc["name"])}
    except ValueError:
        return {"up": True, "kpis": []}


def collect_generic(svc, rates, ts):
    """Anything without a dedicated adapter: just a reachability check (CPU/mem still
    come from docker stats in gBuilder's Live view)."""
    return {"up": _tcp_up(svc["host"], svc.get("port", 80)), "kpis": []}


ADAPTERS = {
    "cache": collect_cache,
    "queue": collect_queue,
    "database": collect_database,
    "proxy": collect_proxy,
    "load_balancer": collect_loadbalancer,
    "messaging": collect_nats,
}


def snapshot(config: dict, rates: Rates) -> dict:
    ts = time.time()
    services: dict[str, dict] = {}
    up = 0
    total_rps = 0.0
    max_lat = 0.0
    for svc in config.get("services", []):
        fn = ADAPTERS.get(svc.get("type"), collect_generic)
        try:
            data = fn(svc, rates, ts)
        except Exception as e:                                  # noqa: BLE001
            data = {"up": False, "kpis": [], "error": str(e)}
        kpis = data.get("kpis", [])
        services[svc["name"]] = {
            "type": svc.get("type"), "up": bool(data.get("up")),
            "kpis": kpis, "primary": kpis[0] if kpis else None}
        if data.get("up"):
            up += 1
        if data.get("rps"):
            total_rps += float(data["rps"])
        if data.get("latency_ms"):
            max_lat = max(max_lat, float(data["latency_ms"]))
    return {"ts": ts, "services": services,
            "totals": {"services_up": up, "services_total": len(config.get("services", [])),
                       "rps": round(total_rps, 1), "latency_ms": round(max_lat, 1)}}


# --------------------------------------------------------------------------- #
# HTTP server + poll loop
# --------------------------------------------------------------------------- #
_LATEST: dict = {"ts": 0, "services": {}, "totals": {}}


def _poll_loop(config: dict) -> None:
    rates = Rates()
    global _LATEST
    while True:
        try:
            _LATEST = snapshot(config, rates)
        except Exception as e:                                  # noqa: BLE001
            _LATEST = {"ts": time.time(), "services": {}, "totals": {}, "error": str(e)}
        time.sleep(POLL)


def _to_prometheus(snap: dict) -> str:
    lines = []
    for name, s in snap.get("services", {}).items():
        lines.append(f'gini_service_up{{service="{name}",type="{s.get("type")}"}} '
                     f'{1 if s.get("up") else 0}')
        for k in s.get("kpis", []):
            metric = "gini_" + str(k["label"]).replace(" ", "_")
            lines.append(f'{metric}{{service="{name}"}} {k["value"]}')
    return "\n".join(lines) + "\n"


def main() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    config = json.loads(os.environ.get("FABRIC_CONFIG", '{"services":[]}'))
    threading.Thread(target=_poll_loop, args=(config,), daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):           # quiet
            pass

        def do_GET(self):
            if self.path.startswith("/metrics.json"):
                body = json.dumps(_LATEST).encode()
                ctype = "application/json"
            elif self.path.startswith("/metrics"):
                body = _to_prometheus(_LATEST).encode()
                ctype = "text/plain; version=0.0.4"
            else:
                body = b'gini cloud fabric: /metrics.json or /metrics\n'
                ctype = "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"[cloudfabric] watching {len(config.get('services', []))} services on :{PORT}",
          flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

"""Cloud service catalog — maps a palette element to a real, off-the-shelf container.

The cloud-course half of GINI: instead of simulating these on the custom UDP/tun
fabric (that's for the networking course), each managed-cloud element runs as a normal
container from a public image on the shared `gini` Docker network, reachable by its
service name — just like real cloud service discovery. Students draw the architecture
in gBuilder, press Run, and get actual services they can inspect, drive, and visualise.

Each `Port(container, label, web)` with web=True is an HTTP console worth opening in a
browser; the compiler assigns a unique host port so several services don't collide.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Port:
    container: int
    label: str
    web: bool = False          # True => an HTTP console to open in the browser
    path: str = ""             # console URL path (some UIs aren't at the root, e.g. Fortio)


@dataclass(frozen=True)
class CloudService:
    image: str
    summary: str               # one line shown in the inspector / to GINI
    command: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    ports: tuple[Port, ...] = ()


# element type_key -> the container that backs it
CATALOG: dict[str, CloudService] = {
    "object_store": CloudService(
        image="minio/minio:latest",
        summary="MinIO — an S3-compatible object store. Use the AWS CLI or `mc`.",
        command=("server", "/data", "--console-address", ":9001"),
        env={"MINIO_ROOT_USER": "minioadmin", "MINIO_ROOT_PASSWORD": "minioadmin"},
        ports=(Port(9001, "console", web=True), Port(9000, "s3"))),
    "database": CloudService(
        image="postgres:16-alpine",
        summary="PostgreSQL managed database. Connect with psql on port 5432.",
        env={"POSTGRES_USER": "gini", "POSTGRES_PASSWORD": "gini", "POSTGRES_DB": "app"},
        ports=(Port(5432, "postgres"),)),
    "queue": CloudService(
        image="rabbitmq:3-management-alpine",
        summary="RabbitMQ message broker with a management console.",
        ports=(Port(15672, "console", web=True), Port(5672, "amqp"))),
    "load_balancer": CloudService(
        image="nginx:alpine",
        summary="nginx reverse proxy / load balancer fronting backend targets.",
        ports=(Port(80, "http", web=True),)),
    "registry": CloudService(
        image="registry:2",
        summary="Docker/OCI image registry serving container images on port 5000.",
        ports=(Port(5000, "registry"),)),

    # --- serverless front door ---
    "api_gateway": CloudService(
        image="traefik:v3.1",
        summary="API Gateway (Traefik) — maps a URL path to each connected serverless "
                "Function. Open the dashboard to watch routing live.",
        command=("--api.insecure=true", "--entrypoints.web.address=:80",
                 "--metrics.prometheus=true"),
        ports=(Port(8080, "dashboard", web=True, path="/dashboard/"), Port(80, "http"))),

    # --- edge & traffic ---
    "proxy": CloudService(
        image="traefik:v3.1",
        summary="Traefik reverse proxy / edge router. Dashboard shows routing live.",
        command=("--api.insecure=true", "--entrypoints.web.address=:80",
                 "--metrics.prometheus=true"),   # exposes /metrics for the cloud fabric
        ports=(Port(8080, "dashboard", web=True, path="/dashboard/"), Port(80, "http"))),
    "web_app": CloudService(
        image="nginxdemos/hello:latest",
        summary="A demo web backend that displays its hostname/IP — put several behind "
                "a load balancer to see requests spread across them.",
        ports=(Port(80, "http", web=True),)),

    # --- streaming & messaging ---
    "stream": CloudService(
        image="redpandadata/redpanda:latest",
        summary="Redpanda — a Kafka-API event streaming log. Produce/consume with any "
                "Kafka client against port 9092.",
        command=("redpanda", "start", "--mode", "dev-container", "--smp", "1",
                 "--advertise-kafka-addr", "{svc}"),
        ports=(Port(9092, "kafka"), Port(9644, "admin"))),
    "messaging": CloudService(
        image="nats:latest",
        summary="NATS pub/sub messaging. Clients connect on 4222; monitoring on 8222.",
        command=("-m", "8222"),
        ports=(Port(8222, "monitor", web=True), Port(4222, "nats"))),

    # --- cache & NoSQL ---
    "cache": CloudService(
        image="redis:7-alpine",
        summary="Redis in-memory store. `redis-cli -h <name>` to set/get keys.",
        ports=(Port(6379, "redis"),)),
    "nosql": CloudService(
        image="mongo:7",
        summary="MongoDB document database (user/pass gini). Connect with mongosh.",
        env={"MONGO_INITDB_ROOT_USERNAME": "gini", "MONGO_INITDB_ROOT_PASSWORD": "gini"},
        ports=(Port(27017, "mongo"),)),

    # --- observability ---
    "metrics": CloudService(
        image="prom/prometheus:latest",
        summary="Prometheus — scrapes and stores metrics; query them in PromQL.",
        ports=(Port(9090, "console", web=True),)),
    "dashboard": CloudService(
        image="grafana/grafana:latest",
        summary="Grafana dashboards — opens straight to a live view (no login in the lab).",
        # No-login teaching setup: anonymous Admin + skip the login form. The home-dashboard
        # path is set by the compiler's observability auto-wiring ONLY once the dashboard is
        # actually provisioned (otherwise Grafana errors on a missing home dashboard).
        # admin/admin still works if you want to edit/save.
        env={"GF_SECURITY_ADMIN_USER": "admin", "GF_SECURITY_ADMIN_PASSWORD": "admin",
             "GF_AUTH_ANONYMOUS_ENABLED": "true", "GF_AUTH_ANONYMOUS_ORG_ROLE": "Admin",
             "GF_AUTH_DISABLE_LOGIN_FORM": "true"},
        ports=(Port(3000, "console", web=True),)),
    "tracing": CloudService(
        image="jaegertracing/all-in-one:latest",
        summary="Jaeger — view distributed request traces across services.",
        ports=(Port(16686, "console", web=True),)),

    # --- workload & testing ---
    "load_generator": CloudService(
        image="fortio/fortio:latest",
        summary="Fortio load generator. Open the UI to fire HTTP/gRPC load at a target "
                "and watch QPS and latency histograms.",
        command=("server",),
        ports=(Port(8080, "console", web=True, path="/fortio/"),)),
}


def is_service(type_key: str) -> bool:
    return type_key in CATALOG


def service_for(type_key: str) -> CloudService | None:
    return CATALOG.get(type_key)

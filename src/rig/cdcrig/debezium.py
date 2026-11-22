"""Debezium Server: configuration, control through marker files, metrics.

Debezium runs in its own container in the same job replica (decision 0010).
Both containers mount /shared. Protocol (see debezium/control.sh):
- write /shared/debezium/application.properties, then create .../start
- control.sh starts Debezium and writes .../running
- create .../stop: control.sh sends SIGTERM, waits, writes .../stopped
- create /shared/done: control.sh stops Debezium if running and exits 0
- /shared/heartbeat older than ten minutes: control.sh exits non-zero
"""
from __future__ import annotations

import re
import time
import urllib.request
from pathlib import Path

from .settings import Settings


def properties(s: Settings) -> str:
    """Debezium Server configuration. No keys or passwords: both connections use the managed identity (decision 0007)."""
    p: list[tuple[str, str]] = [
        # Sink: Kafka protocol to Event Hubs, Entra token through OAUTHBEARER.
        ("debezium.sink.type", "kafka"),
        ("debezium.sink.kafka.producer.bootstrap.servers", f"{s.eventHubsNamespace}.servicebus.windows.net:9093"),
        ("debezium.sink.kafka.producer.security.protocol", "SASL_SSL"),
        ("debezium.sink.kafka.producer.sasl.mechanism", "OAUTHBEARER"),
        ("debezium.sink.kafka.producer.sasl.jaas.config", "org.apache.kafka.common.security.oauthbearer.OAuthBearerLoginModule required;"),
        ("debezium.sink.kafka.producer.sasl.login.callback.handler.class", "cdcimpact.EventHubsTokenHandler"),
        ("debezium.sink.kafka.producer.key.serializer", "org.apache.kafka.common.serialization.StringSerializer"),
        ("debezium.sink.kafka.producer.value.serializer", "org.apache.kafka.common.serialization.StringSerializer"),
        # Event Hubs Standard does not support Kafka compression.
        ("debezium.sink.kafka.producer.compression.type", "none"),
        ("debezium.sink.kafka.producer.max.request.size", "1000000"),
        ("debezium.sink.kafka.producer.linger.ms", "20"),
        # Source: SQL Server connector, database 1, outbox table only, no initial snapshot.
        ("debezium.source.connector.class", "io.debezium.connector.sqlserver.SqlServerConnector"),
        ("debezium.source.database.hostname", s.sqlServer),
        ("debezium.source.database.port", "1433"),
        ("debezium.source.database.names", s.database1),
        ("debezium.source.driver.encrypt", "true"),
        ("debezium.source.driver.trustServerCertificate", "false"),
        ("debezium.source.driver.accessTokenCallbackClass", "cdcimpact.SqlTokenCallback"),
        ("debezium.source.topic.prefix", "cdc"),
        ("debezium.source.table.include.list", "dbo.Outbox"),
        ("debezium.source.snapshot.mode", "no_data"),
        ("debezium.source.include.schema.changes", "false"),
        # Azure SQL Database has no SQL Server Agent; CDC runs on its own scheduler.
        ("debezium.source.database.sqlserver.agent.status.query", "SELECT 1"),
        ("debezium.source.offset.storage", "org.apache.kafka.connect.storage.FileOffsetBackingStore"),
        ("debezium.source.offset.storage.file.filename", "/debezium/data/offsets.dat"),
        ("debezium.source.offset.flush.interval.ms", "10000"),
        ("debezium.source.schema.history.internal", "io.debezium.storage.file.history.FileSchemaHistory"),
        ("debezium.source.schema.history.internal.file.filename", "/debezium/data/schema-history.dat"),
        ("debezium.format.key", "json"),
        ("debezium.format.value", "json"),
        ("debezium.format.key.schemas.enable", "false"),
        ("debezium.format.value.schemas.enable", "false"),
        ("quarkus.log.level", "INFO"),
    ]
    return "".join(f"{k}={v}\n" for k, v in p)


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text format; keeps only debezium metric lines."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.lower().startswith("debezium"):
            continue
        name, _, value = line.rpartition(" ")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            continue
    return out


def _norm(s: str) -> str:
    return s.replace("_", "").lower()


def find(metrics: dict[str, float], *fragments: str) -> float | None:
    """First value whose name contains every fragment, ignoring case and underscores."""
    wanted = [_norm(f) for f in fragments]
    for k, v in metrics.items():
        key = _norm(k)
        if all(w in key for w in wanted):
            return v
    return None


def scrape(url: str, timeout: float = 5) -> dict[str, float]:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - localhost only
        return parse_metrics(r.read().decode())


class Control:
    """Starts and stops Debezium in the sibling container through marker files."""

    def __init__(self, shared: str):
        self.root = Path(shared)
        self.dir = self.root / "debezium"

    def prepare(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        # The Debezium container runs as a different user.
        for p in (self.root, self.dir):
            p.chmod(0o777)

    def heartbeat(self) -> None:
        (self.root / "heartbeat").write_text(str(time.time()))

    def _clear(self, *names: str) -> None:
        for n in names:
            (self.dir / n).unlink(missing_ok=True)

    def start(self, props: str, timeout: float) -> None:
        self._clear("running", "stopped", "stop", "failed")
        (self.dir / "application.properties").write_text(props)
        (self.dir / "start").write_text(str(time.time()))
        self._wait_for("running", timeout)

    def stop(self, timeout: float = 120) -> None:
        if not (self.dir / "running").exists():
            return
        (self.dir / "stop").write_text(str(time.time()))
        self._wait_for("stopped", timeout)

    def running(self) -> bool:
        return (self.dir / "running").exists() and not (self.dir / "failed").exists()

    def done(self) -> None:
        (self.root / "done").write_text(str(time.time()))

    def _wait_for(self, marker: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (self.dir / marker).exists():
                return
            if (self.dir / "failed").exists():
                raise RuntimeError("Debezium control reported failure: " + (self.dir / "failed").read_text())
            time.sleep(1)
        raise TimeoutError(f"Debezium control did not write {marker} within {timeout:.0f} s")

    def log_tail(self, lines: int = 50) -> str:
        log = self.dir / "debezium.log"
        if not log.exists():
            return ""
        return "\n".join(log.read_text(errors="replace").splitlines()[-lines:])

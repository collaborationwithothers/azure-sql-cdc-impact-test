"""Run settings: a mode preset (design section 7) overlaid with RIG_SETTINGS."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, replace

PRESETS: dict[str, dict] = {
    # Spike: prove the mechanics (design section 12) with minimal pool time.
    "spike": {"configurations": ["C"], "steps": [100], "warmupSeconds": 30, "measureSeconds": 60, "seedGb": 0.25, "purgeMinRows": 20000},
    # Smoke: one configuration, two steps, about 10 minutes.
    "smoke": {"configurations": ["C"], "steps": [100, 200], "warmupSeconds": 60, "measureSeconds": 120, "seedGb": 0.5, "purgeMinRows": 50000},
    # Quick run: 100 to 500 tps, the owner's realistic range (decision 0017).
    "quick": {"configurations": ["A", "B", "C"], "steps": [100, 200, 300, 400, 500], "warmupSeconds": 60, "measureSeconds": 120, "seedGb": 10},
    # Confirming run: configuration C at the same steps (decision 0017).
    "confirm": {"configurations": ["C"], "steps": [100, 200, 300, 400, 500], "warmupSeconds": 60, "measureSeconds": 120, "seedGb": 10},
    "targeted": {},
}


@dataclass(frozen=True)
class Settings:
    runId: str = "local"
    mode: str = "smoke"
    sqlServer: str = ""
    database1: str = "cdcdb1"
    database2: str = "cdcdb2"
    clientId: str = ""
    # Container tests only: an ODBC connection string with {db} for the database.
    odbcTemplate: str | None = None
    resultsAccount: str = ""
    resultsContainer: str = "results"
    resultsDirectory: str = "/tmp/results"
    sharedDirectory: str = "/shared"
    eventHubsNamespace: str = ""
    eventHubName: str = "cdc.cdcdb1.dbo.Outbox"
    debeziumMetricsUrl: str = "http://localhost:9404/metrics"
    loadgenPath: str = "/opt/loadgen/loadgen"
    seedGb: float = 10
    seedBatchRows: int = 500_000
    matters: int = 50_000
    configurations: list[str] = field(default_factory=lambda: ["A", "B", "C"])
    steps: list[int] = field(default_factory=lambda: [100, 200, 400, 800, 1200, 1600])
    repeats: int = 1
    warmupSeconds: int = 60
    measureSeconds: int = 120
    stopRatio: float = 0.95
    database2Rate: int = 50
    payloadChars: int = 4096
    payloadType: str = "nvarchar(max)"
    maxPoolSize: int = 200
    telemetry: bool = True
    runPurge: bool = True
    purgeBatch: int = 5000
    purgeMinRows: int = 200_000
    runCleanup: bool = True
    collectorIntervalSeconds: int = 10
    logDrainTimeoutSeconds: int = 900
    catchUpTimeoutSeconds: int = 900
    debeziumStartTimeoutSeconds: int = 300
    # none or fail. "fail" raises after setup, to prove the destroy path.
    injectFailure: str = "none"

    def validate(self) -> None:
        if not self.steps or any(s <= 0 for s in self.steps):
            raise ValueError("steps must be positive")
        if self.mode in ("quick", "confirm") and self.measureSeconds < 120:
            raise ValueError("design section 7: the measured window is at least 2 minutes for quick and confirming runs")
        if self.payloadType not in ("nvarchar(max)", "varchar(max)"):
            raise ValueError("payloadType")
        if not self.configurations or any(c not in ("A", "B", "C") for c in self.configurations):
            raise ValueError("configurations must be A, B or C")
        if not 0 < self.stopRatio <= 1:
            raise ValueError("stopRatio")
        if self.injectFailure not in ("none", "fail"):
            raise ValueError("injectFailure")

    def public(self) -> dict:
        """Settings without names or identifiers, safe to publish."""
        return asdict(replace(self, sqlServer="", clientId="", resultsAccount="", eventHubsNamespace="", odbcTemplate=None))


def resolve(text: str) -> Settings:
    given = json.loads(text)
    if not isinstance(given, dict):
        raise ValueError("RIG_SETTINGS is not a JSON object")
    mode = given.get("mode", "smoke")
    if mode not in PRESETS:
        raise ValueError(f"unknown mode {mode}")
    merged = {**PRESETS[mode], **given, "mode": mode}
    known = {f.name for f in fields(Settings)}
    unknown = set(merged) - known
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    s = Settings(**merged)
    if not s.clientId and os.environ.get("AZURE_CLIENT_ID"):
        s = replace(s, clientId=os.environ["AZURE_CLIENT_ID"])
    return s

"""Reads one run's raw result directory (src/rig/cdcrig/sink.py) without trusting it.

Records are JSON Lines: {"ts","run_id","phase","configuration","step_tps","repeat","data"}.
db2_windows.jsonl is written by the load generator itself and has no envelope:
{"window_start","window_end","result":{...},"achieved_rate","telemetry"}.
A malformed line is skipped and reported, never fatal.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

STREAMS = (
    "steps", "db2_windows", "db_resource_stats", "pool_resource_stats", "db_wait_stats", "os_wait_stats",
    "file_stats", "log_space", "table_sizes", "perf_counters", "governance", "purge", "purge_topup",
    "cdc_cleanup", "catchup", "checks", "settings", "reset", "seed", "environment", "cdc_scan_sessions",
    "cdc_errors", "collector_errors",
)

# Streams the report cannot do without. Others are optional.
REQUIRED = ("steps", "settings")


def ts(value) -> pd.Timestamp | None:
    """UTC timestamp. DMV datetimes come without a zone; Microsoft documents them as UTC."""
    if value is None or value == "":
        return None
    try:
        t = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert("UTC")


def num(value) -> float:
    """DMV decimals arrive as strings (the sink serialises Decimal with str)."""
    if value is None or isinstance(value, bool):
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


@dataclass
class Run:
    run_id: str
    directory: Path
    streams: dict[str, list[dict]] = field(default_factory=dict)
    manifest: dict | None = None
    eventhubs: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def get(self, stream: str) -> list[dict]:
        return self.streams.get(stream, [])

    @property
    def settings(self) -> dict:
        records = self.get("settings")
        return records[-1].get("data", {}) if records else {}


def read_jsonl(path: Path, warnings: list[str]) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"{path.name} line {n}: not valid JSON, skipped")
            continue
        if not isinstance(record, dict):
            warnings.append(f"{path.name} line {n}: not a JSON object, skipped")
            continue
        out.append(record)
    return out


def load_run(results_dir: str | Path, run_id: str) -> Run:
    directory = Path(results_dir) / run_id
    if not directory.is_dir():
        raise FileNotFoundError(f"no result directory for run {run_id}")
    run = Run(run_id=run_id, directory=directory)
    for stream in STREAMS:
        records = read_jsonl(directory / f"{stream}.jsonl", run.warnings)
        if stream != "db2_windows":
            foreign = [r for r in records if r.get("run_id") not in (None, run_id)]
            if foreign:
                run.warnings.append(f"{stream}: {len(foreign)} records carry another run ID; ignored")
                records = [r for r in records if r.get("run_id") in (None, run_id)]
        run.streams[stream] = records
    for stream in REQUIRED:
        if not run.streams.get(stream):
            run.warnings.append(f"{stream}.jsonl is missing or empty")
    for name, attr in (("manifest.json", "manifest"), ("eventhubs_metrics.json", "eventhubs")):
        p = directory / name
        if p.exists():
            try:
                setattr(run, attr, json.loads(p.read_text(errors="replace")))
            except json.JSONDecodeError:
                run.warnings.append(f"{name}: not valid JSON, ignored")
    return run

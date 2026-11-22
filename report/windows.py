"""Matches collector records to load steps by timestamp.

A step's window is [measure_start, measure_end] from steps.jsonl (the load
generator's own clock, UTC). The collector polls every few seconds, so:

- Resource stats (sys.dm_db_resource_stats, sys.dm_elastic_pool_resource_stats)
  are rows of averages over short intervals ending at end_time. A row counts
  for a step only when its whole interval lies inside the window.
- Cumulative counters (file stats, wait stats, performance counters) count for
  a step as the difference between the last poll at or before the window start
  and the first poll at or after the window end. If either is missing, the
  nearest poll inside the window is used, and the covered seconds say so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .load import Run, num, ts

TOLERANCE = pd.Timedelta(seconds=1)

RESOURCE_METRICS = ("avg_cpu_percent", "avg_data_io_percent", "avg_log_write_percent", "max_worker_percent",
                    "max_session_percent", "avg_memory_usage_percent", "avg_storage_percent", "instance_vcores")


@dataclass
class Step:
    run_id: str
    configuration: str
    step_tps: int
    repeat: int
    start: pd.Timestamp
    end: pd.Timestamp
    target: float
    scheduled: float
    completed: float
    errors: float
    shed: float
    achieved: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    client_cpu_percent: float
    processor_count: float
    warmup_completed: float
    debezium_lag_ms: float
    debezium: dict = field(default_factory=dict, repr=False)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def ratio(self) -> float:
        return self.achieved / self.target if self.target else math.nan

    @property
    def carries(self) -> bool:
        return carries(self.achieved, self.target, self.errors, self.shed)


def carries(achieved: float, target: float, errors: float, shed: float, ratio: float = 0.95) -> bool:
    """A step carries its load when it achieved at least 95 percent of target with no errors and nothing shed."""
    if any(math.isnan(x) for x in (achieved, target, errors, shed)) or target <= 0:
        return False
    return achieved >= ratio * target and errors + shed == 0


def _debezium_value(metrics: dict, *fragments: str) -> float:
    """Same matching as cdcrig.debezium.find: every fragment, ignoring case and underscores."""
    wanted = [f.replace("_", "").lower() for f in fragments]
    for k, v in (metrics or {}).items():
        key = k.replace("_", "").lower()
        if all(w in key for w in wanted):
            return num(v)
    return math.nan


def parse_steps(run: Run) -> list[Step]:
    steps: list[Step] = []
    for r in run.get("steps"):
        d = r.get("data") or {}
        m = d.get("measured") or {}
        lat = m.get("latency") or {}
        start, end = ts(d.get("measure_start")), ts(d.get("measure_end"))
        if start is None or end is None or r.get("configuration") is None or r.get("step_tps") is None:
            run.warnings.append("steps: a record without measure_start, measure_end, configuration or step_tps was skipped")
            continue
        deb = d.get("debezium") or {}
        steps.append(Step(
            run_id=run.run_id, configuration=str(r["configuration"]), step_tps=int(r["step_tps"]),
            repeat=int(r.get("repeat") or 1), start=start, end=end,
            target=num(m.get("target_rate", r["step_tps"])), scheduled=num(m.get("scheduled")),
            completed=num(m.get("completed")), errors=num(m.get("errors")), shed=num(m.get("shed")),
            achieved=num(d.get("achieved_rate", m.get("achieved_rate"))),
            p50_ms=num(lat.get("p50_ms")), p95_ms=num(lat.get("p95_ms")), p99_ms=num(lat.get("p99_ms")),
            client_cpu_percent=num(m.get("client_cpu_percent")), processor_count=num(d.get("processor_count")),
            warmup_completed=num((d.get("warmup") or {}).get("completed")),
            debezium_lag_ms=_debezium_value(deb, "MilliSecondsBehindSource", "streaming"),
            debezium=deb,
        ))
    steps.sort(key=lambda s: (s.repeat, s.configuration, s.step_tps))
    return steps


# -- resource stats ---------------------------------------------------------

def resource_frame(run: Run, stream: str) -> pd.DataFrame:
    """One row per (database, end_time), deduplicated across overlapping polls, with an interval start."""
    rows = []
    for r in run.get(stream):
        d = r.get("data") or {}
        for row in d.get("rows") or []:
            t = ts(row.get("end_time"))
            if t is None:
                continue
            rows.append({"database": d.get("database"), "end_time": t, "poll_ts": ts(r.get("ts")),
                         **{k: num(row.get(k)) for k in RESOURCE_METRICS if k in row}})
    if not rows:
        return pd.DataFrame(columns=["database", "end_time", "start_time"])
    df = pd.DataFrame(rows).sort_values(["database", "end_time", "poll_ts"])
    df = df.drop_duplicates(["database", "end_time"], keep="last").reset_index(drop=True)
    parts = []
    for _, g in df.groupby("database", sort=False):
        g = g.sort_values("end_time").copy()
        gap = g["end_time"].diff()
        typical = gap.median() if gap.notna().any() else pd.Timedelta(seconds=15)
        g["start_time"] = g["end_time"] - gap.fillna(typical)
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def rows_in_window(df: pd.DataFrame, database: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    sel = (df["database"] == database) & (df["start_time"] >= start - TOLERANCE) & (df["end_time"] <= end + TOLERANCE)
    return df[sel]


@dataclass
class Stat:
    mean: float
    max: float
    std: float
    n: int
    samples: list[float]


def stat(values) -> Stat:
    v = [float(x) for x in values if not math.isnan(float(x))]
    if not v:
        return Stat(math.nan, math.nan, math.nan, 0, [])
    s = pd.Series(v)
    return Stat(float(s.mean()), float(s.max()), float(s.std(ddof=1)) if len(v) > 1 else math.nan, len(v), v)


# -- cumulative counters ----------------------------------------------------

@dataclass
class Delta:
    seconds: float
    totals: dict
    # Per poll interval: (seconds, {key: delta}). For sample-to-sample jitter.
    intervals: list
    covers_window: bool

    def rate(self, key) -> float:
        if not self.seconds or key not in self.totals:
            return math.nan
        return self.totals[key] / self.seconds

    def interval_rates(self, key) -> list[float]:
        return [d[key] / s for s, d in self.intervals if s > 0 and key in d]


def polls(run: Run, stream: str, database: str, extract) -> list[tuple[pd.Timestamp, dict]]:
    """[(poll time, {key: cumulative value})] for one database, in time order. extract(rows) -> dict."""
    out = []
    for r in run.get(stream):
        d = r.get("data") or {}
        t = ts(r.get("ts"))
        if t is None or d.get("database") != database:
            continue
        out.append((t, extract(d.get("rows") or [])))
    out.sort(key=lambda x: x[0])
    return out


def delta(series: list[tuple[pd.Timestamp, dict]], start: pd.Timestamp, end: pd.Timestamp) -> Delta | None:
    if len(series) < 2:
        return None
    before = [i for i, (t, _) in enumerate(series) if t <= start]
    after = [i for i, (t, _) in enumerate(series) if t >= end]
    inside = [i for i, (t, _) in enumerate(series) if start < t < end]
    i0 = before[-1] if before else (inside[0] if inside else None)
    i1 = after[0] if after else (inside[-1] if inside else None)
    if i0 is None or i1 is None or i1 <= i0:
        return None
    t0, v0 = series[i0]
    t1, v1 = series[i1]
    totals = {k: v1[k] - v0[k] for k in v1 if k in v0}
    if any(v < 0 for v in totals.values()):
        return None  # a counter reset (failover or restart): no valid delta
    intervals = []
    for a, b in zip(series[i0:i1], series[i0 + 1:i1 + 1]):
        intervals.append(((b[0] - a[0]).total_seconds(), {k: b[1][k] - a[1][k] for k in b[1] if k in a[1]}))
    return Delta((t1 - t0).total_seconds(), totals, intervals, bool(before) and bool(after))


def file_values(rows: list[dict]) -> dict:
    """Cumulative IO per file type (ROWS, LOG), summed across files."""
    out: dict = {}
    for row in rows:
        t = row.get("type_desc")
        for f in ("num_of_reads", "num_of_bytes_read", "num_of_writes", "num_of_bytes_written",
                  "io_stall_queued_read_ms", "io_stall_queued_write_ms"):
            v = num(row.get(f))
            if not math.isnan(v):
                out[(t, f)] = out.get((t, f), 0.0) + v
    return out


def wait_values(rows: list[dict]) -> dict:
    out: dict = {}
    for row in rows:
        w = row.get("wait_type")
        for f in ("wait_time_ms", "waiting_tasks_count"):
            v = num(row.get(f))
            if w and not math.isnan(v):
                out[(w, f)] = v
    return out


def perf_values(rows: list[dict]) -> dict:
    """Database-level cumulative counters (PERF_COUNTER_BULK_COUNT rows of the Databases object)."""
    out: dict = {}
    for row in rows:
        if str(row.get("object_name", "")).endswith(":Databases"):
            v = num(row.get("cntr_value"))
            if not math.isnan(v):
                out[row.get("counter_name")] = v
    return out

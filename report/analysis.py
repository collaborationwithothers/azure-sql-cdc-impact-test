"""Per-step metrics, the binding-limit rule, uplift and neighbour impact.

Everything here is computed from one run's raw records. Nothing is estimated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from . import windows as W
from .load import Run, num, ts

THRESHOLD = 90.0  # percent of limit at or above which a limit counts as reached

# Candidate limits for the binding rule: (scope, DMV column, label).
# Memory and storage are excluded: a full buffer pool is normal and does not throttle.
CANDIDATES = (
    ("database 1", "avg_cpu_percent", "CPU"),
    ("database 1", "avg_data_io_percent", "data IO"),
    ("database 1", "avg_log_write_percent", "log write"),
    ("database 1", "max_worker_percent", "workers"),
    ("database 1", "max_session_percent", "sessions"),
    ("pool", "avg_cpu_percent", "CPU"),
    ("pool", "avg_data_io_percent", "data IO"),
    ("pool", "avg_log_write_percent", "log write"),
    ("pool", "max_worker_percent", "workers"),
    ("pool", "max_session_percent", "sessions"),
)

# Wait types that confirm each limit. Database scope reads sys.dm_db_wait_stats
# for database 1; pool scope reads sys.dm_os_wait_stats.
CONFIRMING_WAITS = {
    "CPU": ("SOS_SCHEDULER_YIELD",),
    "data IO": ("PAGEIOLATCH_SH", "PAGEIOLATCH_EX", "PAGEIOLATCH_UP"),
    "log write": {"database 1": ("LOG_RATE_GOVERNOR", "HADR_THROTTLE_LOG_RATE_GOVERNOR"),
                  "pool": ("POOL_LOG_RATE_GOVERNOR", "INSTANCE_LOG_RATE_GOVERNOR")},
    "workers": ("THREADPOOL",),
    "sessions": (),
}
GOVERNOR_WAITS = ("LOG_RATE_GOVERNOR", "POOL_LOG_RATE_GOVERNOR", "INSTANCE_LOG_RATE_GOVERNOR")


@dataclass
class Binding:
    reached: bool
    scope: str | None
    limit: str | None
    percent: float
    confirmed: bool | None
    evidence: str

    @property
    def label(self) -> str:
        if not self.reached:
            return "none reached"
        return f"{self.scope} {self.limit}"


def confirming_waits(scope: str, limit: str) -> tuple[str, ...]:
    w = CONFIRMING_WAITS.get(limit, ())
    return w.get(scope, ()) if isinstance(w, dict) else w


def binding_limit(percents: dict[tuple[str, str], float], db_waits_ms_s: dict[str, float],
                  pool_waits_ms_s: dict[str, float], threshold: float = THRESHOLD) -> Binding:
    """The resource with the highest mean percent of limit in the window; reached when at or above the threshold.

    percents: {(scope, limit label): mean percent}. Waits: {wait type: wait milliseconds per second}.
    Confirmed when a matching wait type accumulated wait time in the window.
    """
    known = {k: v for k, v in percents.items() if not math.isnan(v)}
    if not known:
        return Binding(False, None, None, math.nan, None, "no resource rows inside the window")
    (scope, limit), pct = max(known.items(), key=lambda kv: kv[1])
    governors = {**{f"db {w}": db_waits_ms_s.get(w, 0.0) for w in GOVERNOR_WAITS},
                 **{f"pool {w}": pool_waits_ms_s.get(w, 0.0) for w in GOVERNOR_WAITS}}
    seen = ", ".join(f"{k} {v:.1f} ms/s" for k, v in governors.items() if v and v > 0) or "no governor waits"
    if pct < threshold:
        return Binding(False, None, None, pct, None, f"highest {scope} {limit} {pct:.1f}%; {seen}")
    waits = pool_waits_ms_s if scope == "pool" else db_waits_ms_s
    names = confirming_waits(scope, limit)
    hits = {w: waits.get(w, math.nan) for w in names}
    present = {w: v for w, v in hits.items() if not math.isnan(v) and v > 0}
    if not names:
        confirmed, text = None, "no wait type maps to this limit"
    elif present:
        confirmed, text = True, "confirmed by " + ", ".join(f"{w} {v:.1f} ms/s" for w, v in present.items())
    elif all(math.isnan(v) for v in hits.values()):
        confirmed, text = None, "wait deltas unavailable"
    else:
        confirmed, text = False, "not confirmed: " + ", ".join(names) + " show no wait time"
    return Binding(True, scope, limit, pct, confirmed, f"{text}; {seen}")


@dataclass
class StepMetrics:
    step: W.Step
    db1: dict[str, W.Stat]
    pool: dict[str, W.Stat]
    db2: dict[str, W.Stat]
    db1_log_bytes_s: float
    db1_log_bytes_s_samples: list[float]
    db1_data_write_bytes_s: float
    db1_data_write_bytes_s_samples: list[float]
    db1_data_iops: float
    db1_data_iops_samples: list[float]
    db2_log_bytes_s: float
    db2_data_iops: float
    db1_waits: dict[str, float]
    pool_waits: dict[str, float]
    log_flushed_bytes_s: float
    counter_seconds: float
    counter_covers_window: bool
    binding: Binding
    neighbour: dict = field(default_factory=dict)

    @property
    def tps(self) -> float:
        return self.step.achieved

    def per_txn(self, rate: float) -> float:
        return rate / self.tps if self.tps and not math.isnan(rate) else math.nan


def _waits_ms_s(d: W.Delta | None) -> dict[str, float]:
    if d is None:
        return {}
    return {w: d.totals[(w, f)] / d.seconds for (w, f) in d.totals if f == "wait_time_ms" and d.seconds > 0}


def step_metrics(run: Run, steps: list[W.Step]) -> list[StepMetrics]:
    s = run.settings
    db1, db2 = s.get("database1", "cdcdb1"), s.get("database2", "cdcdb2")
    dbf = W.resource_frame(run, "db_resource_stats")
    poolf = W.resource_frame(run, "pool_resource_stats")
    files1 = W.polls(run, "file_stats", db1, W.file_values)
    files2 = W.polls(run, "file_stats", db2, W.file_values)
    waits1 = W.polls(run, "db_wait_stats", db1, W.wait_values)
    oswaits = W.polls(run, "os_wait_stats", db1, W.wait_values)
    perf1 = W.polls(run, "perf_counters", db1, W.perf_values)
    pool_db = db1  # the collector reads pool-level views through database 1
    neighbours = db2_windows(run)
    out = []
    for st in steps:
        r1 = W.rows_in_window(dbf, db1, st.start, st.end)
        r2 = W.rows_in_window(dbf, db2, st.start, st.end)
        rp = W.rows_in_window(poolf, pool_db, st.start, st.end)
        stats = lambda rows: {c: W.stat(rows[c]) for c in W.RESOURCE_METRICS if c in rows}
        d1, d2 = W.delta(files1, st.start, st.end), W.delta(files2, st.start, st.end)
        dw1, dwo = W.delta(waits1, st.start, st.end), W.delta(oswaits, st.start, st.end)
        dp = W.delta(perf1, st.start, st.end)
        s1, sp = stats(r1), stats(rp)
        percents = {}
        for scope, col, label in CANDIDATES:
            src = s1 if scope == "database 1" else sp
            percents[(scope, label)] = src[col].mean if col in src else math.nan
        wdb, wpool = _waits_ms_s(dw1), _waits_ms_s(dwo)
        rate = lambda d, k: d.rate(k) if d else math.nan
        samples = lambda d, k: d.interval_rates(k) if d else []
        iops = lambda d: (rate(d, ("ROWS", "num_of_reads")) + rate(d, ("ROWS", "num_of_writes"))) if d else math.nan
        iops_samples = []
        if d1:
            iops_samples = [(x.get(("ROWS", "num_of_reads"), 0) + x.get(("ROWS", "num_of_writes"), 0)) / sec
                            for sec, x in d1.intervals if sec > 0]
        out.append(StepMetrics(
            step=st, db1=s1, pool=sp, db2=stats(r2),
            db1_log_bytes_s=rate(d1, ("LOG", "num_of_bytes_written")),
            db1_log_bytes_s_samples=samples(d1, ("LOG", "num_of_bytes_written")),
            db1_data_write_bytes_s=rate(d1, ("ROWS", "num_of_bytes_written")),
            db1_data_write_bytes_s_samples=samples(d1, ("ROWS", "num_of_bytes_written")),
            db1_data_iops=iops(d1), db1_data_iops_samples=iops_samples,
            db2_log_bytes_s=rate(d2, ("LOG", "num_of_bytes_written")), db2_data_iops=iops(d2),
            db1_waits=wdb, pool_waits=wpool,
            log_flushed_bytes_s=rate(dp, "Log Bytes Flushed/sec"),
            counter_seconds=d1.seconds if d1 else math.nan, counter_covers_window=bool(d1 and d1.covers_window),
            binding=binding_limit(percents, wdb, wpool),
            neighbour=neighbour_in(neighbours, st.start, st.end),
        ))
    return out


# -- neighbour (database 2) ---------------------------------------------------

def db2_windows(run: Run) -> pd.DataFrame:
    rows = []
    for r in run.get("db2_windows"):
        res = r.get("result") or {}
        lat = res.get("latency") or {}
        a, b = ts(r.get("window_start")), ts(r.get("window_end"))
        if a is None or b is None:
            continue
        rows.append({"start": a, "end": b, "target": num(res.get("target_rate")), "achieved": num(r.get("achieved_rate")),
                     "errors": num(res.get("errors")), "shed": num(res.get("shed")), "p50_ms": num(lat.get("p50_ms")),
                     "p99_ms": num(lat.get("p99_ms")), "client_cpu_percent": num(res.get("client_cpu_percent"))})
    return pd.DataFrame(rows)


def neighbour_in(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Database 2 windows that lie wholly inside [start, end]: mean rate, mean of window p50 and p99, error sum."""
    if df.empty:
        return {"n": 0}
    w = df[(df["start"] >= start - W.TOLERANCE) & (df["end"] <= end + W.TOLERANCE)]
    if w.empty:
        return {"n": 0}
    return {"n": len(w), "achieved": float(w["achieved"].mean()), "target": float(w["target"].mean()),
            "p50_ms": float(w["p50_ms"].mean()), "p99_ms": float(w["p99_ms"].mean()),
            "p99_max_ms": float(w["p99_ms"].max()), "errors_shed": float((w["errors"] + w["shed"]).sum())}


def neighbour_baseline(run: Run) -> dict:
    """Database 2 while database 1 has no load generator running: the reset phases (log drain and settle)."""
    df = db2_windows(run)
    parts = []
    for r in run.get("reset"):
        end = ts(r.get("ts"))
        secs = num((r.get("data") or {}).get("seconds"))
        if end is None or math.isnan(secs):
            continue
        n = neighbour_in(df, end - pd.Timedelta(seconds=secs), end)
        if n.get("n"):
            parts.append(df[(df["start"] >= end - pd.Timedelta(seconds=secs) - W.TOLERANCE) & (df["end"] <= end + W.TOLERANCE)])
    if not parts:
        return {"n": 0}
    w = pd.concat(parts)
    return {"n": len(w), "achieved": float(w["achieved"].mean()), "target": float(w["target"].mean()),
            "p50_ms": float(w["p50_ms"].mean()), "p99_ms": float(w["p99_ms"].mean()),
            "p99_max_ms": float(w["p99_ms"].max()), "errors_shed": float((w["errors"] + w["shed"]).sum())}


# -- uplift ------------------------------------------------------------------

@dataclass
class Value:
    value: float
    jitter: float  # spread across repeats, or the sample standard deviation within the window with one repeat


@dataclass
class Uplift:
    absolute: float
    percent: float
    jitter: float
    suppressed: str | None  # reason no figure is given


def uplift(x: Value, y: Value) -> Uplift:
    """x minus y. No figure when the difference is not larger than the jitter, or the jitter is unknown."""
    if math.isnan(x.value) or math.isnan(y.value):
        return Uplift(math.nan, math.nan, math.nan, "no data")
    diff = x.value - y.value
    pct = diff / y.value * 100 if y.value else math.nan
    jit = max(x.jitter, y.jitter) if not (math.isnan(x.jitter) or math.isnan(y.jitter)) else math.nan
    if math.isnan(jit):
        return Uplift(diff, pct, jit, "jitter unknown (fewer than 2 samples)")
    if abs(diff) <= jit:
        return Uplift(diff, pct, jit, "difference within jitter")
    return Uplift(diff, pct, jit, None)


def _std(v: list[float]) -> float:
    v = [x for x in v if not math.isnan(x)]
    return float(pd.Series(v).std(ddof=1)) if len(v) > 1 else math.nan


# Uplift metrics: name -> (unit, function(StepMetrics) -> (value, within-window samples))
def _cpu_per_100(m: StepMetrics, scope: str):
    src = m.db1 if scope == "db" else m.pool
    st = src.get("avg_cpu_percent")
    if not st or not m.tps:
        return math.nan, []
    return st.mean / m.tps * 100, [x / m.tps * 100 for x in st.samples]


def _pct(m: StepMetrics, col: str):
    st = m.db1.get(col)
    return (st.mean, st.samples) if st else (math.nan, [])


UPLIFT_METRICS = {
    "log bytes per transaction (database 1 log file)": ("bytes", lambda m: (m.per_txn(m.db1_log_bytes_s),
                                                                         [m.per_txn(x) for x in m.db1_log_bytes_s_samples])),
    "data bytes written per transaction (database 1 data files)": ("bytes", lambda m: (
        m.per_txn(m.db1_data_write_bytes_s), [m.per_txn(x) for x in m.db1_data_write_bytes_s_samples])),
    "data IOPS per 100 tps (database 1 data files)": ("IOPS", lambda m: (
        m.per_txn(m.db1_data_iops) * 100, [m.per_txn(x) * 100 for x in m.db1_data_iops_samples])),
    "database 1 CPU % per 100 tps": ("% of database limit", lambda m: _cpu_per_100(m, "db")),
    "pool CPU % per 100 tps": ("% of pool limit", lambda m: _cpu_per_100(m, "pool")),
    "database 1 data IO %": ("% of database limit", lambda m: _pct(m, "avg_data_io_percent")),
    "database 1 log write %": ("% of database limit", lambda m: _pct(m, "avg_log_write_percent")),
}

PAIRS = (("C", "B", "CDC alone"), ("B", "A", "outbox alone"), ("C", "A", "outbox and CDC"))


def config_value(ms: list[StepMetrics], fn) -> Value:
    """Mean over repeats. Jitter: spread across repeats when there are several, else within-window std."""
    vals = [fn(m) for m in ms]
    xs = [v for v, _ in vals if not math.isnan(v)]
    if not xs:
        return Value(math.nan, math.nan)
    mean = sum(xs) / len(xs)
    if len(xs) > 1:
        return Value(mean, max(xs) - min(xs))
    return Value(mean, _std(vals[0][1]))


def uplift_rows(metrics: list[StepMetrics]) -> list[dict]:
    """One row per (pair, step, metric) where both configurations carried the load in every repeat."""
    by = {}
    for m in metrics:
        by.setdefault((m.step.configuration, m.step.step_tps), []).append(m)
    rows = []
    for x, y, meaning in PAIRS:
        steps = sorted({t for (c, t) in by if c == x} & {t for (c, t) in by if c == y})
        for t in steps:
            mx, my = by[(x, t)], by[(y, t)]
            if not all(m.step.carries for m in mx + my):
                rows.append({"pair": f"{x} - {y}", "meaning": meaning, "step": t, "metric": None,
                             "note": "not compared: a configuration did not carry the load"})
                continue
            for name, (unit, fn) in UPLIFT_METRICS.items():
                vx, vy = config_value(mx, fn), config_value(my, fn)
                rows.append({"pair": f"{x} - {y}", "meaning": meaning, "step": t, "metric": name, "unit": unit,
                             "x": vx, "y": vy, "uplift": uplift(vx, vy), "repeats": min(len(mx), len(my))})
    return rows


# -- purge, cleanup, catch-up ------------------------------------------------------

def catchup_checks(run: Run, steps: list[W.Step]) -> list[dict]:
    """Catch-up records with the change rows that should exist by then.

    The orchestrator declares catch-up when Debezium has seen as many events as
    the change table holds. If capture has not yet copied every committed
    insert into the change table, that test passes early and the time
    understates catch-up. Expected change rows: one per committed outbox insert
    in the ladder (warm-up and measured), plus top-up rows, plus one per purged
    row (a delete is captured too).
    """
    out = []
    by_repeat: dict[int, float] = {}
    for st in steps:
        if st.configuration == "C":
            add = (0 if math.isnan(st.warmup_completed) else st.warmup_completed) + (0 if math.isnan(st.completed) else st.completed)
            by_repeat[st.repeat] = by_repeat.get(st.repeat, 0) + add
    topup = {int(r.get("repeat") or 1): num((r.get("data") or {}).get("added")) for r in run.get("purge_topup")
             if r.get("configuration") == "C"}
    purged = {int(r.get("repeat") or 1): num((r.get("data") or {}).get("rows_deleted")) for r in run.get("purge")
              if r.get("configuration") == "C"}
    for r in run.get("catchup"):
        d = r.get("data") or {}
        rep = int(r.get("repeat") or 1)
        label = d.get("label")
        expected = by_repeat.get(rep, math.nan)
        if label in ("topup", "purge"):
            expected += topup.get(rep, 0) if not math.isnan(topup.get(rep, 0)) else 0
        if label == "purge":
            expected += purged.get(rep, math.nan)
        ct = num(d.get("change_table_rows"))
        early = not math.isnan(expected) and not math.isnan(ct) and ct < expected
        out.append({"configuration": r.get("configuration"), "repeat": rep, "label": label, "seconds": num(d.get("seconds")),
                    "change_rows": ct, "events_seen": num(d.get("debezium_events_seen")), "caught_up": d.get("caught_up"),
                    "expected_rows": expected, "early": early})
    return out

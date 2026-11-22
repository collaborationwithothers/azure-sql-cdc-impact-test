"""Storage and backup cost of CDC: derived figures and their billing rules.

CDC adds two things that cost storage:

- Change rows. Every captured outbox insert becomes a row in the change table
  `cdc.dbo_Outbox_CT`, kept until the cleanup job's retention expires.
- Transaction log. Configuration C writes more log per transaction than B.
  Log backups carry that log, and point-in-time restore (PITR) keeps them.

Everything measured here comes from cumulative polls bracketing each step
window, as in windows.py. Retention and billing values come from Microsoft
Learn, checked 2026-11-22 (docs/verification/2026-11-22-report-limits.md).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import analysis as A
from . import prices as P
from . import windows as W
from .load import Run, num

CT_SCHEMA, CT_TABLE = "cdc", "dbo_Outbox_CT"

# Verified (Microsoft Learn, 2026-11-22): the CDC cleanup job's default retention is 4320 minutes
# (three days), and Azure SQL Database's CDC page names three days as its default.
CDC_RETENTION_MINUTES = 4320
# Verified: PITR keeps 7 days by default, and Azure keeps full, differential and log backups for up to a
# week longer than the retention, because it purges them in weekly sets.
PITR_RETENTION_DAYS = 7
PITR_EXTRA_DAYS = 7

GB = 1e9  # decimal gigabyte; Azure bills "GB". Decimal gives the larger count, so the higher cost.
DAY_SECONDS = 86400
DAYS_PER_MONTH = 30

LOG_METRIC = "log bytes per transaction (database 1 log file)"

# Billing rules. None: verified, priced. A string: why the item is not priced.
UNVERIFIED: dict[str, str | None] = {
    "backup": None,
    "data storage": ("Microsoft Learn says pool data storage is billed on the configured max data size, but not "
                     "which meter a zone-redundant General Purpose pool pays: the API lists both "
                     f"'{P.DATA_METER}' and '{P.DATA_ZR_METER}', and no page says whether the second replaces or "
                     "adds to the first"),
}


def price(item: str, meter: P.Meter | None) -> tuple[float | None, str]:
    """(price per GB-month, "") when the billing rule is verified and the API gave the meter; else (None, reason)."""
    reason = UNVERIFIED.get(item, "no verified billing rule")
    if reason:
        return None, f"not priced: unverified ({reason})"
    if meter is None:
        return None, "not priced: no price for this meter in the API response"
    return meter.per_gb_month, ""


# -- change-table rows -------------------------------------------------------------

def ct_values(rows: list[dict]) -> dict:
    """Reserved bytes and row count of the change table, from one table_sizes poll."""
    for row in rows:
        if row.get("schema_name") == CT_SCHEMA and row.get("table_name") == CT_TABLE:
            reserved, count = num(row.get("reserved_bytes")), num(row.get("row_count"))
            if not (math.isnan(reserved) or math.isnan(count)):
                return {"reserved": reserved, "rows": count}
    return {}


@dataclass
class ChangeRows:
    step: W.Step
    bytes_per_row: float
    rows_per_s: float
    covers_window: bool


def change_rows(run: Run, steps: list[W.Step]) -> list[ChangeRows]:
    """Per configuration C step: reserved bytes added per change row, from growth across the window.

    Both numerator and denominator count the rows capture wrote during the
    window, so capture lag does not bias the ratio. A shrinking table (the
    cleanup job ran) gives no figure.
    """
    series = W.polls(run, "table_sizes", run.settings.get("database1", "cdcdb1"), ct_values)
    out = []
    for st in steps:
        if st.configuration != "C":
            continue
        d = W.delta(series, st.start, st.end)
        rows = d.totals.get("rows", math.nan) if d else math.nan
        reserved = d.totals.get("reserved", math.nan) if d else math.nan
        bpr = reserved / rows if rows and rows > 0 and not math.isnan(reserved) else math.nan
        rate = rows / d.seconds if d and d.seconds and not math.isnan(rows) else math.nan
        out.append(ChangeRows(st, bpr, rate, bool(d and d.covers_window)))
    return out


@dataclass
class Range:
    mean: float
    low: float
    high: float
    n: int

    @classmethod
    def of(cls, values) -> "Range":
        v = [float(x) for x in values if not math.isnan(float(x))]
        if not v:
            return cls(math.nan, math.nan, math.nan, 0)
        return cls(sum(v) / len(v), min(v), max(v), len(v))


def bytes_per_row(items: list[ChangeRows]) -> dict[int, Range]:
    """{step tps: bytes per change row across repeats}."""
    by: dict[int, list[float]] = {}
    for c in items:
        by.setdefault(c.step.step_tps, []).append(c.bytes_per_row)
    return {t: Range.of(v) for t, v in sorted(by.items())}


def retained_bytes(per_row: float, rows_per_s: float, seconds: float) -> float:
    """Change-table size in steady state: rows arrive at rows_per_s and each stays `seconds`."""
    return per_row * rows_per_s * seconds


# -- extra log ------------------------------------------------------------------------

@dataclass
class LogDiff:
    step_tps: int
    b: A.Value
    c: A.Value
    uplift: A.Uplift
    paired: list[float] = field(default_factory=list)  # C - B within each shared repeat
    repeats: list[int] = field(default_factory=list)
    note: str = ""

    @property
    def paired_range(self) -> Range:
        return Range.of(self.paired)


def log_diffs(metrics: list[A.StepMetrics]) -> list[LogDiff]:
    """C minus B log bytes per transaction, per step, over every repeat both ran (decision 0018).

    Same rules as the uplift table: only steps where both carried their load in
    every repeat; no figure when the difference is not larger than the spread
    across repeats. A counter reset inside a window leaves that repeat without
    a value (windows.delta); the repeats that remain are used and counted.
    """
    unit_fn = A.UPLIFT_METRICS[LOG_METRIC][1]
    by: dict[tuple[str, int], list[A.StepMetrics]] = {}
    for m in metrics:
        by.setdefault((m.step.configuration, m.step.step_tps), []).append(m)
    out = []
    for t in sorted({t for (c, t) in by if c == "C"} & {t for (c, t) in by if c == "B"}):
        mc, mb, shared = A.paired(by[("C", t)], by[("B", t)])
        if not all(m.step.carries for m in mc + mb):
            nan = A.Value(math.nan, math.nan)
            out.append(LogDiff(t, nan, nan, A.Uplift(math.nan, math.nan, math.nan, "not compared"), [], shared,
                               "not compared: a configuration did not carry the load"))
            continue
        vc, vb = A.config_value(mc, unit_fn), A.config_value(mb, unit_fn)
        per = {}
        for r in shared:
            c = [unit_fn(m)[0] for m in mc if m.step.repeat == r]
            b = [unit_fn(m)[0] for m in mb if m.step.repeat == r]
            if c and b and not (math.isnan(c[0]) or math.isnan(b[0])):
                per[r] = c[0] - b[0]
        missing = [r for r in shared if r not in per]
        note = f"no value in repeat {', '.join(map(str, missing))} (counter reset or missing polls)" if missing else ""
        out.append(LogDiff(t, vb, vc, A.uplift(vc, vb), list(per.values()), sorted(per), note))
    return out


def log_gb_per_day(bytes_per_txn: float, tps: float, hours_per_day: float = 24) -> float:
    return bytes_per_txn * tps * hours_per_day * 3600 / GB


def backup_gb(gb_per_day: float, days: float) -> float:
    """Log backup storage held in steady state when each day's log is kept `days` days."""
    return gb_per_day * days

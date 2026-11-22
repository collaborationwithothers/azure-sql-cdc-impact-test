"""Sizing translation: the smallest pool that carries each load.

A pool size that a run used is MEASURED: the step either carried its load or
it did not. Every other size is an EXTRAPOLATION. It scales the measured
per-transaction cost linearly with load, adds database 2's measured use (held
constant), and compares the total with the documented pool limits in
limits.py (see Cost for what is counted at each level). A size
carries in the model when every modelled resource stays below THRESHOLD
percent of its limit, at both database and pool level.

The per-database cap is held at the value the run recorded, and the database
level limits are the recorded ones (sys.dm_user_db_resource_governance). No
document gives those limits for other cap values, so the model does not
change the cap. Workers, sessions and latency are not modelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import limits as L
from .analysis import THRESHOLD, StepMetrics
from .load import Run, num

RESOURCES = ("CPU", "log write", "data IO")
COLUMNS = {"CPU": "avg_cpu_percent", "log write": "avg_log_write_percent", "data IO": "avg_data_io_percent"}


@dataclass
class Applied:
    """Limits recorded by one run (sys.dm_user_db_resource_governance), plus the documented pool size they match."""
    run_id: str
    pool: L.PoolLimits | None
    pool_vcores_source: str
    db_cap_vcores: float
    db_log_rate: float
    db_iops: float
    db_workers: float
    pool_log_rate: float
    pool_iops: float
    pool_workers: float
    instance_log_rate: float

    @property
    def db_limit(self) -> dict[str, float]:
        return {"CPU": self.db_cap_vcores, "log write": self.db_log_rate, "data IO": self.db_iops}

    @property
    def pool_limit(self) -> dict[str, float]:
        v = float(self.pool.vcores) if self.pool else math.nan
        return {"CPU": v, "log write": self.pool_log_rate, "data IO": self.pool_iops}


def applied_limits(run: Run) -> Applied:
    db1 = run.settings.get("database1", "cdcdb1")
    row = {}
    for r in run.get("governance"):
        for g in (r.get("data") or {}).get("rows") or []:
            if g.get("database_name") == db1:
                row = g
    pool = L.identify_pool(num(row.get("pool_max_log_rate")) if row else None, num(row.get("pool_max_io")) if row else None)
    source = "documented size matching the recorded pool log rate and IOPS limits" if pool else "unknown"
    if pool is None:
        run.warnings.append("pool size: the recorded pool limits match no documented General Purpose standard-series size")
    return Applied(run.run_id, pool, source, num(row.get("cpu_limit")), num(row.get("primary_max_log_rate")),
                   num(row.get("primary_group_max_io")), num(row.get("primary_group_max_workers")),
                   num(row.get("pool_max_log_rate")), num(row.get("pool_max_io")), num(row.get("primary_pool_max_workers")),
                   num(row.get("instance_max_log_rate")))


@dataclass
class Cost:
    """Per-transaction cost of database 1's load, and database 2's use, from one measured step.

    Database level: database 1's governed use (percent of its recorded limit) per transaction.
    Pool level: the pool's governed use minus database 2's, per transaction. Work the
    governor counts at pool level but not at database level (internal processes such as
    CDC capture, which per-database limits do not cover) therefore scales with load.
    Database 2's use is held constant: its load is fixed by design.
    """
    run_id: str
    configuration: str
    step_tps: int
    per_txn_db: dict[str, float]    # absolute units per transaction per second: vCores, bytes/s, IOPS
    per_txn_pool: dict[str, float]
    other: dict[str, float]         # database 2, absolute units
    applied: Applied


def cost_from(m: StepMetrics, a: Applied) -> Cost:
    per_db, per_pool, other = {}, {}, {}
    for r in RESOURCES:
        col = COLUMNS[r]
        d, p, n = m.db1.get(col), m.pool.get(col), m.db2.get(col)
        used_db = d.mean / 100 * a.db_limit[r] if d else math.nan
        used_pool = p.mean / 100 * a.pool_limit[r] if p else math.nan
        # Per-database limits apply to every database in the pool (documented), so database 2 shares database 1's.
        used_db2 = n.mean / 100 * a.db_limit[r] if n else math.nan
        per_db[r] = used_db / m.tps if m.tps else math.nan
        per_pool[r] = max(used_pool - used_db2, 0.0) / m.tps if m.tps and not math.isnan(used_pool - used_db2) else math.nan
        other[r] = used_db2
    return Cost(m.step.run_id, m.step.configuration, m.step.step_tps, per_db, per_pool, other, a)


def pick_costs(metrics: list[StepMetrics], applied: dict[str, Applied]) -> dict[str, Cost]:
    """Per configuration: the highest step that carried its load and reached no limit, across runs."""
    out: dict[str, Cost] = {}
    for m in sorted(metrics, key=lambda m: m.step.step_tps):
        if m.step.carries and not m.binding.reached and m.step.run_id in applied:
            c = cost_from(m, applied[m.step.run_id])
            if all(not math.isnan(v) for v in list(c.per_txn_db.values()) + list(c.per_txn_pool.values()) + list(c.other.values())):
                out[m.step.configuration] = c
    return out


def model(cost: Cost, load: float, pool: L.PoolLimits) -> tuple[bool, str]:
    """(carries, reason) for a load on a documented pool size."""
    a = cost.applied
    for r in RESOURCES:
        need = load * cost.per_txn_db[r]
        if need > THRESHOLD / 100 * a.db_limit[r]:
            return False, f"database-level {r} (per-database cap {a.db_cap_vcores:g} vCores)"
    measured = a.pool is not None and pool.vcores == a.pool.vcores
    limit = {"CPU": float(pool.vcores),
             "log write": a.pool_log_rate if measured else pool.max_log_rate_mib_s * L.MIB,
             "data IO": a.pool_iops if measured else float(pool.max_data_iops)}
    for r in RESOURCES:
        if load * cost.per_txn_pool[r] + cost.other[r] > THRESHOLD / 100 * limit[r]:
            return False, f"pool {r}"
    return True, ""


@dataclass
class Cell:
    vcores: int | None
    measured: bool
    note: str
    run_id: str | None

    @property
    def label(self) -> str:
        if self.vcores is None:
            return self.note
        tag = f"measured, run {self.run_id}" if self.measured else f"EXTRAPOLATION from run {self.run_id}"
        return f"{self.vcores} vCores ({tag})"


def smallest(load: int, configuration: str, metrics: list[StepMetrics], costs: dict[str, Cost],
             applied: dict[str, Applied]) -> Cell:
    measured: dict[int, tuple[bool, str]] = {}
    for m in metrics:
        a = applied.get(m.step.run_id)
        if m.step.configuration == configuration and m.step.step_tps == load and a and a.pool:
            prev = measured.get(a.pool.vcores)
            ok = m.step.carries and (prev is None or prev[0])  # every repeat must carry
            measured[a.pool.vcores] = (ok, m.step.run_id)
    unexplained: dict[int, str] = {}
    for m in metrics:
        a = applied.get(m.step.run_id)
        if (m.step.configuration == configuration and m.step.step_tps == load and a and a.pool
                and not m.step.carries and not m.binding.reached):
            unexplained[a.pool.vcores] = m.step.run_id
    worst_fail = max((v for v, (ok, _) in measured.items() if not ok), default=0)
    if worst_fail in unexplained:
        # The load was not carried and no resource limit was reached: scaling the pool is not shown to help.
        return Cell(None, False, f"not carried at {worst_fail} vCores with no resource limit reached "
                                 f"(measured, run {unexplained[worst_fail]}); no size can be inferred", unexplained[worst_fail])
    cost = costs.get(configuration)
    reason = ""
    for pool in L.GP_GEN5_POOLS:
        if pool.vcores <= worst_fail:
            continue  # a size at or below one that measurably failed cannot carry the load
        if pool.vcores in measured:
            ok, run_id = measured[pool.vcores]
            if ok:
                return Cell(pool.vcores, True, "", run_id)
            continue
        if cost is None:
            continue
        ok, reason = model(cost, load, pool)
        if ok:
            return Cell(pool.vcores, False, "", cost.run_id)
        if reason.startswith("database-level") and not any(ok for ok, _ in measured.values()):
            return Cell(None, False, f"none at this cap: {reason} binds (EXTRAPOLATION from run {cost.run_id})", cost.run_id)
    if cost is None and not measured:
        return Cell(None, False, "no data", None)
    if cost is None:
        return Cell(None, False, "not carried where measured; no cost model", None)
    return Cell(None, False, f"none up to {L.GP_GEN5_POOLS[-1].vcores} vCores: {reason} (EXTRAPOLATION)", cost.run_id)

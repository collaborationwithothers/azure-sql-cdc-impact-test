"""Documented limits used by the sizing translation.

Every value here was checked against Microsoft Learn on 2026-11-22. The source
URL and the check are recorded in docs/verification/2026-11-22-report-limits.md.
Do not add a pool size or a limit that is not in that file.

Recorded limits (sys.dm_user_db_resource_governance) win over these for the
pool size a run actually used (AGENTS.md truth rules).
"""
from __future__ import annotations

from dataclasses import dataclass

CHECKED = "2026-11-22"
SOURCE_POOL = "https://learn.microsoft.com/azure/azure-sql/database/resource-limits-vcore-elastic-pools?view=azuresql"

MIB = 1024 * 1024


@dataclass(frozen=True)
class PoolLimits:
    vcores: int
    max_log_rate_mib_s: float
    max_data_iops: int
    max_workers: int
    zone_redundant: bool  # "Multi-AZ: Yes" in the documented table


# General Purpose, standard-series (Gen5), provisioned, elastic pool.
# Columns: vCores, max log rate per pool (MiB/s), max data IOPS per pool,
# max concurrent workers per pool. Every size lists Multi-AZ "Yes".
GP_GEN5_POOLS: tuple[PoolLimits, ...] = tuple(
    PoolLimits(v, log, iops, workers, True)
    for v, log, iops, workers in (
        (2, 12, 1400, 210),
        (4, 24, 2800, 420),
        (6, 36, 4200, 630),
        (8, 48, 5600, 840),
        (10, 60, 7000, 1050),
        (12, 62.5, 8400, 1260),
        (14, 62.5, 9800, 1470),
        (16, 62.5, 11200, 1680),
        (18, 62.5, 12600, 1890),
        (20, 62.5, 14000, 2100),
        (24, 62.5, 16800, 2520),
        (32, 62.5, 22400, 3360),
        (40, 62.5, 28000, 4200),
        (80, 62.5, 32000, 8400),
        (128, 62.5, 40000, 13440),
    )
)


def by_vcores(vcores: int) -> PoolLimits | None:
    for p in GP_GEN5_POOLS:
        if p.vcores == vcores:
            return p
    return None


def identify_pool(pool_max_log_rate_bytes: float | None, pool_max_io: float | None) -> PoolLimits | None:
    """The documented pool size whose log rate and IOPS limits both equal the recorded ones.

    Log rate alone is ambiguous from 12 vCores up (62.5 MiB/s); IOPS separates them.
    Returns None when no size matches both, so the caller never guesses.
    """
    if pool_max_log_rate_bytes is None or pool_max_io is None:
        return None
    matches = [p for p in GP_GEN5_POOLS
               if abs(p.max_log_rate_mib_s * MIB - pool_max_log_rate_bytes) < 1 and int(pool_max_io) == p.max_data_iops]
    return matches[0] if len(matches) == 1 else None

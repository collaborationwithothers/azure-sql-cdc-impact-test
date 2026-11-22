"""Synthetic run directories, written by hand for the report tests.

No real result file is copied here: real files can contain resource names.
Times are on 2030-01-01 so they cannot be mistaken for a real run.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

T0 = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
MIB = 1024 * 1024


def iso(t: datetime, z: bool = True) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + ("Z" if z else "")


class RunWriter:
    def __init__(self, root: Path, run_id: str):
        self.run_id = run_id
        self.dir = root / run_id
        self.dir.mkdir(parents=True)

    def rec(self, stream: str, t: datetime, data, phase="measure", configuration=None, step=None, repeat=1):
        r = {"ts": t.isoformat(), "run_id": self.run_id, "phase": phase, "configuration": configuration,
             "step_tps": step, "repeat": repeat, "data": data}
        with open(self.dir / f"{stream}.jsonl", "a") as f:
            f.write(json.dumps(r) + "\n")

    def raw(self, stream: str, obj) -> None:
        with open(self.dir / f"{stream}.jsonl", "a") as f:
            f.write(json.dumps(obj) + "\n")


def governance_row(db: str, cap=2, db_log=9 * MIB, db_io=640, pool_log=24 * MIB, pool_io=2800) -> dict:
    return {"database_name": db, "cpu_limit": cap, "primary_max_log_rate": db_log, "primary_group_max_io": db_io,
            "primary_group_max_workers": 200, "pool_max_log_rate": pool_log, "pool_max_io": pool_io,
            "primary_pool_max_workers": 420, "instance_max_log_rate": 36 * MIB}


def write_run(root: Path, run_id: str, steps: dict, *, repeats=1, pool_log=24 * MIB, pool_io=2800,
              log_bytes_per_txn=None, cpu_per_100=5.0, db1_log_pct_per_100=10.0, pool_log_pct_per_100=5.0,
              cpu_jitter=0.5, catchup_rows=None) -> RunWriter:
    """steps: {configuration: [(target tps, achieved tps, errors, shed), ...]} run back to back.

    Each step: 3 min apart, measure window 120 s. Resource rows every 15 s; collector polls every 10 s.
    """
    w = RunWriter(root, run_id)
    configs = list(steps)
    all_steps = sorted({s[0] for v in steps.values() for s in v})
    w.rec("settings", T0, {"runId": run_id, "mode": "quick", "database1": "db1", "database2": "db2",
                           "configurations": configs, "steps": all_steps, "repeats": repeats, "warmupSeconds": 60,
                           "measureSeconds": 120, "database2Rate": 50, "payloadChars": 4096,
                           "payloadType": "nvarchar(max)"}, phase="setup")
    w.rec("governance", T0, {"database": "db1", "rows": [governance_row("db2", pool_log=pool_log, pool_io=pool_io),
                                                         governance_row("db1", pool_log=pool_log, pool_io=pool_io)]},
          phase="setup")
    log_bytes_per_txn = log_bytes_per_txn or {"A": 500, "B": 9000, "C": 17000}
    t = T0 + timedelta(minutes=1)
    cum = {"log": 0.0, "rows_w": 0.0, "reads": 0.0, "writes": 0.0, "wait_log": 0.0, "wait_pool": 0.0, "sos": 0.0}
    k = 0

    def poll(at: datetime, cfg, step):
        nonlocal k
        for db in ("db1", "db2"):
            rows = [{"file_id": 1, "type_desc": "ROWS", "num_of_reads": cum["reads"], "num_of_bytes_read": 0,
                     "num_of_writes": cum["writes"], "num_of_bytes_written": cum["rows_w"],
                     "io_stall_queued_read_ms": 0, "io_stall_queued_write_ms": 0},
                    {"file_id": 2, "type_desc": "LOG", "num_of_reads": 0, "num_of_bytes_read": 0, "num_of_writes": 0,
                     "num_of_bytes_written": cum["log"] if db == "db1" else k * 1000.0,
                     "io_stall_queued_read_ms": 0, "io_stall_queued_write_ms": 0}]
            w.rec("file_stats", at, {"database": db, "rows": rows}, configuration=cfg, step=step)
            w.rec("db_wait_stats", at, {"database": db, "rows": [
                {"wait_type": "LOG_RATE_GOVERNOR", "waiting_tasks_count": 0, "wait_time_ms": cum["wait_log"], "signal_wait_time_ms": 0},
                {"wait_type": "SOS_SCHEDULER_YIELD", "waiting_tasks_count": 0, "wait_time_ms": cum["sos"], "signal_wait_time_ms": 0}]},
                configuration=cfg, step=step)
        w.rec("os_wait_stats", at, {"database": "db1", "rows": [
            {"wait_type": "POOL_LOG_RATE_GOVERNOR", "waiting_tasks_count": 0, "wait_time_ms": cum["wait_pool"], "signal_wait_time_ms": 0},
            {"wait_type": "SOS_SCHEDULER_YIELD", "waiting_tasks_count": 0, "wait_time_ms": cum["sos"], "signal_wait_time_ms": 0}]},
            configuration=cfg, step=step)
        k += 1

    for repeat in range(1, repeats + 1):
        for cfg in configs:
            for target, achieved, errors, shed in steps[cfg]:
                start = t + timedelta(seconds=60)
                end = start + timedelta(seconds=120)
                # Resource rows: 15 s intervals, both inside and around the window.
                db_rows, db2_rows, pool_rows = [], [], []
                for i in range(-2, 11):
                    et = start + timedelta(seconds=15 * (i + 1))
                    j = cpu_jitter if i % 2 else -cpu_jitter
                    cpu = cpu_per_100 * achieved / 100 + j
                    logp = db1_log_pct_per_100 * achieved / 100 * (2 if cfg == "C" else 1 if cfg == "B" else 0.1)
                    plog = pool_log_pct_per_100 * achieved / 100 * (2 if cfg == "C" else 1 if cfg == "B" else 0.1) + 1
                    db_rows.append({"end_time": iso(et, z=False), "avg_cpu_percent": str(round(cpu, 2)),
                                    "avg_data_io_percent": "1.00", "avg_log_write_percent": str(round(logp, 2)),
                                    "max_worker_percent": "2.00", "max_session_percent": "0.50",
                                    "avg_memory_usage_percent": "99.00"})
                    db2_rows.append({"end_time": iso(et, z=False), "avg_cpu_percent": "2.00", "avg_data_io_percent": "0.50",
                                     "avg_log_write_percent": "4.00", "max_worker_percent": "1.00",
                                     "max_session_percent": "0.20", "avg_memory_usage_percent": "50.00"})
                    pool_rows.append({"end_time": iso(et, z=False), "avg_cpu_percent": str(round(cpu / 2 + 1, 2)),
                                      "avg_data_io_percent": "1.00", "avg_log_write_percent": str(round(plog, 2)),
                                      "max_worker_percent": "1.00", "max_session_percent": "0.20", "instance_vcores": "4.00"})
                w.rec("db_resource_stats", end + timedelta(seconds=40), {"database": "db1", "rows": list(reversed(db_rows))},
                      configuration=cfg, step=target, repeat=repeat)
                w.rec("db_resource_stats", end + timedelta(seconds=40), {"database": "db2", "rows": list(reversed(db2_rows))},
                      configuration=cfg, step=target, repeat=repeat)
                w.rec("pool_resource_stats", end + timedelta(seconds=40), {"database": "db1", "rows": list(reversed(pool_rows))},
                      configuration=cfg, step=target, repeat=repeat)
                # Cumulative counters, polled every 10 s from 20 s before the window to 20 s after.
                rate_log = log_bytes_per_txn[cfg] * achieved
                for i in range(-2, 15):
                    at = start + timedelta(seconds=10 * i)
                    poll(at, cfg, target)
                    if start <= at < end:
                        cum["log"] += rate_log * 10
                        cum["rows_w"] += 8192 * achieved * 10
                        cum["writes"] += achieved * 10 / 10
                        cum["sos"] += 5
                        if cfg == "C" and target >= 800:
                            cum["wait_pool"] += 4000
                # db2 windows: 10 s each across the step.
                for i in range(0, 12):
                    ws = start + timedelta(seconds=10 * i)
                    w.raw("db2_windows", {"window_start": iso(ws), "window_end": iso(ws + timedelta(seconds=10)),
                                          "result": {"target_rate": 50, "duration_seconds": 10, "scheduled": 500,
                                                     "completed": 500, "errors": 0, "shed": 0, "achieved_rate": 50,
                                                     "latency": {"count": 500, "mean_ms": 5, "p50_ms": 4.0 + target / 1000,
                                                                 "p95_ms": 8, "p99_ms": 10.0 + target / 100, "max_ms": 30},
                                                     "client_cpu_percent": 3.0, "errors_by_type": {}},
                                          "achieved_rate": 50, "telemetry": True})
                completed = achieved * 120
                data = {"warmup_start": iso(t), "measure_start": iso(start), "measure_end": iso(end),
                        "warmup": {"completed": achieved * 60},
                        "measured": {"target_rate": target, "duration_seconds": 120, "scheduled": target * 120,
                                     "completed": completed, "errors": errors, "shed": shed, "achieved_rate": achieved,
                                     "latency": {"count": completed, "mean_ms": 6, "p50_ms": 5.0, "p95_ms": 9.0,
                                                 "p99_ms": 20.0, "max_ms": 80}, "client_cpu_percent": 4.0,
                                     "errors_by_type": {}},
                        "achieved_rate": achieved, "processor_count": 4, "telemetry": True, "stopped": False}
                data["measured"]["scheduled"] = completed + errors + shed
                if cfg == "C":
                    data["debezium"] = {'debezium_metrics_MilliSecondsBehindSource{context="streaming",name="cdc"}': 400.0}
                w.rec("steps", end + timedelta(seconds=1), data, configuration=cfg, step=target, repeat=repeat)
                t = end + timedelta(seconds=60)
            # reset phase after each configuration (db1 idle): db2 windows for the baseline
            w.rec("reset", t + timedelta(seconds=30), {"seconds": 30.0, "log": []}, phase="reset", configuration=cfg, repeat=repeat)
            for i in range(3):
                ws = t + timedelta(seconds=10 * i)
                w.raw("db2_windows", {"window_start": iso(ws), "window_end": iso(ws + timedelta(seconds=10)),
                                      "result": {"target_rate": 50, "completed": 500, "errors": 0, "shed": 0,
                                                 "latency": {"p50_ms": 3.0, "p99_ms": 6.0}, "client_cpu_percent": 3.0},
                                      "achieved_rate": 50, "telemetry": True})
            t += timedelta(seconds=40)
            if cfg == "C" and catchup_rows is not None:
                w.rec("catchup", t, {"label": "ladder", "seconds": 0.1, "change_table_rows": catchup_rows,
                                     "debezium_events_seen": catchup_rows, "caught_up": True},
                      phase="ladder_catchup", configuration="C", repeat=repeat)
    (w.dir / "manifest.json").write_text(json.dumps({"run_id": run_id, "mode": "quick", "status": "succeeded",
                                                     "started": T0.isoformat(), "finished": t.isoformat(), "checks": {}}))
    return w


@pytest.fixture
def results(tmp_path):
    return tmp_path / "results"

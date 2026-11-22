"""Container tests: the orchestrator's SQL logic and the collector against SQL Server.

Needs RIG_TEST_ODBC (an ODBC connection string with {db}) and the load
generator at /opt/loadgen/loadgen. Run by tests/rig/run-container-tests.sh.
A container SQL Server has no elastic pool and no resource governance: these
tests prove mechanics, never performance or limits.
"""
import json
import os
import time
from dataclasses import replace

import pytest

from cdcrig import collector, db, settings
from cdcrig.orchestrator import Orchestrator
from cdcrig.sink import Sink

ODBC = os.environ.get("RIG_TEST_ODBC")
pytestmark = pytest.mark.skipif(not ODBC, reason="RIG_TEST_ODBC not set")


@pytest.fixture(scope="module")
def orch(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("run")
    s = settings.resolve(json.dumps({
        "mode": "smoke", "runId": "container-test", "odbcTemplate": ODBC, "resultsDirectory": str(tmp / "results"),
        "sharedDirectory": str(tmp / "shared"), "seedGb": 0.01, "seedBatchRows": 20000, "matters": 100,
        "warmupSeconds": 1, "measureSeconds": 2, "telemetry": False, "purgeMinRows": 3000, "purgeBatch": 1000,
        "logDrainTimeoutSeconds": 30, "catchUpTimeoutSeconds": 10,
    }))
    master = db.Connector(replace(s, odbcTemplate=ODBC))
    conn = master.connect("master")
    db.execute(conn, "IF DB_ID('cdcdb1') IS NULL CREATE DATABASE cdcdb1; IF DB_ID('cdcdb2') IS NULL CREATE DATABASE cdcdb2;")
    conn.close()
    o = Orchestrator(s, Sink(s.resultsDirectory, s.runId))
    o.control.prepare()
    o.setup_schema()
    o.setup_schema()  # idempotent
    o.seed()
    return o


def _scalar(o, database, sql):
    conn = o.connector.connect(database)
    try:
        return db.scalar(conn, sql)
    finally:
        conn.close()


def test_seed_reaches_target_size(orch):
    reserved = _scalar(orch, "cdcdb1", "SELECT SUM(reserved_page_count) * 8192 FROM sys.dm_db_partition_stats WHERE object_id = OBJECT_ID('dbo.Task')")
    assert reserved >= 0.01 * 1024 ** 3
    assert orch.max_task_id["cdcdb1"] == _scalar(orch, "cdcdb1", "SELECT MAX(TaskId) FROM dbo.Task")


def test_ladder_runs_loadgen_and_writes_step_records(orch):
    o = Orchestrator(replace(orch.s, steps=[20, 40]), orch.sink, orch.connector)
    o.max_task_id = orch.max_task_id
    o.ladder("B", 1)
    lines = (orch.sink.dir / "steps.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["configuration"] == "B" and rec["step_tps"] == 40 and rec["run_id"] == "container-test"
    assert rec["data"]["measured"]["scheduled"] == 80
    assert rec["data"]["measured"]["errors"] == 0
    assert _scalar(orch, "cdcdb1", "SELECT COUNT_BIG(*) FROM dbo.Outbox") > 0


def test_configuration_a_writes_no_outbox_rows(orch):
    orch.reset_database1()
    o = Orchestrator(replace(orch.s, steps=[20]), orch.sink, orch.connector)
    o.max_task_id = orch.max_task_id
    o.ladder("A", 1)
    assert _scalar(orch, "cdcdb1", "SELECT COUNT_BIG(*) FROM dbo.Outbox") == 0


def test_purge_deletes_everything_and_records_log_bytes(orch):
    orch.purge("B", 1)
    assert _scalar(orch, "cdcdb1", "SELECT COUNT_BIG(*) FROM dbo.Outbox") == 0
    rec = json.loads((orch.sink.dir / "purge.jsonl").read_text().splitlines()[-1])["data"]
    assert rec["rows_deleted"] >= 3000 and rec["log_bytes_written"] > 0 and rec["batches"] >= 3


def test_cdc_enable_capture_cleanup_and_reset(orch):
    orch.enable_cdc()
    assert orch.checks["cdc_enable"]["ok"]
    conn = orch.connector.connect("cdcdb1")
    try:
        db.execute(conn, "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) SELECT TOP (50) TaskId, 'T', SYSUTCDATETIME(), N'x' FROM dbo.Task")
        rows = 0
        for _ in range(30):
            try:
                # SQL Server needs the capture job; force a scan rather than wait for it.
                db.execute(conn, "EXEC sys.sp_cdc_scan @maxtrans = 500, @maxscans = 10, @continuous = 0")
            except Exception:  # noqa: BLE001 - the agent job may be scanning already
                pass
            rows = db.scalar(conn, "SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT")
            if rows >= 50:
                break
            time.sleep(1)
        assert rows >= 50
    finally:
        conn.close()
    orch.cdc_cleanup(1)
    rec = json.loads((orch.sink.dir / "cdc_cleanup.jsonl").read_text().splitlines()[-1])["data"]
    assert orch.checks["cdc_cleanup_on_demand"]["ok"], orch.checks["cdc_cleanup_on_demand"]
    assert rec["change_rows_after"] < rec["change_rows_before"]
    orch.reset_database1()
    assert _scalar(orch, "cdcdb1", "SELECT is_cdc_enabled FROM sys.databases WHERE database_id = DB_ID()") == 0


@pytest.mark.parametrize("stream", ["file_stats", "log_space", "table_sizes", "perf_counters", "cdc_scan_sessions", "cdc_errors"])
def test_portable_collector_queries(orch, stream):
    conn = orch.connector.connect("cdcdb1")
    try:
        result = db.rows(conn, collector.PER_DATABASE[stream])
    finally:
        conn.close()
    if stream in ("file_stats", "log_space", "table_sizes"):
        assert result


def test_os_wait_stats_query(orch):
    conn = orch.connector.connect("cdcdb1")
    try:
        db.rows(conn, collector.POOL_LEVEL["os_wait_stats"])
    finally:
        conn.close()


def test_collector_records_and_azure_only_errors(orch):
    c = collector.Collector(orch.connector, orch.sink, ["cdcdb1"], lambda: orch.ctx, 1)
    c.poll_once()
    rec = json.loads((orch.sink.dir / "file_stats.jsonl").read_text().splitlines()[-1])
    assert rec["run_id"] == "container-test" and rec["data"]["rows"]
    # sys.dm_db_resource_stats exists only in Azure SQL Database: recorded as an error, not a crash.
    assert "db_resource_stats" in (orch.sink.dir / "collector_errors.jsonl").read_text()


def test_neighbour_steady_load_writes_windows(orch, tmp_path):
    from cdcrig import loadgen

    out = tmp_path / "db2.jsonl"
    s = replace(orch.s, odbcTemplate=ODBC)
    p = loadgen.start_steady(s, "cdcdb2", "B", 20, orch.max_task_id["cdcdb2"], out, {"role": "neighbour"})
    time.sleep(25)
    p.terminate()
    p.wait(timeout=30)
    lines = out.read_text().splitlines()
    assert len(lines) >= 1
    assert json.loads(lines[0])["result"]["scheduled"] == 200

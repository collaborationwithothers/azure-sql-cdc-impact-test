"""Polls the DMVs listed in design section 6 and writes each result as a record.

Cumulative counters (file stats, wait stats) are written raw; the report
computes deltas. Each query fails on its own: an error is recorded and the
other sources keep running.
"""
from __future__ import annotations

import threading
from typing import Callable

from . import db
from .sink import Context, Sink

GOVERNOR_WAITS = (
    "'LOG_RATE_GOVERNOR','POOL_LOG_RATE_GOVERNOR','INSTANCE_LOG_RATE_GOVERNOR','WRITELOG',"
    "'PAGEIOLATCH_SH','PAGEIOLATCH_EX','PAGEIOLATCH_UP','SOS_SCHEDULER_YIELD','RESOURCE_SEMAPHORE','THREADPOOL'"
)

PER_DATABASE: dict[str, str] = {
    "db_resource_stats":
        "SELECT TOP (8) end_time, avg_cpu_percent, avg_data_io_percent, avg_log_write_percent, avg_memory_usage_percent, "
        "max_worker_percent, max_session_percent, avg_instance_cpu_percent, avg_instance_memory_percent "
        "FROM sys.dm_db_resource_stats ORDER BY end_time DESC",
    "file_stats":
        "SELECT f.file_id, f.type_desc, s.num_of_reads, s.num_of_bytes_read, s.io_stall_read_ms, s.io_stall_queued_read_ms, "
        "s.num_of_writes, s.num_of_bytes_written, s.io_stall_write_ms, s.io_stall_queued_write_ms, s.size_on_disk_bytes "
        "FROM sys.dm_io_virtual_file_stats(DB_ID(), NULL) s JOIN sys.database_files f ON f.file_id = s.file_id",
    "db_wait_stats":
        "SELECT wait_type, waiting_tasks_count, wait_time_ms, signal_wait_time_ms FROM sys.dm_db_wait_stats "
        f"WHERE wait_type IN ({GOVERNOR_WAITS}) OR wait_type LIKE 'HADR_THROTTLE_LOG_RATE%'",
    "log_space":
        "SELECT u.total_log_size_in_bytes, u.used_log_space_in_bytes, u.used_log_space_in_percent, "
        "u.log_space_in_bytes_since_last_backup, d.log_reuse_wait_desc, d.is_cdc_enabled "
        "FROM sys.dm_db_log_space_usage u CROSS JOIN sys.databases d WHERE d.database_id = DB_ID()",
    "table_sizes":
        "SELECT OBJECT_SCHEMA_NAME(p.object_id) AS schema_name, OBJECT_NAME(p.object_id) AS table_name, "
        "SUM(p.reserved_page_count) * 8192 AS reserved_bytes, SUM(p.used_page_count) * 8192 AS used_bytes, "
        "SUM(CASE WHEN p.index_id IN (0,1) THEN p.row_count ELSE 0 END) AS row_count "
        "FROM sys.dm_db_partition_stats p WHERE OBJECT_NAME(p.object_id) IN ('Task','Outbox','Matter','dbo_Outbox_CT','lsn_time_mapping') "
        "GROUP BY p.object_id",
    "perf_counters":
        "SELECT RTRIM(c.object_name) AS object_name, RTRIM(c.counter_name) AS counter_name, c.cntr_value, c.cntr_type "
        "FROM sys.dm_os_performance_counters c "
        "WHERE (c.counter_name IN ('Log Bytes Flushed/sec','Log Flushes/sec','Log Flush Wait Time','Transactions/sec','Write Transactions/sec') "
        "  AND c.instance_name = (SELECT physical_database_name FROM sys.databases WHERE database_id = DB_ID())) "
        "OR c.counter_name IN ('Page life expectancy','Buffer cache hit ratio','Buffer cache hit ratio base')",
    "cdc_scan_sessions":
        "SELECT TOP (5) session_id, start_time, end_time, duration, scan_phase, error_count, start_lsn, current_lsn, end_lsn, "
        "tran_count, last_commit_lsn, last_commit_time, log_record_count, schema_change_count, command_count, "
        "first_begin_cdc_lsn, last_commit_cdc_lsn, last_commit_cdc_time, latency, empty_scan_count, failed_sessions_count "
        "FROM sys.dm_cdc_log_scan_sessions ORDER BY session_id DESC",
    "cdc_errors":
        "SELECT TOP (5) session_id, phase_number, entry_time, error_number, error_severity, error_state, error_message "
        "FROM sys.dm_cdc_errors ORDER BY entry_time DESC",
}

POOL_LEVEL: dict[str, str] = {
    "pool_resource_stats":
        "SELECT TOP (8) end_time, avg_cpu_percent, avg_data_io_percent, avg_log_write_percent, avg_storage_percent, "
        "max_worker_percent, max_session_percent, avg_instance_cpu_percent, avg_instance_memory_percent, instance_vcores "
        "FROM sys.dm_elastic_pool_resource_stats ORDER BY end_time DESC",
    "os_wait_stats":
        "SELECT wait_type, waiting_tasks_count, wait_time_ms, signal_wait_time_ms FROM sys.dm_os_wait_stats "
        f"WHERE wait_type IN ({GOVERNOR_WAITS}) OR wait_type LIKE 'HADR_THROTTLE_LOG_RATE%'",
}

# Applied limits, read once per run. AGENTS.md: recorded values win over documented ones.
GOVERNANCE = "SELECT * FROM sys.dm_user_db_resource_governance"


class Collector:
    def __init__(self, connector: db.Connector, sink: Sink, databases: list[str], context: Callable[[], Context], interval: float):
        self._connector = connector
        self._sink = sink
        self._databases = databases
        self._context = context
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> None:
        for i, database in enumerate(self._databases):
            queries = dict(PER_DATABASE)
            if i == 0:
                queries.update(POOL_LEVEL)
            try:
                conn = self._connector.connect(database)
            except Exception as e:  # noqa: BLE001 - record and keep polling
                self._sink.write("collector_errors", self._context(), {"database": database, "stream": "connect", "error": str(e)})
                continue
            try:
                for stream, sql in queries.items():
                    try:
                        self._sink.write(stream, self._context(), {"database": database, "rows": db.rows(conn, sql, timeout=20)})
                    except Exception as e:  # noqa: BLE001
                        self._sink.write("collector_errors", self._context(), {"database": database, "stream": stream, "error": str(e)})
            finally:
                conn.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=60)

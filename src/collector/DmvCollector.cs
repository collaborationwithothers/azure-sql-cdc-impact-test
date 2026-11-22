using CdcImpact.Common;
using Microsoft.Data.SqlClient;

namespace CdcImpact.Collector;

/// <summary>
/// Polls the DMVs listed in design section 6 every interval and writes each
/// row as a record. Cumulative counters (file stats, wait stats) are written
/// raw; the report computes deltas. Each query fails independently: an error
/// is recorded and the other sources keep running.
/// </summary>
public sealed class DmvCollector(SqlConnections connections, ResultSink sink, IReadOnlyList<string> databases, Func<RunContext> context, TimeSpan interval)
{
    /// <summary>Queries run in every database, keyed by stream name.</summary>
    public static readonly IReadOnlyDictionary<string, string> PerDatabase = new Dictionary<string, string>
    {
        ["db_resource_stats"] =
            "SELECT TOP (8) end_time, avg_cpu_percent, avg_data_io_percent, avg_log_write_percent, avg_memory_usage_percent, " +
            "max_worker_percent, max_session_percent, avg_instance_cpu_percent, avg_instance_memory_percent " +
            "FROM sys.dm_db_resource_stats ORDER BY end_time DESC",
        ["file_stats"] =
            "SELECT f.file_id, f.type_desc, s.num_of_reads, s.num_of_bytes_read, s.io_stall_read_ms, s.io_stall_queued_read_ms, " +
            "s.num_of_writes, s.num_of_bytes_written, s.io_stall_write_ms, s.io_stall_queued_write_ms, s.size_on_disk_bytes " +
            "FROM sys.dm_io_virtual_file_stats(DB_ID(), NULL) s JOIN sys.database_files f ON f.file_id = s.file_id",
        ["db_wait_stats"] =
            "SELECT wait_type, waiting_tasks_count, wait_time_ms, signal_wait_time_ms FROM sys.dm_db_wait_stats " +
            "WHERE wait_type IN ('LOG_RATE_GOVERNOR','POOL_LOG_RATE_GOVERNOR','INSTANCE_LOG_RATE_GOVERNOR','WRITELOG'," +
            "'PAGEIOLATCH_SH','PAGEIOLATCH_EX','PAGEIOLATCH_UP','SOS_SCHEDULER_YIELD','RESOURCE_SEMAPHORE','THREADPOOL') " +
            "OR wait_type LIKE 'HADR_THROTTLE_LOG_RATE%'",
        ["log_space"] =
            "SELECT u.total_log_size_in_bytes, u.used_log_space_in_bytes, u.used_log_space_in_percent, u.log_space_in_bytes_since_last_backup, " +
            "d.log_reuse_wait_desc, d.is_cdc_enabled FROM sys.dm_db_log_space_usage u CROSS JOIN sys.databases d WHERE d.database_id = DB_ID()",
        ["table_sizes"] =
            "SELECT OBJECT_SCHEMA_NAME(p.object_id) AS schema_name, OBJECT_NAME(p.object_id) AS table_name, " +
            "SUM(p.reserved_page_count) * 8192 AS reserved_bytes, SUM(p.used_page_count) * 8192 AS used_bytes, " +
            "SUM(CASE WHEN p.index_id IN (0,1) THEN p.row_count ELSE 0 END) AS row_count " +
            "FROM sys.dm_db_partition_stats p WHERE OBJECT_NAME(p.object_id) IN ('Task','Outbox','Matter','dbo_Outbox_CT','lsn_time_mapping') " +
            "GROUP BY p.object_id",
        ["perf_counters"] =
            "SELECT RTRIM(c.object_name) AS object_name, RTRIM(c.counter_name) AS counter_name, c.cntr_value, c.cntr_type " +
            "FROM sys.dm_os_performance_counters c " +
            "WHERE (c.counter_name IN ('Log Bytes Flushed/sec','Log Flushes/sec','Log Flush Wait Time','Transactions/sec','Write Transactions/sec') " +
            "  AND c.instance_name = (SELECT physical_database_name FROM sys.databases WHERE database_id = DB_ID())) " +
            "OR c.counter_name IN ('Page life expectancy','Buffer cache hit ratio','Buffer cache hit ratio base')",
        ["cdc_scan_sessions"] =
            "SELECT TOP (5) session_id, start_time, end_time, duration, scan_phase, error_count, start_lsn, current_lsn, end_lsn, " +
            "tran_count, last_commit_lsn, last_commit_time, log_record_count, schema_change_count, command_count, " +
            "first_begin_cdc_lsn, last_commit_cdc_lsn, last_commit_cdc_time, latency, empty_scan_count, failed_sessions_count " +
            "FROM sys.dm_cdc_log_scan_sessions ORDER BY session_id DESC",
        ["cdc_errors"] =
            "SELECT TOP (5) session_id, phase_number, entry_time, error_number, error_severity, error_state, error_message " +
            "FROM sys.dm_cdc_errors ORDER BY entry_time DESC",
    };

    /// <summary>Instance and pool level, read from the first database only.</summary>
    public static readonly IReadOnlyDictionary<string, string> PoolLevel = new Dictionary<string, string>
    {
        ["pool_resource_stats"] =
            "SELECT TOP (8) end_time, avg_cpu_percent, avg_data_io_percent, avg_log_write_percent, avg_storage_percent, " +
            "max_worker_percent, max_session_percent, avg_instance_cpu_percent, avg_instance_memory_percent, instance_vcores " +
            "FROM sys.dm_elastic_pool_resource_stats ORDER BY end_time DESC",
        ["os_wait_stats"] =
            "SELECT wait_type, waiting_tasks_count, wait_time_ms, signal_wait_time_ms FROM sys.dm_os_wait_stats " +
            "WHERE wait_type IN ('LOG_RATE_GOVERNOR','POOL_LOG_RATE_GOVERNOR','INSTANCE_LOG_RATE_GOVERNOR','WRITELOG'," +
            "'PAGEIOLATCH_SH','PAGEIOLATCH_EX','SOS_SCHEDULER_YIELD','THREADPOOL') OR wait_type LIKE 'HADR_THROTTLE_LOG_RATE%'",
    };

    /// <summary>Applied limits, read once per run (AGENTS.md truth rules: recorded values win).</summary>
    public const string Governance = "SELECT * FROM sys.dm_user_db_resource_governance";

    public async Task RunAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(interval);
        try
        {
            do
            {
                await PollOnceAsync(ct);
            }
            while (await timer.WaitForNextTickAsync(ct));
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
        }
    }

    public async Task PollOnceAsync(CancellationToken ct)
    {
        var tasks = databases.Select((db, index) => PollDatabaseAsync(db, index == 0, ct));
        await Task.WhenAll(tasks);
    }

    private async Task PollDatabaseAsync(string database, bool includePool, CancellationToken ct)
    {
        SqlConnection? conn = null;
        try
        {
            conn = await connections.OpenAsync(database, ct, maxPool: 4);
            foreach (var (stream, sql) in PerDatabase)
            {
                await QueryAsync(conn, database, stream, sql, ct);
            }
            if (includePool)
            {
                foreach (var (stream, sql) in PoolLevel)
                {
                    await QueryAsync(conn, database, stream, sql, ct);
                }
            }
        }
        catch (Exception e) when (e is not OperationCanceledException)
        {
            sink.Write("collector_errors", context(), new { database, stream = "connect", error = e.Message });
        }
        finally
        {
            if (conn is not null) await conn.DisposeAsync();
        }
    }

    private async Task QueryAsync(SqlConnection conn, string database, string stream, string sql, CancellationToken ct)
    {
        try
        {
            var rows = await conn.RowsAsync(sql, ct, timeoutSeconds: 20);
            sink.Write(stream, context(), new { database, rows });
        }
        catch (Exception e) when (e is not OperationCanceledException)
        {
            sink.Write("collector_errors", context(), new { database, stream, error = e.Message });
        }
    }

    public static async Task<List<Dictionary<string, object?>>> ReadGovernanceAsync(SqlConnection conn, CancellationToken ct) =>
        await conn.RowsAsync(Governance, ct);
}

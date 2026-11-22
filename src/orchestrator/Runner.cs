using System.Diagnostics;
using CdcImpact.Collector;
using CdcImpact.Common;
using CdcImpact.LoadGen;
using Microsoft.Data.SqlClient;

namespace CdcImpact.Orchestrator;

/// <summary>
/// Walks the test matrix: setup, seed, then for each repeat and configuration
/// the load ladder, followed by the purge and CDC cleanup phases. Database 2
/// carries a constant configuration B load throughout (design section 7).
/// </summary>
public sealed class Runner(RunSettings settings, ResultSink sink)
{
    private readonly SqlConnections _connections = new(settings);
    private RunContext _ctx = new(settings.RunId, "setup", null, null, 1);
    private readonly object _ctxLock = new();
    private readonly Dictionary<string, object?> _checks = [];
    private long _maxTaskId1;
    private long _maxTaskId2;

    public IReadOnlyDictionary<string, object?> Checks => _checks;

    private RunContext Ctx
    {
        get { lock (_ctxLock) return _ctx; }
        set { lock (_ctxLock) _ctx = value; }
    }

    private void Check(string name, bool ok, object? detail = null)
    {
        _checks[name] = new { ok, detail };
        sink.Write("checks", Ctx, new { name, ok, detail });
        Console.WriteLine($"check {name}: {(ok ? "ok" : "FAILED")} {detail}");
    }

    private void Log(string message) => Console.WriteLine($"{DateTime.UtcNow:O} [{Ctx.Phase} {Ctx.Configuration} {Ctx.StepTps}] {message}");

    public async Task RunAsync(CancellationToken ct)
    {
        Check("identity_endpoint_present", !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("IDENTITY_ENDPOINT")) || !string.IsNullOrEmpty(settings.ConnectionStringTemplate));
        sink.Write("settings", Ctx, settings with { ClientId = "", SqlServer = "", ResultsAccount = "", EventHubsNamespace = "", ConnectionStringTemplate = null });

        await RecordEnvironmentAsync(ct);
        await SetupSchemaAsync(ct);
        await SeedAsync(ct);
        await sink.UploadAsync(ct);

        if (settings.InjectFailure == "fail")
        {
            throw new InvalidOperationException("Injected failure (inject_failure=fail) to prove the destroy path");
        }

        using var background = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var collector = new DmvCollector(_connections, sink, [settings.Database1, settings.Database2], () => Ctx, TimeSpan.FromSeconds(settings.CollectorIntervalSeconds));
        var collectorTask = Task.Run(() => collector.RunAsync(background.Token), CancellationToken.None);
        var db2Task = Task.Run(() => Database2LoadAsync(background.Token), CancellationToken.None);

        try
        {
            for (var repeat = 1; repeat <= settings.Repeats; repeat++)
            {
                foreach (var configuration in settings.Configurations)
                {
                    await RunConfigurationAsync(configuration, repeat, ct);
                    await sink.UploadAsync(ct);
                }
            }
        }
        finally
        {
            Ctx = Ctx.With("teardown");
            await background.CancelAsync();
            await Task.WhenAll(collectorTask, db2Task).ContinueWith(_ => { }, TaskScheduler.Default);
        }
    }

    private async Task RecordEnvironmentAsync(CancellationToken ct)
    {
        foreach (var db in new[] { settings.Database1, settings.Database2 })
        {
            await using var conn = await _connections.OpenAsync(db, ct);
            Check($"connect_{db}", true);
            var env = await conn.RowsAsync(
                "SELECT DB_NAME() AS database_name, CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition, " +
                "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version, " +
                "CAST(DATABASEPROPERTYEX(DB_NAME(), 'Edition') AS nvarchar(128)) AS edition, " +
                "CAST(DATABASEPROPERTYEX(DB_NAME(), 'ServiceObjective') AS nvarchar(128)) AS service_objective, " +
                "IS_ROLEMEMBER('db_owner') AS is_db_owner", ct);
            sink.Write("environment", Ctx, new { database = db, rows = env });
            try
            {
                var gov = await DmvCollector.ReadGovernanceAsync(conn, ct);
                sink.Write("governance", Ctx, new { database = db, rows = gov });
                Check($"governance_{db}", gov.Count > 0, $"{gov.Count} rows");
            }
            catch (SqlException e)
            {
                Check($"governance_{db}", false, e.Message);
            }
        }
        // Spike item 6: every DMV the collector needs is readable.
        var probe = new DmvCollector(_connections, sink, [settings.Database1], () => Ctx.With("dmv_probe"), TimeSpan.FromSeconds(10));
        await using (var conn = await _connections.OpenAsync(settings.Database1, ct))
        {
            foreach (var (stream, sql) in DmvCollector.PerDatabase.Concat(DmvCollector.PoolLevel))
            {
                try
                {
                    var rows = await conn.RowsAsync(sql, ct);
                    Check($"dmv_{stream}", true, $"{rows.Count} rows");
                }
                catch (SqlException e)
                {
                    Check($"dmv_{stream}", false, e.Message);
                }
            }
        }
        await probe.PollOnceAsync(ct);
    }

    private async Task SetupSchemaAsync(CancellationToken ct)
    {
        Ctx = Ctx.With("schema");
        var schema = SqlScripts.Load("schema.sql").Replace("{{PAYLOAD_TYPE}}", settings.PayloadType);
        foreach (var db in new[] { settings.Database1, settings.Database2 })
        {
            await using var conn = await _connections.OpenAsync(db, ct);
            await conn.ExecAsync(schema, ct);
            // A previous configuration C may have left CDC on. Start clean.
            await conn.ExecAsync(SqlScripts.Block("cdc.sql", "disable"), ct);
        }
        Log("schema ready");
    }

    private async Task SeedAsync(CancellationToken ct)
    {
        Ctx = Ctx.With("seed");
        var results = await Task.WhenAll(
            SeedDatabaseAsync(settings.Database1, ct),
            SeedDatabaseAsync(settings.Database2, ct));
        _maxTaskId1 = results[0];
        _maxTaskId2 = results[1];
    }

    /// <summary>Seeds dbo.Task until its reserved size reaches SeedGb GiB. Returns the highest TaskId.</summary>
    private async Task<long> SeedDatabaseAsync(string db, CancellationToken ct)
    {
        await using var conn = await _connections.OpenAsync(db, ct);
        await conn.ExecAsync(
            "IF NOT EXISTS (SELECT 1 FROM dbo.Matter) " +
            "INSERT dbo.Matter (MatterId, Name, CreatedAt) SELECT TOP (@n) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)), " +
            "CONCAT(N'Matter ', ROW_NUMBER() OVER (ORDER BY (SELECT NULL))), '2026-01-01' FROM sys.all_columns a CROSS JOIN sys.all_columns b",
            ct, 600, ("@n", settings.Matters));

        var targetBytes = (long)(settings.SeedGb * 1024 * 1024 * 1024);
        var batch = SqlScripts.Load("seed-batch.sql");
        var sw = Stopwatch.StartNew();
        var maxId = await conn.ScalarAsync<long?>("SELECT MAX(TaskId) FROM dbo.Task", ct) ?? 0;
        var batches = 0;
        while (true)
        {
            var size = await conn.ScalarAsync<long?>("SELECT SUM(reserved_page_count) * 8192 FROM sys.dm_db_partition_stats WHERE object_id = OBJECT_ID('dbo.Task')", ct) ?? 0;
            if (size >= targetBytes) break;
            var to = maxId + settings.SeedBatchRows;
            await conn.ExecAsync(batch, ct, 1800, ("@from", maxId), ("@to", to), ("@matters", settings.Matters));
            maxId = to;
            batches++;
            if (batches % 20 == 0) Log($"seed {db}: {size / 1048576} MiB after {sw.Elapsed:hh\\:mm\\:ss}");
        }
        var rows = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM dbo.Task", ct, 1800);
        var reserved = await conn.ScalarAsync<long>("SELECT SUM(reserved_page_count) * 8192 FROM sys.dm_db_partition_stats WHERE object_id = OBJECT_ID('dbo.Task')", ct);
        sink.Write("seed", Ctx, new { database = db, rows, reserved_bytes = reserved, max_task_id = maxId, batches, seconds = sw.Elapsed.TotalSeconds });
        Log($"seed {db} done: {rows} rows, {reserved / 1048576} MiB, {sw.Elapsed}");
        return maxId;
    }

    private async Task Database2LoadAsync(CancellationToken ct)
    {
        var workload = new TaskWorkload(_connections.ConnectionString(settings.Database2, 50), Configuration.B, _maxTaskId2, settings.PayloadChars, settings.PayloadType, settings.CommandTimeoutSeconds);
        var runner = new OpenLoopRunner(50, Ladder.MaxPending(settings.Database2Rate));
        while (!ct.IsCancellationRequested)
        {
            var ctx = Ctx;
            try
            {
                var w = await runner.RunAsync(settings.Database2Rate, TimeSpan.FromSeconds(10), workload.ExecuteAsync, ct);
                sink.Write("db2_windows", ctx, w);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private async Task RunConfigurationAsync(Configuration configuration, int repeat, CancellationToken ct)
    {
        Ctx = Ctx.With("reset", configuration, null, repeat);
        await ResetDatabase1Async(ct);

        DebeziumProcess? debezium = null;
        DebeziumMetrics? metrics = null;
        Task? metricsTask = null;
        using var metricsCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        try
        {
            if (configuration == Configuration.C)
            {
                Ctx = Ctx.With("cdc_enable", configuration, null, repeat);
                await using (var conn = await _connections.OpenAsync(settings.Database1, ct))
                {
                    try
                    {
                        await conn.ExecAsync(SqlScripts.Block("cdc.sql", "enable"), ct);
                        var enabled = await conn.ScalarAsync<bool>("SELECT is_tracked_by_cdc FROM sys.tables WHERE object_id = OBJECT_ID('dbo.Outbox')", ct);
                        Check("cdc_enable", enabled);
                        if (!enabled) throw new InvalidOperationException("CDC was not enabled on dbo.Outbox");
                    }
                    catch (SqlException e)
                    {
                        Check("cdc_enable", false, e.Message);
                        throw;
                    }
                }
                debezium = new DebeziumProcess(settings, sink);
                debezium.Start();
                metrics = new DebeziumMetrics(new HttpClient { Timeout = TimeSpan.FromSeconds(5) }, new Uri($"http://localhost:{DebeziumProcess.MetricsPort}/metrics"));
                await WaitForDebeziumAsync(debezium, metrics, ct);
                metricsTask = Task.Run(() => metrics.RunAsync(sink, () => Ctx, TimeSpan.FromSeconds(settings.CollectorIntervalSeconds), metricsCts.Token), CancellationToken.None);
            }

            await RunLadderAsync(configuration, repeat, ct);

            if (configuration == Configuration.C && metrics is not null)
            {
                Ctx = Ctx.With("ladder_catchup", configuration, null, repeat);
                await WaitForCatchUpAsync(metrics, "ladder", ct);
            }

            if (settings.RunPurge && configuration != Configuration.A)
            {
                await PurgeAsync(configuration, repeat, metrics, ct);
            }

            if (settings.RunCleanup && configuration == Configuration.C)
            {
                await CdcCleanupAsync(repeat, ct);
            }
        }
        finally
        {
            await metricsCts.CancelAsync();
            if (metricsTask is not null) await metricsTask.ContinueWith(_ => { }, TaskScheduler.Default);
            if (debezium is not null)
            {
                Check("debezium_alive_at_end", !debezium.HasExited);
                await debezium.StopAsync();
            }
        }
    }

    private async Task ResetDatabase1Async(CancellationToken ct)
    {
        await using var conn = await _connections.OpenAsync(settings.Database1, ct);
        await conn.ExecAsync(SqlScripts.Block("cdc.sql", "disable"), ct);
        await conn.ExecAsync("TRUNCATE TABLE dbo.Outbox; CHECKPOINT;", ct);
        var sw = Stopwatch.StartNew();
        // Wait for CDC and open transactions to release the log, then let it settle.
        while (sw.Elapsed.TotalSeconds < settings.LogDrainTimeoutSeconds)
        {
            var wait = await conn.ScalarAsync<string>("SELECT log_reuse_wait_desc FROM sys.databases WHERE database_id = DB_ID()", ct);
            if (wait is not ("REPLICATION" or "ACTIVE_TRANSACTION")) break;
            await Task.Delay(TimeSpan.FromSeconds(10), ct);
        }
        await Task.Delay(TimeSpan.FromSeconds(Math.Min(60, settings.WarmupSeconds)), ct);
        var log = await conn.RowsAsync("SELECT u.used_log_space_in_percent, d.log_reuse_wait_desc FROM sys.dm_db_log_space_usage u CROSS JOIN sys.databases d WHERE d.database_id = DB_ID()", ct);
        sink.Write("reset", Ctx, new { seconds = sw.Elapsed.TotalSeconds, log });
    }

    private async Task WaitForDebeziumAsync(DebeziumProcess debezium, DebeziumMetrics metrics, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        while (sw.Elapsed < TimeSpan.FromMinutes(5))
        {
            if (debezium.HasExited)
            {
                Check("debezium_started", false, "process exited; see debezium.log");
                throw new InvalidOperationException("Debezium Server exited during start-up");
            }
            try
            {
                var m = await metrics.ScrapeAsync(ct);
                var connected = DebeziumMetrics.Find(m, "streaming", "Connected");
                if (connected == 1)
                {
                    Check("debezium_started", true, $"{sw.Elapsed.TotalSeconds:F0} s");
                    Check("debezium_metrics_readable", m.Count > 0, $"{m.Count} series");
                    return;
                }
            }
            catch (HttpRequestException)
            {
            }
            await Task.Delay(TimeSpan.FromSeconds(5), ct);
        }
        Check("debezium_started", false, "not connected after 5 minutes");
        throw new TimeoutException("Debezium did not connect");
    }

    private async Task RunLadderAsync(Configuration configuration, int repeat, CancellationToken ct)
    {
        foreach (var step in settings.Steps)
        {
            var maxConcurrency = Math.Max(4, settings.MaxPoolSize);
            var workload = new TaskWorkload(_connections.ConnectionString(settings.Database1), configuration, _maxTaskId1, settings.PayloadChars, settings.PayloadType, settings.CommandTimeoutSeconds);
            var runner = new OpenLoopRunner(maxConcurrency, Ladder.MaxPending(step));

            Ctx = Ctx.With("warmup", configuration, step, repeat);
            var warmStart = DateTime.UtcNow;
            var warm = await runner.RunAsync(step, TimeSpan.FromSeconds(settings.WarmupSeconds), workload.ExecuteAsync, ct);
            sink.Write("loadgen_windows", Ctx, warm);

            Ctx = Ctx.With("measure", configuration, step, repeat);
            var start = DateTime.UtcNow;
            var measured = await runner.RunAsync(step, TimeSpan.FromSeconds(settings.MeasureSeconds), workload.ExecuteAsync, ct);
            var end = DateTime.UtcNow;
            sink.Write("loadgen_windows", Ctx, measured);
            var stop = Ladder.ShouldStop(measured, settings.StopRatio);
            sink.Write("steps", Ctx, new { warmup_start = warmStart, measure_start = start, measure_end = end, result = measured, stopped = stop });
            Log($"achieved {measured.AchievedRate:F1}/{step} tps, p95 {measured.Latency.P95Ms:F1} ms, errors {measured.Errors}, shed {measured.Shed}, client cpu {measured.ClientCpuPercent:F1}%");
            if (stop)
            {
                Log("stop rule: achieved rate below stop ratio; not climbing further");
                break;
            }
        }
    }

    /// <summary>Waits until Debezium has seen every change row in the change table.</summary>
    private async Task WaitForCatchUpAsync(DebeziumMetrics metrics, string label, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        long ctRows = 0;
        double? seen = null;
        await using var conn = await _connections.OpenAsync(settings.Database1, ct);
        while (sw.Elapsed.TotalSeconds < settings.DebeziumCatchUpTimeoutSeconds)
        {
            ctRows = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT", ct, 600);
            try
            {
                var m = await metrics.ScrapeAsync(ct);
                seen = DebeziumMetrics.Find(m, "streaming", "TotalNumberOfEventsSeen");
            }
            catch (HttpRequestException)
            {
                seen = null;
            }
            if (seen is not null && seen >= ctRows && ctRows > 0) break;
            await Task.Delay(TimeSpan.FromSeconds(5), ct);
        }
        var caught = seen is not null && seen >= ctRows;
        sink.Write("catchup", Ctx, new { label, seconds = sw.Elapsed.TotalSeconds, change_table_rows = ctRows, debezium_events_seen = seen, caught_up = caught });
        Check($"catchup_{label}_{Ctx.Configuration}", caught, $"{sw.Elapsed.TotalSeconds:F0} s, events {seen}, change rows {ctRows}");
    }

    private static async Task<long> LogBytesWrittenAsync(SqlConnection conn, CancellationToken ct) =>
        await conn.ScalarAsync<long>("SELECT SUM(s.num_of_bytes_written) FROM sys.dm_io_virtual_file_stats(DB_ID(), NULL) s JOIN sys.database_files f ON f.file_id = s.file_id WHERE f.type_desc = 'LOG'", ct);

    private static async Task<long> DataBytesWrittenAsync(SqlConnection conn, CancellationToken ct) =>
        await conn.ScalarAsync<long>("SELECT SUM(s.num_of_bytes_written) FROM sys.dm_io_virtual_file_stats(DB_ID(), NULL) s JOIN sys.database_files f ON f.file_id = s.file_id WHERE f.type_desc = 'ROWS'", ct);

    private async Task PurgeAsync(Configuration configuration, int repeat, DebeziumMetrics? metrics, CancellationToken ct)
    {
        Ctx = Ctx.With("purge_topup", configuration, null, repeat);
        await using var conn = await _connections.OpenAsync(settings.Database1, ct);
        var rows = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM dbo.Outbox", ct, 600);
        if (rows < settings.PurgeMinRows)
        {
            var need = settings.PurgeMinRows - rows;
            var payload = TaskWorkload.Payload(TaskWorkload.BuildPayloadTemplate(settings.PayloadChars), 0);
            var sw0 = Stopwatch.StartNew();
            while (need > 0)
            {
                var n = Math.Min(need, 10_000);
                await conn.ExecAsync(
                    "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) SELECT TOP (@n) TaskId, 'TopUp', SYSUTCDATETIME(), @p FROM dbo.Task",
                    ct, 1800, ("@n", n), ("@p", payload));
                need -= n;
            }
            sink.Write("purge_topup", Ctx, new { added = settings.PurgeMinRows - rows, seconds = sw0.Elapsed.TotalSeconds });
            if (metrics is not null) await WaitForCatchUpAsync(metrics, "topup", ct);
        }

        Ctx = Ctx.With("purge", configuration, null, repeat);
        var before = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM dbo.Outbox", ct, 600);
        var logBefore = await LogBytesWrittenAsync(conn, ct);
        var dataBefore = await DataBytesWrittenAsync(conn, ct);
        var sw = Stopwatch.StartNew();
        var batches = 0;
        while (true)
        {
            var deleted = await conn.ExecAsync("DELETE TOP (@b) FROM dbo.Outbox", ct, 1800, ("@b", settings.PurgeBatch));
            batches++;
            if (deleted < settings.PurgeBatch) break;
        }
        var seconds = sw.Elapsed.TotalSeconds;
        var logAfter = await LogBytesWrittenAsync(conn, ct);
        var dataAfter = await DataBytesWrittenAsync(conn, ct);
        sink.Write("purge", Ctx, new
        {
            rows_deleted = before,
            batches,
            batch_size = settings.PurgeBatch,
            seconds,
            log_bytes_written = logAfter - logBefore,
            data_bytes_written = dataAfter - dataBefore,
            started = DateTime.UtcNow.AddSeconds(-seconds),
        });
        Log($"purge: {before} rows in {seconds:F0} s, log {(logAfter - logBefore) / 1048576} MiB");
        if (metrics is not null)
        {
            Ctx = Ctx.With("purge_catchup", configuration, null, repeat);
            await WaitForCatchUpAsync(metrics, "purge", ct);
        }
    }

    private async Task CdcCleanupAsync(int repeat, CancellationToken ct)
    {
        Ctx = Ctx.With("cdc_cleanup", Configuration.C, null, repeat);
        await using var conn = await _connections.OpenAsync(settings.Database1, ct);
        try
        {
            await conn.ExecAsync(SqlScripts.Block("cdc.sql", "lower-retention"), ct);
            Check("cdc_change_job_retention", true);
        }
        catch (SqlException e)
        {
            // Not documented for Azure SQL Database. The on-demand cleanup below does not depend on it.
            Check("cdc_change_job_retention", false, e.Message);
        }
        var rowsBefore = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT", ct, 600);
        var logBefore = await LogBytesWrittenAsync(conn, ct);
        var dataBefore = await DataBytesWrittenAsync(conn, ct);
        var sw = Stopwatch.StartNew();
        try
        {
            await conn.ExecAsync(SqlScripts.Block("cdc.sql", "cleanup"), ct, 3600);
            Check("cdc_cleanup_on_demand", true);
        }
        catch (SqlException e)
        {
            Check("cdc_cleanup_on_demand", false, e.Message);
        }
        var seconds = sw.Elapsed.TotalSeconds;
        var rowsAfter = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT", ct, 600);
        var logAfter = await LogBytesWrittenAsync(conn, ct);
        var dataAfter = await DataBytesWrittenAsync(conn, ct);
        sink.Write("cdc_cleanup", Ctx, new
        {
            change_rows_before = rowsBefore,
            change_rows_after = rowsAfter,
            seconds,
            log_bytes_written = logAfter - logBefore,
            data_bytes_written = dataAfter - dataBefore,
        });
        Log($"cdc cleanup: {rowsBefore} -> {rowsAfter} change rows in {seconds:F0} s");
    }
}

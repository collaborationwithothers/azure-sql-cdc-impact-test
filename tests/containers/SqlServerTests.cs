using CdcImpact.Collector;
using CdcImpact.Common;
using CdcImpact.LoadGen;
using Microsoft.Data.SqlClient;
using Testcontainers.MsSql;
using Xunit;

namespace CdcImpact.Tests.Containers;

/// <summary>
/// Starts one SQL Server container with SQL Server Agent on (CDC capture on
/// SQL Server needs it; Azure SQL Database uses its own scheduler).
/// A container has no elastic pool and no resource governance: these tests
/// prove mechanics only, never performance.
/// </summary>
public sealed class SqlServerFixture : IAsyncLifetime
{
    public MsSqlContainer Container { get; } = new MsSqlBuilder("mcr.microsoft.com/mssql/server:2022-latest")
        .WithEnvironment("MSSQL_AGENT_ENABLED", "true")
        .Build();

    public RunSettings Settings { get; private set; } = new();

    public async ValueTask InitializeAsync()
    {
        await Container.StartAsync();
        var master = new SqlConnectionStringBuilder(Container.GetConnectionString()) { InitialCatalog = "master" };
        await using (var conn = new SqlConnection(master.ConnectionString))
        {
            await conn.OpenAsync();
            await conn.ExecAsync("IF DB_ID('cdcdb1') IS NULL CREATE DATABASE cdcdb1; IF DB_ID('cdcdb2') IS NULL CREATE DATABASE cdcdb2;", CancellationToken.None);
        }
        var template = new SqlConnectionStringBuilder(Container.GetConnectionString()) { InitialCatalog = "{db}", TrustServerCertificate = true };
        Settings = new RunSettings
        {
            RunId = "container-test",
            ConnectionStringTemplate = template.ConnectionString,
            ResultsDirectory = Path.Combine(Path.GetTempPath(), "cdc-impact-tests"),
        };
        var schema = SqlScripts.Load("schema.sql").Replace("{{PAYLOAD_TYPE}}", "nvarchar(max)");
        foreach (var db in new[] { "cdcdb1", "cdcdb2" })
        {
            await using var c = await new SqlConnections(Settings).OpenAsync(db, CancellationToken.None);
            await c.ExecAsync(schema, CancellationToken.None);
            await c.ExecAsync(schema, CancellationToken.None); // idempotent
            await c.ExecAsync(SqlScripts.Load("seed-batch.sql"), CancellationToken.None, 600, ("@from", 0L), ("@to", 20_000L), ("@matters", 100));
        }
    }

    public async ValueTask DisposeAsync() => await Container.DisposeAsync();
}

[CollectionDefinition("sql")]
public sealed class SqlCollection : ICollectionFixture<SqlServerFixture>;

[Collection("sql")]
public sealed class SqlServerTests(SqlServerFixture fx)
{
    private SqlConnections Connections => new(fx.Settings);
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task Seed_batch_inserts_contiguous_ids()
    {
        await using var conn = await Connections.OpenAsync("cdcdb2", Ct);
        Assert.Equal(20_000L, await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM dbo.Task", Ct));
        Assert.Equal(20_000L, await conn.ScalarAsync<long>("SELECT MAX(TaskId) FROM dbo.Task", Ct));
    }

    [Theory]
    [InlineData(Configuration.A, 0)]
    [InlineData(Configuration.B, 1)]
    public async Task Transaction_updates_task_and_writes_outbox_only_for_B(Configuration configuration, int outboxRowsPerTx)
    {
        await using var conn = await Connections.OpenAsync("cdcdb2", Ct);
        var before = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM dbo.Outbox", Ct);
        var w = new TaskWorkload(Connections.ConnectionString("cdcdb2"), configuration, 20_000, 4096, "nvarchar(max)", 30);
        for (var i = 0; i < 20; i++) await w.ExecuteAsync(Ct);
        var after = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM dbo.Outbox", Ct);
        Assert.Equal(20L * outboxRowsPerTx, after - before);
        if (outboxRowsPerTx > 0)
        {
            Assert.Equal(4096L, await conn.ScalarAsync<long>("SELECT TOP 1 LEN(Payload) FROM dbo.Outbox ORDER BY OutboxId DESC", Ct));
            // nvarchar: 2 bytes per character (decision D1).
            Assert.Equal(8192L, await conn.ScalarAsync<long>("SELECT TOP 1 DATALENGTH(Payload) FROM dbo.Outbox ORDER BY OutboxId DESC", Ct));
        }
        Assert.True(await conn.ScalarAsync<int>("SELECT COUNT(*) FROM dbo.Task WHERE UpdatedAt > '2026-01-02'", Ct) > 0);
    }

    [Fact]
    public async Task Open_loop_runner_drives_real_transactions()
    {
        var w = new TaskWorkload(Connections.ConnectionString("cdcdb2", 20), Configuration.B, 20_000, 4096, "nvarchar(max)", 30);
        var r = await new OpenLoopRunner(20, 1000).RunAsync(50, TimeSpan.FromSeconds(3), w.ExecuteAsync, Ct);
        Assert.Equal(0, r.Errors);
        Assert.Equal(150, r.Scheduled);
        Assert.True(r.Completed >= 140, $"completed {r.Completed}");
    }

    [Fact]
    public async Task Cdc_enable_capture_cleanup_disable()
    {
        await using var conn = await Connections.OpenAsync("cdcdb1", Ct);
        await conn.ExecAsync(SqlScripts.Block("cdc.sql", "enable"), Ct);
        await conn.ExecAsync(SqlScripts.Block("cdc.sql", "enable"), Ct); // idempotent
        Assert.True(await conn.ScalarAsync<bool>("SELECT is_tracked_by_cdc FROM sys.tables WHERE object_id = OBJECT_ID('dbo.Outbox')", Ct));

        var w = new TaskWorkload(Connections.ConnectionString("cdcdb1"), Configuration.C, 20_000, 4096, "nvarchar(max)", 30);
        for (var i = 0; i < 50; i++) await w.ExecuteAsync(Ct);

        // Capture runs asynchronously from the log. Force a scan rather than wait for the job.
        long changeRows = 0;
        for (var attempt = 0; attempt < 30 && changeRows < 50; attempt++)
        {
            try
            {
                await conn.ExecAsync("EXEC sys.sp_cdc_scan @maxtrans = 500, @maxscans = 10, @continuous = 0", Ct);
            }
            catch (SqlException)
            {
                // The capture job may already be running a scan.
            }
            changeRows = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT", Ct);
            if (changeRows < 50) await Task.Delay(1000, Ct);
        }
        Assert.True(changeRows >= 50, $"change rows {changeRows}");

        await conn.ExecAsync(SqlScripts.Block("cdc.sql", "cleanup"), Ct, 600);
        var after = await conn.ScalarAsync<long>("SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT", Ct);
        Assert.True(after < changeRows, $"cleanup left {after} of {changeRows}");

        await conn.ExecAsync(SqlScripts.Block("cdc.sql", "disable"), Ct);
        Assert.False(await conn.ScalarAsync<bool>("SELECT is_cdc_enabled FROM sys.databases WHERE database_id = DB_ID()", Ct));
        await conn.ExecAsync("TRUNCATE TABLE dbo.Outbox", Ct);
    }

    /// <summary>
    /// DMVs that exist on SQL Server as well as Azure SQL Database. The
    /// Azure-only ones (resource stats, pool stats, governance, db wait stats)
    /// can only be proven live.
    /// </summary>
    [Theory]
    [InlineData("file_stats")]
    [InlineData("log_space")]
    [InlineData("table_sizes")]
    [InlineData("perf_counters")]
    [InlineData("cdc_scan_sessions")]
    [InlineData("cdc_errors")]
    public async Task Portable_collector_queries_run(string stream)
    {
        await using var conn = await Connections.OpenAsync("cdcdb1", Ct);
        var rows = await conn.RowsAsync(DmvCollector.PerDatabase[stream], Ct);
        if (stream is "file_stats" or "log_space" or "table_sizes") Assert.NotEmpty(rows);
    }

    [Fact]
    public async Task Os_wait_stats_query_runs()
    {
        await using var conn = await Connections.OpenAsync("cdcdb1", Ct);
        await conn.RowsAsync(DmvCollector.PoolLevel["os_wait_stats"], Ct);
    }

    [Fact]
    public async Task Collector_writes_records_and_errors_for_azure_only_views()
    {
        var settings = fx.Settings with { RunId = "collector-test-" + Guid.NewGuid().ToString("N") };
        await using var sink = new ResultSink(settings);
        var ctx = new RunContext(settings.RunId, "test", "B", 100, 1);
        await new DmvCollector(new SqlConnections(settings), sink, ["cdcdb1"], () => ctx, TimeSpan.FromSeconds(1)).PollOnceAsync(Ct);
        await sink.DisposeAsync();
        var dir = Path.Combine(settings.ResultsDirectory, settings.RunId);
        Assert.True(File.Exists(Path.Combine(dir, "file_stats.jsonl")));
        var line = (await File.ReadAllLinesAsync(Path.Combine(dir, "file_stats.jsonl"), Ct))[0];
        Assert.Contains("\"run_id\":\"" + settings.RunId + "\"", line);
        Assert.Contains("\"step_tps\":100", line);
        // dm_db_resource_stats does not exist on SQL Server: recorded as an error, not a crash.
        Assert.Contains("db_resource_stats", await File.ReadAllTextAsync(Path.Combine(dir, "collector_errors.jsonl"), Ct));
    }
}

using System.Diagnostics;
using System.Text.Json;
using CdcImpact.LoadGen;
using Microsoft.Data.SqlClient;
using OpenTelemetry;
using OpenTelemetry.Trace;
using Testcontainers.MsSql;
using Xunit;

namespace CdcImpact.Tests.Containers;

/// <summary>
/// SQL Server in a container. It has no elastic pool and no resource
/// governance: these tests prove the load generator's mechanics, never its
/// performance. CDC, seeding and the collector are tested in tests/rig.
/// </summary>
public sealed class SqlServerFixture : IAsyncLifetime
{
    public MsSqlContainer Container { get; } = new MsSqlBuilder("mcr.microsoft.com/mssql/server:2022-latest").Build();

    public static string RepoRoot
    {
        get
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "CdcImpact.slnx"))) dir = dir.Parent;
            return dir?.FullName ?? throw new InvalidOperationException("repo root not found");
        }
    }

    public string ConnectionString { get; private set; } = "";

    public async ValueTask InitializeAsync()
    {
        await Container.StartAsync();
        var master = new SqlConnectionStringBuilder(Container.GetConnectionString()) { InitialCatalog = "master", TrustServerCertificate = true };
        await using (var conn = new SqlConnection(master.ConnectionString))
        {
            await conn.OpenAsync();
            await new SqlCommand("IF DB_ID('loadgendb') IS NULL CREATE DATABASE loadgendb;", conn).ExecuteNonQueryAsync();
        }
        ConnectionString = new SqlConnectionStringBuilder(master.ConnectionString) { InitialCatalog = "loadgendb" }.ConnectionString;
        await using var db = new SqlConnection(ConnectionString);
        await db.OpenAsync();
        var schema = (await File.ReadAllTextAsync(Path.Combine(RepoRoot, "sql", "schema.sql"))).Replace("{{PAYLOAD_TYPE}}", "nvarchar(max)");
        await new SqlCommand(schema, db).ExecuteNonQueryAsync();
        await new SqlCommand(await File.ReadAllTextAsync(Path.Combine(RepoRoot, "sql", "seed-proc.sql")), db).ExecuteNonQueryAsync();
        var seed = new SqlCommand("dbo.SeedTaskBatch", db) { CommandType = System.Data.CommandType.StoredProcedure };
        seed.Parameters.AddWithValue("@from", 0L);
        seed.Parameters.AddWithValue("@to", 20_000L);
        seed.Parameters.AddWithValue("@matters", 100);
        await seed.ExecuteNonQueryAsync();
    }

    public async ValueTask DisposeAsync() => await Container.DisposeAsync();
}

[CollectionDefinition("sql")]
public sealed class SqlCollection : ICollectionFixture<SqlServerFixture>;

[Collection("sql")]
public sealed class LoadGenSqlTests(SqlServerFixture fx)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private async Task<long> ScalarAsync(string sql)
    {
        await using var conn = new SqlConnection(fx.ConnectionString);
        await conn.OpenAsync(Ct);
        return Convert.ToInt64(await new SqlCommand(sql, conn).ExecuteScalarAsync(Ct));
    }

    [Theory]
    [InlineData(Transaction.UpdateOnly, 0)]
    [InlineData(Transaction.UpdateAndOutbox, 1)]
    public async Task Transaction_writes_outbox_only_when_asked(Transaction transaction, int outboxRowsPerTx)
    {
        var before = await ScalarAsync("SELECT COUNT_BIG(*) FROM dbo.Outbox");
        var w = new TaskWorkload(fx.ConnectionString, transaction, 20_000, 4096, "nvarchar(max)", 30);
        for (var i = 0; i < 20; i++) await w.ExecuteAsync(Ct);
        Assert.Equal(20L * outboxRowsPerTx, await ScalarAsync("SELECT COUNT_BIG(*) FROM dbo.Outbox") - before);
        if (outboxRowsPerTx > 0)
        {
            Assert.Equal(4096L, await ScalarAsync("SELECT TOP 1 LEN(Payload) FROM dbo.Outbox ORDER BY OutboxId DESC"));
            // nvarchar stores 2 bytes per character (decision D1).
            Assert.Equal(8192L, await ScalarAsync("SELECT TOP 1 DATALENGTH(Payload) FROM dbo.Outbox ORDER BY OutboxId DESC"));
        }
        Assert.True(await ScalarAsync("SELECT COUNT_BIG(*) FROM dbo.Task WHERE UpdatedAt > '2026-01-02'") > 0);
    }

    [Fact]
    public async Task Open_loop_runner_drives_real_transactions()
    {
        var w = new TaskWorkload(fx.ConnectionString, Transaction.UpdateAndOutbox, 20_000, 4096, "nvarchar(max)", 30);
        var r = await new OpenLoopRunner(20, 1000).RunAsync(50, TimeSpan.FromSeconds(3), w.ExecuteAsync, Ct);
        Assert.Equal(0, r.Errors);
        Assert.Equal(150, r.Scheduled);
        Assert.True(r.Completed >= 140, $"completed {r.Completed}");
    }

    /// <summary>OpenTelemetry.Instrumentation.SqlClient is not documented for SqlClient 7.x. This proves it emits spans.</summary>
    [Fact]
    public async Task Sql_client_instrumentation_emits_spans_with_this_sqlclient_version()
    {
        var spans = new List<Activity>();
        using (var tracer = Sdk.CreateTracerProviderBuilder().AddSqlClientInstrumentation().AddInMemoryExporter(spans).Build())
        {
            var w = new TaskWorkload(fx.ConnectionString, Transaction.UpdateOnly, 20_000, 4096, "nvarchar(max)", 30);
            await w.ExecuteAsync(Ct);
            tracer.ForceFlush();
        }
        Assert.Contains(spans, s => s.Kind == ActivityKind.Client);
    }

    [Fact]
    public async Task Cli_window_prints_warmup_and_measured_results()
    {
        var dll = Path.Combine(AppContext.BaseDirectory, "loadgen.dll");
        var psi = new ProcessStartInfo("dotnet")
        {
            ArgumentList = { dll, "window", "--connection-string", fx.ConnectionString, "--database", "loadgendb", "--transaction", "update-outbox",
                "--rate", "40", "--warmup", "1", "--measure", "2", "--max-task-id", "20000", "--telemetry", "off", "--label", "run_id=test" },
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        using var p = Process.Start(psi)!;
        var stdout = await p.StandardOutput.ReadToEndAsync(Ct);
        var stderr = await p.StandardError.ReadToEndAsync(Ct);
        await p.WaitForExitAsync(Ct);
        Assert.True(p.ExitCode == 0, stderr);
        using var doc = JsonDocument.Parse(stdout);
        Assert.Equal(80, doc.RootElement.GetProperty("measured").GetProperty("scheduled").GetInt64());
        Assert.Equal(0, doc.RootElement.GetProperty("measured").GetProperty("errors").GetInt64());
        Assert.False(doc.RootElement.GetProperty("telemetry").GetBoolean());
        Assert.True(doc.RootElement.GetProperty("warmup").GetProperty("scheduled").GetInt64() == 40);
    }

    [Fact]
    public async Task Cli_rejects_bad_arguments_with_exit_code_2()
    {
        var dll = Path.Combine(AppContext.BaseDirectory, "loadgen.dll");
        using var p = Process.Start(new ProcessStartInfo("dotnet") { ArgumentList = { dll, "window", "--rate", "0" }, RedirectStandardError = true })!;
        await p.WaitForExitAsync(Ct);
        Assert.Equal(2, p.ExitCode);
    }
}

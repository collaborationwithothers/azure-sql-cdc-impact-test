using System.Text.Json;
using System.Text.Json.Serialization;

namespace CdcImpact.Common;

/// <summary>Configurations from design section 2.</summary>
public enum Configuration
{
    /// <summary>Task update only, no outbox.</summary>
    A,
    /// <summary>Task update plus outbox insert.</summary>
    B,
    /// <summary>B with CDC on the outbox and Debezium reading it.</summary>
    C,
}

/// <summary>
/// Everything a run needs, passed to the container as JSON in RIG_SETTINGS.
/// Holds no secrets: the rig authenticates with its managed identity.
/// </summary>
public sealed record RunSettings
{
    public string RunId { get; init; } = "local";
    /// <summary>spike, smoke, quick, targeted or confirm.</summary>
    public string Mode { get; init; } = "smoke";

    public string SqlServer { get; init; } = "";
    public string Database1 { get; init; } = "cdcdb1";
    public string Database2 { get; init; } = "cdcdb2";
    /// <summary>Client ID of the user-assigned identity. Empty for local runs.</summary>
    public string ClientId { get; init; } = "";
    /// <summary>Full connection string template for container tests, with {db} as the database placeholder. Overrides managed identity.</summary>
    public string? ConnectionStringTemplate { get; init; }

    public string ResultsAccount { get; init; } = "";
    public string ResultsContainer { get; init; } = "results";
    public string ResultsDirectory { get; init; } = "/tmp/results";

    public string EventHubsNamespace { get; init; } = "";
    public string EventHubName { get; init; } = "cdc.cdcdb1.dbo.Outbox";
    public string DebeziumHome { get; init; } = "/debezium";

    public double SeedGb { get; init; } = 10;
    public long SeedBatchRows { get; init; } = 500_000;
    public int Matters { get; init; } = 50_000;

    public Configuration[] Configurations { get; init; } = [Configuration.A, Configuration.B, Configuration.C];
    public int[] Steps { get; init; } = [100, 200, 400, 800, 1200, 1600];
    public int Repeats { get; init; } = 1;
    public int WarmupSeconds { get; init; } = 60;
    public int MeasureSeconds { get; init; } = 120;
    /// <summary>Stop climbing when achieved rate falls below this fraction of target.</summary>
    public double StopRatio { get; init; } = 0.95;

    public int Database2Rate { get; init; } = 50;
    public int PayloadChars { get; init; } = 4096;
    /// <summary>nvarchar(max) (decision D1) or varchar(max).</summary>
    public string PayloadType { get; init; } = "nvarchar(max)";
    public int MaxPoolSize { get; init; } = 200;
    public int CommandTimeoutSeconds { get; init; } = 30;

    public bool RunPurge { get; init; } = true;
    public int PurgeBatch { get; init; } = 5000;
    public long PurgeMinRows { get; init; } = 200_000;
    public bool RunCleanup { get; init; } = true;

    public int CollectorIntervalSeconds { get; init; } = 10;
    public int LogDrainTimeoutSeconds { get; init; } = 900;
    public int DebeziumCatchUpTimeoutSeconds { get; init; } = 900;

    /// <summary>none or fail. "fail" throws after setup, to prove the failure path.</summary>
    public string InjectFailure { get; init; } = "none";

    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        Converters = { new JsonStringEnumConverter() },
        WriteIndented = false,
    };

    public static RunSettings FromJson(string json) =>
        JsonSerializer.Deserialize<RunSettings>(json, Json) ?? throw new InvalidOperationException("RIG_SETTINGS is empty");

    public static RunSettings FromEnvironment()
    {
        var json = Environment.GetEnvironmentVariable("RIG_SETTINGS");
        if (string.IsNullOrWhiteSpace(json))
        {
            throw new InvalidOperationException("RIG_SETTINGS is not set");
        }
        var settings = FromJson(json);
        var clientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
        return string.IsNullOrEmpty(settings.ClientId) && !string.IsNullOrEmpty(clientId)
            ? settings with { ClientId = clientId }
            : settings;
    }

    /// <summary>Rejects settings that would corrupt a measurement.</summary>
    public void Validate()
    {
        if (Steps.Length == 0 || Steps.Any(s => s <= 0)) throw new ArgumentException("Steps must be positive");
        if (MeasureSeconds < 1 || WarmupSeconds < 0) throw new ArgumentException("Bad step timing");
        if (Mode is "quick" or "confirm" && MeasureSeconds < 120)
            throw new ArgumentException("Design section 7: measured window must be at least 2 minutes for quick and confirming runs");
        if (PayloadType is not ("nvarchar(max)" or "varchar(max)")) throw new ArgumentException("PayloadType");
        if (Configurations.Length == 0) throw new ArgumentException("No configurations");
        if (StopRatio is <= 0 or > 1) throw new ArgumentException("StopRatio");
    }
}

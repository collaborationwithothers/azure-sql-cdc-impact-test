using Microsoft.Data.SqlClient;

namespace CdcImpact.LoadGen;

/// <summary>Command-line options. Parsed by hand: the tool has a handful of flags.</summary>
public sealed record Options
{
    public string Command { get; init; } = "";
    public string Server { get; init; } = "";
    public string Database { get; init; } = "";
    /// <summary>Client ID of the user-assigned managed identity.</summary>
    public string ClientId { get; init; } = "";
    /// <summary>For container tests: a full connection string. Overrides server and identity.</summary>
    public string? ConnectionString { get; init; }
    public Transaction Transaction { get; init; } = Transaction.UpdateAndOutbox;
    public int Rate { get; init; }
    public int WarmupSeconds { get; init; }
    public int MeasureSeconds { get; init; }
    public int WindowSeconds { get; init; } = 10;
    public long MaxTaskId { get; init; }
    public int PayloadChars { get; init; } = 4096;
    public string PayloadType { get; init; } = "nvarchar(max)";
    public int MaxPoolSize { get; init; } = 200;
    public int CommandTimeoutSeconds { get; init; } = 30;
    public string? Output { get; init; }
    /// <summary>on (default) or off. Off lets a targeted pass measure telemetry overhead.</summary>
    public bool Telemetry { get; init; } = true;
    /// <summary>Labels attached to telemetry, for example run id, configuration and step.</summary>
    public Dictionary<string, object> Labels { get; init; } = [];

    public static Options Parse(string[] args)
    {
        if (args.Length == 0) throw new ArgumentException(Usage);
        var o = new Options { Command = args[0], ClientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID") ?? "" };
        for (var i = 1; i < args.Length; i += 2)
        {
            if (i + 1 >= args.Length) throw new ArgumentException($"Missing value for {args[i]}");
            var v = args[i + 1];
            o = args[i] switch
            {
                "--server" => o with { Server = v },
                "--database" => o with { Database = v },
                "--client-id" => o with { ClientId = v },
                "--connection-string" => o with { ConnectionString = v },
                "--transaction" => o with { Transaction = v switch { "update" => Transaction.UpdateOnly, "update-outbox" => Transaction.UpdateAndOutbox, _ => throw new ArgumentException("--transaction update|update-outbox") } },
                "--rate" => o with { Rate = int.Parse(v) },
                "--warmup" => o with { WarmupSeconds = int.Parse(v) },
                "--measure" => o with { MeasureSeconds = int.Parse(v) },
                "--window" => o with { WindowSeconds = int.Parse(v) },
                "--max-task-id" => o with { MaxTaskId = long.Parse(v) },
                "--payload-chars" => o with { PayloadChars = int.Parse(v) },
                "--payload-type" => o with { PayloadType = v },
                "--max-pool" => o with { MaxPoolSize = int.Parse(v) },
                "--command-timeout" => o with { CommandTimeoutSeconds = int.Parse(v) },
                "--output" => o with { Output = v },
                "--telemetry" => o with { Telemetry = v switch { "on" => true, "off" => false, _ => throw new ArgumentException("--telemetry on|off") } },
                "--label" => o with { Labels = AddLabel(o.Labels, v) },
                _ => throw new ArgumentException($"Unknown option {args[i]}\n{Usage}"),
            };
        }
        o.Validate();
        return o;
    }

    private static Dictionary<string, object> AddLabel(Dictionary<string, object> labels, string kv)
    {
        var eq = kv.IndexOf('=');
        if (eq <= 0) throw new ArgumentException("--label key=value");
        return new Dictionary<string, object>(labels) { [kv[..eq]] = kv[(eq + 1)..] };
    }

    private void Validate()
    {
        if (Command is not ("window" or "steady")) throw new ArgumentException(Usage);
        if (Rate <= 0) throw new ArgumentException("--rate must be positive");
        if (MaxTaskId <= 0) throw new ArgumentException("--max-task-id must be positive");
        if (ConnectionString is null && (Server == "" || Database == "")) throw new ArgumentException("--server and --database are required");
        if (Command == "window" && MeasureSeconds <= 0) throw new ArgumentException("--measure must be positive");
        if (Command == "steady" && Output is null) throw new ArgumentException("steady needs --output");
        if (PayloadType is not ("nvarchar(max)" or "varchar(max)")) throw new ArgumentException("--payload-type");
    }

    public string BuildConnectionString()
    {
        if (ConnectionString is not null)
        {
            return new SqlConnectionStringBuilder(ConnectionString) { MaxPoolSize = MaxPoolSize }.ConnectionString;
        }
        return new SqlConnectionStringBuilder
        {
            DataSource = $"tcp:{Server},1433",
            InitialCatalog = Database,
            Authentication = SqlAuthenticationMethod.ActiveDirectoryManagedIdentity,
            UserID = ClientId,
            Encrypt = SqlConnectionEncryptOption.Mandatory,
            ConnectTimeout = 60,
            MaxPoolSize = MaxPoolSize,
            ApplicationName = "cdc-impact-loadgen",
        }.ConnectionString;
    }

    public const string Usage = """
        loadgen window --server S --database D --transaction update|update-outbox --rate N --warmup S --measure S --max-task-id N [options]
        loadgen steady --server S --database D --transaction update|update-outbox --rate N --window S --max-task-id N --output FILE [options]
        options: --client-id ID --connection-string CS --payload-chars N --payload-type nvarchar(max)|varchar(max) --max-pool N --command-timeout S
                 --telemetry on|off --label key=value (repeatable). Telemetry goes to APPLICATIONINSIGHTS_CONNECTION_STRING if set.
        """;
}

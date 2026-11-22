using System.Diagnostics;
using System.Text;
using CdcImpact.Common;

namespace CdcImpact.Orchestrator;

/// <summary>
/// Runs Debezium Server as a child process of the orchestrator, inside the
/// same container (decision 0008). It runs only in configuration C.
/// Debezium reads CDC change tables in database 1 and publishes to the Event
/// Hubs Kafka endpoint. Both connections use the rig's managed identity
/// through the classes in debezium/auth.
/// </summary>
public sealed class DebeziumProcess(RunSettings settings, ResultSink sink) : IAsyncDisposable
{
    public const int MetricsPort = 9404;
    private Process? _process;
    private StreamWriter? _log;

    public static string BuildProperties(RunSettings s, string dataDir)
    {
        var p = new StringBuilder();
        void Add(string k, string v) => p.Append(k).Append('=').Append(v).Append('\n');

        // Sink: Kafka protocol to Event Hubs, Entra token via OAUTHBEARER. No key.
        Add("debezium.sink.type", "kafka");
        Add("debezium.sink.kafka.producer.bootstrap.servers", $"{s.EventHubsNamespace}.servicebus.windows.net:9093");
        Add("debezium.sink.kafka.producer.security.protocol", "SASL_SSL");
        Add("debezium.sink.kafka.producer.sasl.mechanism", "OAUTHBEARER");
        Add("debezium.sink.kafka.producer.sasl.jaas.config", "org.apache.kafka.common.security.oauthbearer.OAuthBearerLoginModule required;");
        Add("debezium.sink.kafka.producer.sasl.login.callback.handler.class", "cdcimpact.EventHubsTokenHandler");
        Add("debezium.sink.kafka.producer.key.serializer", "org.apache.kafka.common.serialization.StringSerializer");
        Add("debezium.sink.kafka.producer.value.serializer", "org.apache.kafka.common.serialization.StringSerializer");
        // Event Hubs Standard does not support Kafka compression.
        Add("debezium.sink.kafka.producer.compression.type", "none");
        Add("debezium.sink.kafka.producer.max.request.size", "1000000");
        Add("debezium.sink.kafka.producer.linger.ms", "20");

        // Source: SQL Server connector against database 1, outbox table only.
        Add("debezium.source.connector.class", "io.debezium.connector.sqlserver.SqlServerConnector");
        Add("debezium.source.database.hostname", s.SqlServer);
        Add("debezium.source.database.port", "1433");
        Add("debezium.source.database.names", s.Database1);
        Add("debezium.source.driver.encrypt", "true");
        Add("debezium.source.driver.trustServerCertificate", "false");
        Add("debezium.source.driver.accessTokenCallbackClass", "cdcimpact.SqlTokenCallback");
        Add("debezium.source.topic.prefix", "cdc");
        Add("debezium.source.table.include.list", "dbo.Outbox");
        Add("debezium.source.snapshot.mode", "no_data");
        Add("debezium.source.include.schema.changes", "false");
        // Azure SQL Database has no SQL Server Agent; CDC runs on its own scheduler.
        Add("debezium.source.database.sqlserver.agent.status.query", "SELECT 1");
        Add("debezium.source.offset.storage", "org.apache.kafka.connect.storage.FileOffsetBackingStore");
        Add("debezium.source.offset.storage.file.filename", Path.Combine(dataDir, "offsets.dat"));
        Add("debezium.source.offset.flush.interval.ms", "10000");
        Add("debezium.source.schema.history.internal", "io.debezium.storage.file.history.FileSchemaHistory");
        Add("debezium.source.schema.history.internal.file.filename", Path.Combine(dataDir, "schema-history.dat"));

        Add("debezium.format.key", "json");
        Add("debezium.format.value", "json");
        Add("debezium.format.key.schemas.enable", "false");
        Add("debezium.format.value.schemas.enable", "false");
        Add("quarkus.log.level", "INFO");
        Add("quarkus.http.port", "8080");
        return p.ToString();
    }

    public void Start()
    {
        var dataDir = Path.Combine(Path.GetTempPath(), "debezium-data");
        if (Directory.Exists(dataDir)) Directory.Delete(dataDir, recursive: true);
        Directory.CreateDirectory(dataDir);
        File.WriteAllText(Path.Combine(settings.DebeziumHome, "config", "application.properties"), BuildProperties(settings, dataDir));

        var psi = new ProcessStartInfo(Path.Combine(settings.DebeziumHome, "run.sh"))
        {
            WorkingDirectory = settings.DebeziumHome,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        };
        psi.Environment["JMX_EXPORTER_PORT"] = MetricsPort.ToString();
        psi.Environment["JAVA_OPTS"] = "-Xms1g -Xmx3g";
        _log = new StreamWriter(Path.Combine(sink.Directory_, "debezium.log"), append: true) { AutoFlush = true };
        _process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        _process.OutputDataReceived += (_, e) => { if (e.Data is not null) lock (_log) _log.WriteLine(e.Data); };
        _process.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (_log) _log.WriteLine(e.Data); };
        if (!_process.Start()) throw new InvalidOperationException("Debezium Server did not start");
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();
    }

    public bool HasExited => _process?.HasExited ?? true;

    public async Task StopAsync()
    {
        if (_process is null) return;
        if (!_process.HasExited)
        {
            // SIGTERM first, so Debezium flushes offsets.
            // bash's built-in kill: the runtime image has no procps.
            using var kill = Process.Start("/bin/bash", ["-c", $"kill -TERM {_process.Id}"]);
            kill?.WaitForExit();
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
            try
            {
                await _process.WaitForExitAsync(cts.Token);
            }
            catch (OperationCanceledException)
            {
                _process.Kill(entireProcessTree: true);
            }
        }
        _process.Dispose();
        _process = null;
        _log?.Dispose();
        _log = null;
    }

    public async ValueTask DisposeAsync() => await StopAsync();
}

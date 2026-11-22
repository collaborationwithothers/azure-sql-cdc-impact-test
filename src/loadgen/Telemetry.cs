using System.Diagnostics;
using System.Diagnostics.Metrics;
using Azure.Identity;
using Azure.Monitor.OpenTelemetry.Exporter;
using OpenTelemetry;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

namespace CdcImpact.LoadGen;

/// <summary>
/// OpenTelemetry for the load generator (decision 0011): a span for every SQL
/// call, plus transaction counters and a latency histogram, exported to
/// Application Insights with the managed identity. Sampling is 100 percent;
/// the exporter's default is 5 traces per second. Published figures never come
/// from here: they come from the exact counts in WindowResult.
/// </summary>
public sealed class Telemetry : IDisposable
{
    public const string SourceName = "CdcImpact.LoadGen";
    public static readonly ActivitySource Source = new(SourceName);
    private static readonly Meter Meter = new(SourceName);
    public static readonly Counter<long> Completed = Meter.CreateCounter<long>("loadgen.transactions.completed");
    public static readonly Counter<long> Failed = Meter.CreateCounter<long>("loadgen.transactions.failed");
    public static readonly Histogram<double> Latency = Meter.CreateHistogram<double>("loadgen.transaction.latency", "ms");

    private readonly TracerProvider? _tracer;
    private readonly MeterProvider? _meter;

    private Telemetry(TracerProvider? tracer, MeterProvider? meter)
    {
        _tracer = tracer;
        _meter = meter;
    }

    public bool Enabled => _tracer is not null;

    /// <summary>Starts exporters, or nothing when telemetry is off or no connection string is set.</summary>
    public static Telemetry Start(bool enabled, string? connectionString, string clientId, IDictionary<string, object> attributes)
    {
        if (!enabled || string.IsNullOrEmpty(connectionString)) return new Telemetry(null, null);
        var credential = new ManagedIdentityCredential(ManagedIdentityId.FromUserAssignedClientId(clientId));
        var resource = ResourceBuilder.CreateDefault().AddService("loadgen").AddAttributes(attributes);
        var tracer = Sdk.CreateTracerProviderBuilder()
            .SetResourceBuilder(resource)
            .AddSource(SourceName)
            .AddSqlClientInstrumentation()
            .AddAzureMonitorTraceExporter(o =>
            {
                o.ConnectionString = connectionString;
                o.Credential = credential;
                o.SamplingRatio = 1.0f;
                o.TracesPerSecond = null;
            })
            .Build();
        var meter = Sdk.CreateMeterProviderBuilder()
            .SetResourceBuilder(resource)
            .AddMeter(SourceName)
            .AddSqlClientInstrumentation()
            .AddAzureMonitorMetricExporter(o =>
            {
                o.ConnectionString = connectionString;
                o.Credential = credential;
            })
            .Build();
        return new Telemetry(tracer, meter);
    }

    public void Dispose()
    {
        _tracer?.ForceFlush(10_000);
        _meter?.ForceFlush(10_000);
        _tracer?.Dispose();
        _meter?.Dispose();
    }
}

using System.Text.Json;
using CdcImpact.LoadGen;

// window: warm-up then measured window at one rate; prints one JSON object.
// steady: fixed windows at one rate until SIGTERM; appends one JSON line per window.
// Exit code 0 on completion, 2 on bad arguments, 1 on any other failure.

Options options;
try
{
    options = Options.Parse(args);
}
catch (Exception e) when (e is ArgumentException or FormatException)
{
    Console.Error.WriteLine(e.Message);
    return 2;
}

using var cts = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
using var sigterm = System.Runtime.InteropServices.PosixSignalRegistration.Create(
    System.Runtime.InteropServices.PosixSignal.SIGTERM, ctx => { ctx.Cancel = true; cts.Cancel(); });

var json = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
var workload = new TaskWorkload(options.BuildConnectionString(), options.Transaction, options.MaxTaskId, options.PayloadChars, options.PayloadType, options.CommandTimeoutSeconds);
var labels = new Dictionary<string, object>(options.Labels) { ["database"] = options.Database, ["transaction"] = options.Transaction.ToString(), ["target_rate"] = options.Rate };
using var telemetry = Telemetry.Start(options.Telemetry, Environment.GetEnvironmentVariable("APPLICATIONINSIGHTS_CONNECTION_STRING"), options.ClientId, labels);
var tags = labels.Select(kv => new KeyValuePair<string, object?>(kv.Key, kv.Value)).ToArray();

// One span per transaction, parent of the SQL client span.
async Task Execute(CancellationToken ct)
{
    using var activity = telemetry.Enabled ? Telemetry.Source.StartActivity("transaction") : null;
    var started = System.Diagnostics.Stopwatch.GetTimestamp();
    try
    {
        await workload.ExecuteAsync(ct);
        if (telemetry.Enabled)
        {
            Telemetry.Completed.Add(1, tags);
            Telemetry.Latency.Record(System.Diagnostics.Stopwatch.GetElapsedTime(started).TotalMilliseconds, tags);
        }
    }
    catch (Exception e) when (e is not OperationCanceledException)
    {
        if (telemetry.Enabled) Telemetry.Failed.Add(1, tags);
        activity?.SetStatus(System.Diagnostics.ActivityStatusCode.Error, e.Message);
        throw;
    }
}
// Bounded concurrency, like an API's connection pool (decision D2). The
// pending queue holds at most ten seconds of schedule.
var runner = new OpenLoopRunner(options.MaxPoolSize, Math.Max(1000, options.Rate * 10));

try
{
    if (options.Command == "window")
    {
        WindowResult? warmup = null;
        var warmupStart = DateTime.UtcNow;
        if (options.WarmupSeconds > 0)
        {
            warmup = await runner.RunAsync(options.Rate, TimeSpan.FromSeconds(options.WarmupSeconds), Execute, cts.Token);
        }
        var measureStart = DateTime.UtcNow;
        var measured = await runner.RunAsync(options.Rate, TimeSpan.FromSeconds(options.MeasureSeconds), Execute, cts.Token);
        var measureEnd = DateTime.UtcNow;
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            warmup_start = warmupStart,
            measure_start = measureStart,
            measure_end = measureEnd,
            warmup,
            measured,
            achieved_rate = measured.AchievedRate,
            processor_count = Environment.ProcessorCount,
            telemetry = telemetry.Enabled,
        }, json));
    }
    else
    {
        await using var output = new StreamWriter(options.Output!, append: true) { AutoFlush = true };
        while (!cts.IsCancellationRequested)
        {
            var start = DateTime.UtcNow;
            var w = await runner.RunAsync(options.Rate, TimeSpan.FromSeconds(options.WindowSeconds), Execute, cts.Token);
            if (cts.IsCancellationRequested) break; // a cut-short window is not a valid sample
            await output.WriteLineAsync(JsonSerializer.Serialize(new { window_start = start, window_end = DateTime.UtcNow, result = w, achieved_rate = w.AchievedRate, telemetry = telemetry.Enabled }, json));
        }
    }
    return 0;
}
catch (Exception e)
{
    Console.Error.WriteLine(e);
    return 1;
}

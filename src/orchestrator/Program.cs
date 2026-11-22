using System.Text.Json;
using CdcImpact.Common;
using CdcImpact.Orchestrator;

// Entry point of the test rig. Exit code 0 means the run completed; anything
// else marks the run failed. The Container Apps job has retries set to zero,
// so a failed run is never retried (design section 5).

var json = Environment.GetEnvironmentVariable("RIG_SETTINGS") ?? throw new InvalidOperationException("RIG_SETTINGS is not set");
var settings = Modes.Resolve(json);
var clientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
if (string.IsNullOrEmpty(settings.ClientId) && !string.IsNullOrEmpty(clientId)) settings = settings with { ClientId = clientId };
settings.Validate();

using var cts = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
AppDomain.CurrentDomain.ProcessExit += (_, _) => cts.Cancel();

await using var sink = new ResultSink(settings);
var runner = new Runner(settings, sink);
var started = DateTime.UtcNow;
string status;
string? error = null;
try
{
    Console.WriteLine($"run {settings.RunId} mode {settings.Mode} starting");
    await runner.RunAsync(cts.Token);
    status = "succeeded";
}
catch (Exception e)
{
    status = "failed";
    error = e.GetType().Name + ": " + e.Message;
    Console.Error.WriteLine(e);
}

var manifest = new
{
    run_id = settings.RunId,
    mode = settings.Mode,
    status,
    error,
    started,
    finished = DateTime.UtcNow,
    checks = runner.Checks,
};
sink.WriteFile("manifest.json", JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }));
try
{
    await sink.UploadAsync(CancellationToken.None);
    Console.WriteLine("results uploaded");
}
catch (Exception e)
{
    Console.Error.WriteLine("upload failed: " + e.Message);
    status = "failed";
}
Console.WriteLine($"run {settings.RunId} {status}");
return status == "succeeded" ? 0 : 1;

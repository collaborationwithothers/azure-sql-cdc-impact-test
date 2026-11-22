using System.Collections.Concurrent;
using System.Text.Json;
using Azure.Identity;
using Azure.Storage.Blobs;

namespace CdcImpact.Common;

/// <summary>Where a record belongs in the test matrix. Every record carries one (design section 6).</summary>
public sealed record RunContext(string RunId, string Phase, string? Configuration, int? StepTps, int? Repeat)
{
    public RunContext With(string phase, Configuration? configuration = null, int? step = null, int? repeat = null) =>
        this with { Phase = phase, Configuration = configuration?.ToString() ?? Configuration, StepTps = step, Repeat = repeat ?? Repeat };
}

/// <summary>
/// Appends JSON Lines records to local files, one file per stream, and uploads
/// them to blob storage under results/RUN_ID/. Upload is idempotent: each call
/// overwrites the blobs with the current full files.
/// Records hold no server names or principal identifiers.
/// </summary>
public sealed class ResultSink : IAsyncDisposable
{
    private readonly RunSettings _settings;
    private readonly string _dir;
    private readonly ConcurrentDictionary<string, StreamWriter> _writers = new();
    private readonly object _lock = new();

    public ResultSink(RunSettings settings)
    {
        _settings = settings;
        _dir = Path.Combine(settings.ResultsDirectory, settings.RunId);
        Directory.CreateDirectory(_dir);
    }

    public string Directory_ => _dir;

    public void Write(string stream, RunContext ctx, object payload)
    {
        var record = new Dictionary<string, object?>
        {
            ["ts"] = DateTime.UtcNow,
            ["run_id"] = ctx.RunId,
            ["phase"] = ctx.Phase,
            ["configuration"] = ctx.Configuration,
            ["step_tps"] = ctx.StepTps,
            ["repeat"] = ctx.Repeat,
            ["data"] = payload,
        };
        var line = JsonSerializer.Serialize(record, RunSettings.Json);
        lock (_lock)
        {
            var w = _writers.GetOrAdd(stream, s => new StreamWriter(Path.Combine(_dir, s + ".jsonl"), append: true) { AutoFlush = true });
            w.WriteLine(line);
        }
    }

    public void WriteFile(string name, string content)
    {
        File.WriteAllText(Path.Combine(_dir, name), content);
    }

    public async Task UploadAsync(CancellationToken ct)
    {
        if (string.IsNullOrEmpty(_settings.ResultsAccount)) return;
        lock (_lock)
        {
            foreach (var w in _writers.Values) w.Flush();
        }
        var credential = new ManagedIdentityCredential(ManagedIdentityId.FromUserAssignedClientId(_settings.ClientId));
        var container = new BlobContainerClient(new Uri($"https://{_settings.ResultsAccount}.blob.core.windows.net/{_settings.ResultsContainer}"), credential);
        foreach (var file in Directory.GetFiles(_dir))
        {
            await using var fs = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            await container.GetBlobClient($"{_settings.RunId}/{Path.GetFileName(file)}").UploadAsync(fs, overwrite: true, ct);
        }
    }

    public ValueTask DisposeAsync()
    {
        lock (_lock)
        {
            foreach (var w in _writers.Values) w.Dispose();
            _writers.Clear();
        }
        return ValueTask.CompletedTask;
    }
}

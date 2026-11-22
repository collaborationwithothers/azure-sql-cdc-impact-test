using System.Diagnostics;

namespace CdcImpact.LoadGen;

/// <summary>Result of one open-loop window.</summary>
public sealed record WindowResult
{
    public required int TargetRate { get; init; }
    public required double DurationSeconds { get; init; }
    /// <summary>Transactions the schedule asked for.</summary>
    public required long Scheduled { get; init; }
    /// <summary>Transactions that committed within the window (by completion time).</summary>
    public required long Completed { get; init; }
    public required long Errors { get; init; }
    /// <summary>Scheduled transactions dropped because the pending queue was full.</summary>
    public required long Shed { get; init; }
    /// <summary>Completed per second over the window.</summary>
    public double AchievedRate => Completed / DurationSeconds;
    /// <summary>Latency from the scheduled start time to commit, so queueing delay counts.</summary>
    public required LatencySummary Latency { get; init; }
    /// <summary>Process CPU used during the window, as percent of all cores.</summary>
    public required double ClientCpuPercent { get; init; }
    public required IReadOnlyDictionary<string, long> ErrorsByType { get; init; }
}

public sealed record LatencySummary(long Count, double MeanMs, double P50Ms, double P95Ms, double P99Ms, double MaxMs)
{
    public static LatencySummary From(List<double> samplesMs)
    {
        if (samplesMs.Count == 0) return new LatencySummary(0, 0, 0, 0, 0, 0);
        samplesMs.Sort();
        return new LatencySummary(
            samplesMs.Count,
            samplesMs.Average(),
            Percentile(samplesMs, 0.50),
            Percentile(samplesMs, 0.95),
            Percentile(samplesMs, 0.99),
            samplesMs[^1]);
    }

    /// <summary>Nearest-rank percentile on a sorted list.</summary>
    public static double Percentile(List<double> sorted, double p)
    {
        var rank = (int)Math.Ceiling(p * sorted.Count);
        return sorted[Math.Clamp(rank - 1, 0, sorted.Count - 1)];
    }
}

/// <summary>
/// Open-loop load: transactions are scheduled at a fixed rate whatever the
/// database does. A transaction that cannot start because all connections are
/// busy waits, and its latency includes that wait, measured from the scheduled
/// start. This avoids coordinated omission, where a slow system silently
/// receives less load. Concurrency is bounded, like an API's connection pool
/// (decision D2), and the pending queue is bounded so a saturated database
/// cannot exhaust client memory; excess is counted as shed.
/// </summary>
public sealed class OpenLoopRunner(int maxConcurrency, int maxPending)
{
    public async Task<WindowResult> RunAsync(int rate, TimeSpan duration, Func<CancellationToken, Task> transaction, CancellationToken ct)
    {
        if (rate <= 0) throw new ArgumentOutOfRangeException(nameof(rate));
        var gate = new SemaphoreSlim(maxConcurrency, maxConcurrency);
        var latencies = new List<double>((int)Math.Min(rate * duration.TotalSeconds * 1.1, 10_000_000));
        var errorsByType = new Dictionary<string, long>();
        var sync = new object();
        long completed = 0, errors = 0, shed = 0, pending = 0, scheduled = 0;
        var inFlight = new List<Task>();

        var process = Process.GetCurrentProcess();
        var cpuStart = process.TotalProcessorTime;
        var sw = Stopwatch.StartNew();
        var freq = Stopwatch.Frequency;
        var durationTicks = (long)(duration.TotalSeconds * freq);

        async Task Execute(long dueTicks)
        {
            try
            {
                await gate.WaitAsync(ct);
                try
                {
                    await transaction(ct);
                    var done = sw.ElapsedTicks;
                    var ms = (done - dueTicks) * 1000.0 / freq;
                    lock (sync)
                    {
                        if (done <= durationTicks) completed++;
                        latencies.Add(ms);
                    }
                }
                finally
                {
                    gate.Release();
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
            }
            catch (Exception e)
            {
                lock (sync)
                {
                    errors++;
                    var key = e.GetType().Name + (e is Microsoft.Data.SqlClient.SqlException se ? ":" + se.Number : "");
                    errorsByType[key] = errorsByType.GetValueOrDefault(key) + 1;
                }
            }
            finally
            {
                Interlocked.Decrement(ref pending);
            }
        }

        long i = 0;
        while (!ct.IsCancellationRequested)
        {
            var due = i * freq / rate;
            if (due >= durationTicks) break;
            var now = sw.ElapsedTicks;
            if (due > now)
            {
                var waitMs = (due - now) * 1000 / freq;
                if (waitMs >= 2) await Task.Delay((int)(waitMs - 1), ct).ContinueWith(_ => { }, TaskScheduler.Default);
                else Thread.SpinWait(50);
                continue;
            }
            // Issue every transaction that is due now, so a late wake-up does not lower the rate.
            scheduled++;
            i++;
            if (Interlocked.Read(ref pending) >= maxPending)
            {
                shed++;
                continue;
            }
            Interlocked.Increment(ref pending);
            var t = Execute(due);
            if (!t.IsCompleted) inFlight.Add(t);
            if (inFlight.Count > 4096) inFlight.RemoveAll(x => x.IsCompleted);
        }

        var elapsedAtScheduleEnd = sw.Elapsed;
        // Let in-flight transactions finish so their latency is recorded, but
        // only completions inside the window count towards the achieved rate.
        await Task.WhenAll(inFlight).WaitAsync(TimeSpan.FromSeconds(120), CancellationToken.None).ContinueWith(_ => { }, TaskScheduler.Default);
        var cpu = (process.TotalProcessorTime - cpuStart).TotalSeconds;
        var wall = Math.Max(sw.Elapsed.TotalSeconds, 0.001);

        List<double> snapshot;
        long c, e2, s2;
        Dictionary<string, long> eb;
        lock (sync)
        {
            snapshot = [.. latencies];
            c = completed;
            e2 = errors;
            s2 = shed;
            eb = new Dictionary<string, long>(errorsByType);
        }

        return new WindowResult
        {
            TargetRate = rate,
            DurationSeconds = Math.Max(elapsedAtScheduleEnd.TotalSeconds, duration.TotalSeconds),
            Scheduled = scheduled,
            Completed = c,
            Errors = e2,
            Shed = s2,
            Latency = LatencySummary.From(snapshot),
            ClientCpuPercent = 100.0 * cpu / (wall * Environment.ProcessorCount),
            ErrorsByType = eb,
        };
    }
}

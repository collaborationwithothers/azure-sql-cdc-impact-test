using CdcImpact.LoadGen;
using Xunit;

namespace CdcImpact.Tests.Unit;

public class OpenLoopRunnerTests
{
    [Fact]
    public async Task Schedules_target_rate_against_fast_backend()
    {
        var runner = new OpenLoopRunner(maxConcurrency: 50, maxPending: 10_000);
        var r = await runner.RunAsync(200, TimeSpan.FromSeconds(2), _ => Task.CompletedTask, TestContext.Current.CancellationToken);
        Assert.Equal(400, r.Scheduled);
        Assert.InRange(r.Completed, 390, 400);
        Assert.Equal(0, r.Errors);
        Assert.InRange(r.AchievedRate, 190, 201);
    }

    [Fact]
    public async Task Open_loop_keeps_scheduling_when_backend_is_slow_and_latency_includes_queueing()
    {
        // 10 concurrent slots, 50 ms per call: capacity 200/s. Target 400/s.
        var runner = new OpenLoopRunner(maxConcurrency: 10, maxPending: 100_000);
        var r = await runner.RunAsync(400, TimeSpan.FromSeconds(2), ct => Task.Delay(50, ct), TestContext.Current.CancellationToken);
        Assert.Equal(800, r.Scheduled); // the schedule does not slow down
        Assert.True(r.AchievedRate < 0.95 * 400, $"achieved {r.AchievedRate}");
        // Queueing makes later transactions wait far longer than the 50 ms service time.
        Assert.True(r.Latency.P99Ms > 500, $"p99 {r.Latency.P99Ms}");
    }

    [Fact]
    public async Task Sheds_when_pending_queue_is_full()
    {
        var runner = new OpenLoopRunner(maxConcurrency: 1, maxPending: 5);
        var r = await runner.RunAsync(500, TimeSpan.FromSeconds(1), ct => Task.Delay(100, ct), TestContext.Current.CancellationToken);
        Assert.True(r.Shed > 400, $"shed {r.Shed}");
    }

    [Fact]
    public async Task Counts_errors_by_type()
    {
        var runner = new OpenLoopRunner(10, 1000);
        var r = await runner.RunAsync(100, TimeSpan.FromSeconds(1), _ => throw new TimeoutException(), TestContext.Current.CancellationToken);
        Assert.Equal(100, r.Errors);
        Assert.Equal(0, r.Completed);
        Assert.Equal(100, r.ErrorsByType["TimeoutException"]);
    }
}

public class LatencyTests
{
    [Fact]
    public void Nearest_rank_percentiles()
    {
        var samples = Enumerable.Range(1, 100).Select(i => (double)i).ToList();
        var s = LatencySummary.From(samples);
        Assert.Equal(50, s.P50Ms);
        Assert.Equal(95, s.P95Ms);
        Assert.Equal(99, s.P99Ms);
        Assert.Equal(100, s.MaxMs);
        Assert.Equal(100, s.Count);
    }

    [Fact]
    public void Empty_is_zero()
    {
        Assert.Equal(0, LatencySummary.From([]).Count);
    }
}

public class PayloadTests
{
    [Theory]
    [InlineData(4096)]
    [InlineData(1000)]
    public void Payload_has_exact_length_for_any_id(int chars)
    {
        var t = TaskWorkload.BuildPayloadTemplate(chars);
        Assert.Equal(chars, TaskWorkload.Payload(t, 1).Length);
        Assert.Equal(chars, TaskWorkload.Payload(t, long.MaxValue).Length);
        Assert.StartsWith("{\"type\":\"TaskUpdated\"", TaskWorkload.Payload(t, 7));
    }

    [Fact]
    public void Update_only_has_no_outbox_insert()
    {
        Assert.DoesNotContain("Outbox", TaskWorkload.UpdateOnlySql);
        Assert.Contains("INSERT dbo.Outbox", TaskWorkload.UpdateAndOutboxSql);
        Assert.Contains("BEGIN TRAN", TaskWorkload.UpdateAndOutboxSql);
    }
}

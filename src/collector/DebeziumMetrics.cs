using System.Globalization;
using CdcImpact.Common;

namespace CdcImpact.Collector;

/// <summary>
/// Reads Debezium's JMX metrics through the Prometheus JMX exporter bundled in
/// Debezium Server, started by setting JMX_EXPORTER_PORT. Only debezium
/// metric lines are kept.
/// </summary>
public sealed class DebeziumMetrics(HttpClient http, Uri endpoint)
{
    public async Task<Dictionary<string, double>> ScrapeAsync(CancellationToken ct)
    {
        var text = await http.GetStringAsync(endpoint, ct);
        return Parse(text);
    }

    /// <summary>Parses Prometheus text format. Keeps lines whose metric name starts with "debezium".</summary>
    public static Dictionary<string, double> Parse(string text)
    {
        var result = new Dictionary<string, double>();
        foreach (var raw in text.Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line[0] == '#' || !line.StartsWith("debezium", StringComparison.OrdinalIgnoreCase)) continue;
            var space = line.LastIndexOf(' ');
            if (space <= 0) continue;
            var name = line[..space].Trim();
            var valueText = line[(space + 1)..];
            if (double.TryParse(valueText, NumberStyles.Float, CultureInfo.InvariantCulture, out var value))
            {
                result[name] = value;
            }
        }
        return result;
    }

    /// <summary>
    /// First value whose key contains every fragment, ignoring case and
    /// underscores, so "TotalNumberOfEventsSeen" matches "total_number_of_events_seen".
    /// </summary>
    public static double? Find(Dictionary<string, double> metrics, params string[] fragments)
    {
        static string Norm(string s) => s.Replace("_", "", StringComparison.Ordinal).ToLowerInvariant();
        var wanted = fragments.Select(Norm).ToArray();
        foreach (var (k, v) in metrics)
        {
            var key = Norm(k);
            if (wanted.All(key.Contains)) return v;
        }
        return null;
    }

    public async Task RunAsync(ResultSink sink, Func<RunContext> context, TimeSpan interval, CancellationToken ct)
    {
        using var timer = new PeriodicTimer(interval);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var m = await ScrapeAsync(ct);
                sink.Write("debezium_metrics", context(), m);
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                sink.Write("collector_errors", context(), new { stream = "debezium_metrics", error = e.Message });
            }
            try
            {
                if (!await timer.WaitForNextTickAsync(ct)) break;
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }
}

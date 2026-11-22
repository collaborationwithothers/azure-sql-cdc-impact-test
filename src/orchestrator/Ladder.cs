using CdcImpact.LoadGen;

namespace CdcImpact.Orchestrator;

/// <summary>Stop rule for the load ladder (design section 7).</summary>
public static class Ladder
{
    /// <summary>
    /// True when the measured window fell below the stop ratio of its target,
    /// so higher steps would only measure a saturated system.
    /// </summary>
    public static bool ShouldStop(WindowResult measured, double stopRatio) =>
        measured.AchievedRate < stopRatio * measured.TargetRate;

    /// <summary>Pending queue bound: ten seconds of schedule, at least 1,000.</summary>
    public static int MaxPending(int rate) => Math.Max(1000, rate * 10);
}

using System.Text.Json.Nodes;
using CdcImpact.Common;

namespace CdcImpact.Orchestrator;

/// <summary>
/// Presets for each mode in design section 7. Settings passed in RIG_SETTINGS
/// override the preset key by key.
/// </summary>
public static class Modes
{
    public static JsonObject Preset(string mode) => mode switch
    {
        // Spike: prove the mechanics (design section 12) with minimal pool time.
        "spike" => new JsonObject
        {
            ["configurations"] = new JsonArray("C"),
            ["steps"] = new JsonArray(100),
            ["warmupSeconds"] = 30,
            ["measureSeconds"] = 60,
            ["seedGb"] = 0.25,
            ["purgeMinRows"] = 20000,
        },
        // Smoke: one configuration, two steps, about 10 minutes.
        "smoke" => new JsonObject
        {
            ["configurations"] = new JsonArray("C"),
            ["steps"] = new JsonArray(100, 200),
            ["warmupSeconds"] = 60,
            ["measureSeconds"] = 120,
            ["seedGb"] = 0.5,
            ["purgeMinRows"] = 50000,
        },
        // Quick run: the default grid.
        "quick" => new JsonObject
        {
            ["configurations"] = new JsonArray("A", "B", "C"),
            ["steps"] = new JsonArray(100, 200, 400, 800, 1200, 1600),
            ["warmupSeconds"] = 60,
            ["measureSeconds"] = 120,
            ["seedGb"] = 10,
        },
        // Confirming run: configuration C only; steps are chosen from the quick run.
        "confirm" => new JsonObject
        {
            ["configurations"] = new JsonArray("C"),
            ["warmupSeconds"] = 60,
            ["measureSeconds"] = 120,
            ["seedGb"] = 10,
        },
        "targeted" => new JsonObject(),
        _ => throw new ArgumentException($"Unknown mode {mode}"),
    };

    /// <summary>Preset for the mode in <paramref name="json"/>, overlaid with every key present in it.</summary>
    public static RunSettings Resolve(string json)
    {
        var given = JsonNode.Parse(json)?.AsObject() ?? throw new ArgumentException("RIG_SETTINGS is not a JSON object");
        var mode = given["mode"]?.GetValue<string>() ?? "smoke";
        var merged = Preset(mode);
        foreach (var (key, value) in given)
        {
            merged[key] = value?.DeepClone();
        }
        merged["mode"] = mode;
        return RunSettings.FromJson(merged.ToJsonString());
    }
}

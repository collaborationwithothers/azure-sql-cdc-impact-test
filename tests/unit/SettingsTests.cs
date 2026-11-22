using CdcImpact.Collector;
using CdcImpact.Common;
using CdcImpact.Orchestrator;
using Xunit;

namespace CdcImpact.Tests.Unit;

public class ModesTests
{
    [Fact]
    public void Quick_preset_matches_design_section_7()
    {
        var s = Modes.Resolve("{\"mode\":\"quick\"}");
        Assert.Equal([100, 200, 400, 800, 1200, 1600], s.Steps);
        Assert.Equal([Configuration.A, Configuration.B, Configuration.C], s.Configurations);
        Assert.Equal(60, s.WarmupSeconds);
        Assert.Equal(120, s.MeasureSeconds);
        Assert.Equal(10, s.SeedGb);
        Assert.Equal(50, s.Database2Rate);
        Assert.Equal(0.95, s.StopRatio);
        Assert.Equal("nvarchar(max)", s.PayloadType);
        s.Validate();
    }

    [Fact]
    public void Given_keys_override_preset()
    {
        var s = Modes.Resolve("{\"mode\":\"confirm\",\"steps\":[800,1200,1600],\"runId\":\"r1\"}");
        Assert.Equal([800, 1200, 1600], s.Steps);
        Assert.Equal([Configuration.C], s.Configurations);
        Assert.Equal("r1", s.RunId);
    }

    [Fact]
    public void Quick_rejects_shortened_window()
    {
        var s = Modes.Resolve("{\"mode\":\"quick\",\"measureSeconds\":60}");
        Assert.Throws<ArgumentException>(s.Validate);
    }

    [Fact]
    public void Unknown_mode_is_rejected()
    {
        Assert.Throws<ArgumentException>(() => Modes.Resolve("{\"mode\":\"nope\"}"));
    }
}

public class DebeziumConfigTests
{
    private static readonly RunSettings S = new() { SqlServer = "srv.database.windows.net", EventHubsNamespace = "ns", Database1 = "cdcdb1" };

    [Fact]
    public void Uses_managed_identity_classes_and_no_secret()
    {
        var p = DebeziumProcess.BuildProperties(S, "/tmp/x");
        Assert.Contains("debezium.source.driver.accessTokenCallbackClass=cdcimpact.SqlTokenCallback", p);
        Assert.Contains("debezium.sink.kafka.producer.sasl.login.callback.handler.class=cdcimpact.EventHubsTokenHandler", p);
        Assert.Contains("debezium.sink.kafka.producer.bootstrap.servers=ns.servicebus.windows.net:9093", p);
        Assert.DoesNotContain("password", p, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("connectionstring", p, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("SharedAccessKey", p, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void Captures_outbox_only_without_snapshot()
    {
        var p = DebeziumProcess.BuildProperties(S, "/tmp/x");
        Assert.Contains("debezium.source.table.include.list=dbo.Outbox", p);
        Assert.Contains("debezium.source.snapshot.mode=no_data", p);
        Assert.Contains("debezium.source.database.names=cdcdb1", p);
    }
}

public class MetricsParseTests
{
    private const string Sample = """
        # HELP debezium_metrics_TotalNumberOfEventsSeen x
        # TYPE debezium_metrics_TotalNumberOfEventsSeen gauge
        debezium_metrics_TotalNumberOfEventsSeen{context="streaming",server="cdc",task="0",database="cdcdb1",} 1234.0
        debezium_metrics_MilliSecondsBehindSource{context="streaming",server="cdc",} 21500.0
        debezium_metrics_Connected{context="streaming",server="cdc",} 1.0
        jvm_memory_bytes_used{area="heap",} 1.0E8
        """;

    [Fact]
    public void Keeps_debezium_lines_and_finds_by_fragment()
    {
        var m = DebeziumMetrics.Parse(Sample);
        Assert.Equal(3, m.Count);
        Assert.Equal(1234, DebeziumMetrics.Find(m, "streaming", "TotalNumberOfEventsSeen"));
        Assert.Equal(21500, DebeziumMetrics.Find(m, "milli_seconds_behind_source"));
        Assert.Equal(1, DebeziumMetrics.Find(m, "streaming", "Connected"));
        Assert.Null(DebeziumMetrics.Find(m, "snapshot", "Connected"));
    }
}

public class SqlScriptTests
{
    [Fact]
    public void Cdc_blocks_load()
    {
        Assert.Contains("sp_cdc_enable_table", SqlScripts.Block("cdc.sql", "enable"));
        Assert.Contains("sp_cdc_disable_db", SqlScripts.Block("cdc.sql", "disable"));
        Assert.Contains("sp_cdc_cleanup_change_table", SqlScripts.Block("cdc.sql", "cleanup"));
        Assert.DoesNotContain("sp_cdc_cleanup_change_table", SqlScripts.Block("cdc.sql", "enable"));
    }

    [Fact]
    public void Schema_has_payload_token()
    {
        Assert.Contains("{{PAYLOAD_TYPE}}", SqlScripts.Load("schema.sql"));
    }
}

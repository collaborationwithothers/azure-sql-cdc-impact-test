# Verification: Application Insights and OpenTelemetry

Checked: 2026-11-22 by the documentation-verification agent against Microsoft
Learn, NuGet, PyPI and GitHub releases.

## Entra-only ingestion

Sources: https://learn.microsoft.com/azure/azure-monitor/app/azure-ad-authentication,
https://learn.microsoft.com/azure/azure-monitor/app/connection-strings

- With local authentication disabled, ingestion accepts only Entra-authenticated
  telemetry. The identity needs "Monitoring Metrics Publisher" on the
  Application Insights resource. VERIFIED.
- The connection string is still required (instrumentation key and ingestion
  endpoint). With local authentication off it carries no write access.
  VERIFIED.
- azurerm 5.x argument: `local_authentication_enabled` (default true). 4.x used
  `local_authentication_disabled`. VERIFIED from provider docs.

## .NET

- `Azure.Monitor.OpenTelemetry.Exporter` 1.10.0, stable. Options take
  `ConnectionString` and `Credential` (a `TokenCredential`). VERIFIED.
- Default trace sampling is rate limited to 5 traces per second, not 100
  percent. REFUTED the 100 percent assumption. Full tracing needs
  `SamplingRatio = 1.0f` and `TracesPerSecond = null`, or
  `OTEL_TRACES_SAMPLER=microsoft.fixed_percentage` and
  `OTEL_TRACES_SAMPLER_ARG=1.0`.
- `OpenTelemetry.Instrumentation.SqlClient` 1.21.0, stable, supports
  Microsoft.Data.SqlClient. Support for SqlClient 7.x is not stated.
  UNVERIFIABLE; proven by the container tests.
- For a short-lived process, dispose providers at exit so telemetry flushes.

## Python

- `azure-monitor-opentelemetry` 1.8.12, Python 3.10 or later,
  `configure_azure_monitor(connection_string=..., credential=...)`. VERIFIED.
- Same rate-limited default sampling. Set `sampling_ratio=1.0` or the
  environment variables above.

## Java agent for Debezium

Sources: https://learn.microsoft.com/azure/azure-monitor/app/java-standalone-config,
https://github.com/microsoft/ApplicationInsights-Java/releases

- `applicationinsights-agent` 3.7.10 is the latest release. VERIFIED.
- `jmxMetrics` collects named JMX attributes, sent every
  `metricIntervalSeconds` (default 60). VERIFIED.
- Entra authentication with a user-assigned identity:
  `"authentication": {"enabled": true, "type": "UAMI", "clientId": "..."}`.
  VERIFIED.
- Working inside Azure Container Apps: UNVERIFIABLE. The spike tests it.

## Not checked

Learn recommends a newer "Microsoft OpenTelemetry Distro" for new apps. Not
researched; this repo uses the verified Azure Monitor packages above.

# 0011. Application Insights with full per-request tracing

Date: 2026-11-22
Decided by: the owner (asked for Application Insights, chose full per-request
tracing, persistent resource, Java agent for Debezium with a fallback).

## Context

Results reach blob storage only at checkpoints. Nothing is visible during a
run, and a run that dies before upload leaves little behind. The owner asked
for Application Insights instrumentation.

## Decision

- **Resource.** Workspace-based Application Insights in the persistent layer,
  on the existing Log Analytics workspace, with local authentication disabled.
  The rig identity holds "Monitoring Metrics Publisher" on it.
- **Connection string.** Read from the Application Insights resource by the
  ephemeral Terraform and passed into the job's environment. Never committed,
  never handled by a person. With local authentication off it grants no write
  access, so it is an identifier, not a credential (see
  `docs/verification/2026-11-22-app-insights.md`). This is the owner's
  explicit exception to stop condition 2 for this one value.
- **Load generator.** OpenTelemetry traces for every SQL call
  (`OpenTelemetry.Instrumentation.SqlClient`) and metrics, exported with the
  managed identity. Sampling is set to 100 percent explicitly, because the
  exporter default is 5 traces per second. `--telemetry off` disables it.
- **Orchestrator.** Python, `azure-monitor-opentelemetry`, spans per phase and
  step, logs.
- **Debezium.** Application Insights Java agent 3.7.10 with `jmxMetrics` for
  the Debezium MBeans and UAMI authentication. If it does not work on
  Container Apps, the fallback is for the orchestrator to forward the
  Debezium metrics it already scrapes from `localhost:9404` as OpenTelemetry
  metrics.

## Consequence for the measurements

Per-request tracing adds work inside the process whose latency is published.
Until a run with `--telemetry off` measures that overhead, every report states
that client-side latency includes telemetry overhead. Published figures come
from the load generator's own exact counts and the SQL DMVs, never from
Application Insights.

## Alternatives rejected

- **Aggregated metrics with 1 percent traces.** Recommended by the agent, not
  chosen.
- **Azure Monitor workspace (managed Prometheus) and Managed Grafana.** Needs
  a collector sidecar to scrape and remote-write anyway, adds an idle cost, and
  Grafana only visualises. Kept as a last resort.

## How it is verified

Container tests check the load generator runs with telemetry on. The spike
checks telemetry from all three components arrives in Application Insights.

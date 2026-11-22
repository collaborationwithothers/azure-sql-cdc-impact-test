"""Application Insights for the orchestrator (decision 0011). 100 percent sampling.

Telemetry must never break or change a run: any failure here is printed and ignored.
"""
from __future__ import annotations

import os


def configure(client_id: str, run_id: str, enabled: bool) -> bool:
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not enabled or not connection_string:
        return False
    try:
        # The exporter default is rate-limited sampling at 5 traces per second.
        os.environ.setdefault("OTEL_TRACES_SAMPLER", "microsoft.fixed_percentage")
        os.environ.setdefault("OTEL_TRACES_SAMPLER_ARG", "1.0")
        os.environ.setdefault("OTEL_SERVICE_NAME", "orchestrator")
        os.environ.setdefault("OTEL_RESOURCE_ATTRIBUTES", f"run_id={run_id}")
        from azure.identity import ManagedIdentityCredential
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=connection_string, credential=ManagedIdentityCredential(client_id=client_id),
                                sampling_ratio=1.0)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"telemetry disabled: {e}", flush=True)
        return False


def flush() -> None:
    try:
        from opentelemetry import metrics, trace

        for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
            if hasattr(provider, "force_flush"):
                provider.force_flush(10_000)
    except Exception:  # noqa: BLE001
        pass

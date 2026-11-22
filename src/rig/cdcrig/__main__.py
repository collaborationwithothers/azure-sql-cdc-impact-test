"""Entry point of the rig container.

Exit code 0: the run completed. Anything else: the run failed. The job has
retries set to zero, so a failed run is never retried (design section 5).
"""
from __future__ import annotations

import json
import os
import sys
import traceback

from . import settings as settings_mod
from . import telemetry
from .debezium import Control
from .orchestrator import Orchestrator
from .sink import Sink, utcnow


class _Tee:
    """Copies console output into the results, so a failed run still explains itself."""

    def __init__(self, stream, path):
        self._stream = stream
        self._file = open(path, "a", buffering=1)  # noqa: SIM115 - lives for the process

    def write(self, data):
        self._stream.write(data)
        self._file.write(data)
        return len(data)

    def flush(self):
        self._stream.flush()
        self._file.flush()


def main() -> int:
    s = settings_mod.resolve(os.environ.get("RIG_SETTINGS", "{}"))
    s.validate()
    sink = Sink(s.resultsDirectory, s.runId, s.resultsAccount, s.resultsContainer, s.clientId)
    sys.stdout = _Tee(sys.__stdout__, sink.dir / "orchestrator.log")
    sys.stderr = _Tee(sys.__stderr__, sink.dir / "orchestrator.log")
    telemetry_on = telemetry.configure(s.clientId, s.runId, s.telemetry)
    orchestrator = Orchestrator(s, sink)
    started = utcnow()
    status, error = "failed", None
    print(f"run {s.runId} mode {s.mode} starting; telemetry {'on' if telemetry_on else 'off'}", flush=True)
    try:
        orchestrator.run()
        status = "succeeded"
    except BaseException as e:  # noqa: BLE001 - every failure must produce a manifest
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        Control(s.sharedDirectory).done()  # lets the Debezium container exit, so the job completes

    debezium_log = Control(s.sharedDirectory).dir / "debezium.log"
    if debezium_log.exists():
        sink.write_file("debezium.log", debezium_log.read_text(errors="replace"))
    sink.write_file("manifest.json", json.dumps({
        "run_id": s.runId, "mode": s.mode, "status": status, "error": error, "started": started, "finished": utcnow(),
        "telemetry": telemetry_on, "checks": orchestrator.checks,
    }, indent=2, default=str))
    try:
        print(f"uploaded {sink.upload()} files", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"upload failed: {e}", flush=True)
        status = "failed"
    telemetry.flush()
    print(f"run {s.runId} {status}", flush=True)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())

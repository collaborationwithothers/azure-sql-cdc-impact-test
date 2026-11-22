"""Results: JSON Lines files per stream, uploaded to blob storage under RUN_ID/.

Records carry the run ID, phase, configuration, step and repeat (design
section 6). They hold no server names or principal identifiers.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Context:
    run_id: str
    phase: str = "setup"
    configuration: str | None = None
    step_tps: int | None = None
    repeat: int | None = 1

    def at(self, phase: str, configuration: str | None = None, step: int | None = None, repeat: int | None = None) -> "Context":
        return replace(self, phase=phase, configuration=configuration if configuration is not None else self.configuration,
                       step_tps=step, repeat=repeat if repeat is not None else self.repeat)


class Sink:
    def __init__(self, directory: str, run_id: str, account: str = "", container: str = "results", client_id: str = ""):
        self.dir = Path(directory) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._account = account
        self._container = container
        self._client_id = client_id
        self._lock = threading.Lock()

    def write(self, stream: str, ctx: Context, data) -> None:
        record = {"ts": utcnow(), "run_id": ctx.run_id, "phase": ctx.phase, "configuration": ctx.configuration,
                  "step_tps": ctx.step_tps, "repeat": ctx.repeat, "data": data}
        line = json.dumps(record, default=str)
        with self._lock, open(self.dir / f"{stream}.jsonl", "a") as f:
            f.write(line + "\n")

    def write_file(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text)

    def upload(self) -> int:
        """Uploads every file in the run directory, overwriting. Returns the file count."""
        if not self._account:
            return 0
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import ContainerClient

        client = ContainerClient(f"https://{self._account}.blob.core.windows.net", self._container,
                                 credential=ManagedIdentityCredential(client_id=self._client_id))
        n = 0
        with self._lock:
            for path in sorted(self.dir.iterdir()):
                if path.is_file():
                    with open(path, "rb") as f:
                        client.upload_blob(f"{self._run_id}/{path.name}", f, overwrite=True)
                    n += 1
        return n

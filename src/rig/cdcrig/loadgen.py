"""Runs the .NET load generator (src/loadgen) as a subprocess."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .settings import Settings

TRANSACTION = {"A": "update", "B": "update-outbox", "C": "update-outbox"}


def _common(s: Settings, database: str, configuration: str, rate: int, max_task_id: int, labels: dict) -> list[str]:
    args = [s.loadgenPath]
    if s.odbcTemplate:
        args += ["--connection-string", _sqlclient_from_odbc(s.odbcTemplate.replace("{db}", database))]
    else:
        args += ["--server", s.sqlServer, "--client-id", s.clientId]
    args += ["--database", database, "--transaction", TRANSACTION[configuration], "--rate", str(rate),
             "--max-task-id", str(max_task_id), "--payload-chars", str(s.payloadChars), "--payload-type", s.payloadType,
             "--max-pool", str(s.maxPoolSize), "--telemetry", "on" if s.telemetry else "off"]
    for k, v in labels.items():
        args += ["--label", f"{k}={v}"]
    return args


def _sqlclient_from_odbc(odbc: str) -> str:
    """Container tests only: turns the ODBC test string into a SqlClient one."""
    parts = dict(p.split("=", 1) for p in odbc.split(";") if "=" in p)
    server = parts.get("Server", "").replace("tcp:", "")
    return (f"Server={server};Database={parts.get('Database', '')};User ID={parts.get('UID', '')};"
            f"Password={parts.get('PWD', '')};TrustServerCertificate=True;Encrypt=True")


def window(s: Settings, database: str, configuration: str, rate: int, max_task_id: int, labels: dict) -> dict:
    """Warm-up then measured window. Returns the load generator's JSON result."""
    args = _common(s, database, configuration, rate, max_task_id, labels)
    args[1:1] = ["window"]
    args += ["--warmup", str(s.warmupSeconds), "--measure", str(s.measureSeconds)]
    timeout = s.warmupSeconds + s.measureSeconds + 300
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"loadgen window exited {p.returncode}: {p.stderr[-2000:]}")
    return json.loads(p.stdout)


def start_steady(s: Settings, database: str, configuration: str, rate: int, max_task_id: int, output: Path, labels: dict) -> subprocess.Popen:
    """Constant load in fixed windows until terminated (database 2's neighbour load)."""
    args = _common(s, database, configuration, rate, max_task_id, labels)
    args[1:1] = ["steady"]
    args += ["--window", "10", "--output", str(output)]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

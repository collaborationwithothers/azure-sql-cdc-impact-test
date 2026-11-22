"""SQL connections and the scripts in /sql.

Azure runs authenticate with the rig's managed identity: a token from
azure-identity is passed to ODBC Driver 18 as SQL_COPT_SS_ACCESS_TOKEN.
"""
from __future__ import annotations

import re
import struct
import threading
import time
from pathlib import Path

import pyodbc

from .settings import Settings

SQL_COPT_SS_ACCESS_TOKEN = 1256
TOKEN_SCOPE = "https://database.windows.net/.default"

_SQL_DIRS = [Path(__file__).parent / "sql", Path(__file__).resolve().parents[3] / "sql"]


def sql_dir() -> Path:
    for d in _SQL_DIRS:
        if (d / "schema.sql").exists():
            return d
    raise FileNotFoundError("sql scripts not found")


def load(name: str) -> str:
    return (sql_dir() / name).read_text()


def block(name: str, block_name: str) -> str:
    """Returns the text after "-- name: BLOCK" up to the next marker."""
    parts = re.split(r"^--\s*name:\s*(\S+)\s*$", load(name), flags=re.MULTILINE)
    for i in range(1, len(parts) - 1, 2):
        if parts[i].strip() == block_name:
            return parts[i + 1].strip()
    raise KeyError(f"{name}: block {block_name}")


class Connector:
    """Opens connections to a database by name."""

    def __init__(self, settings: Settings):
        self._s = settings
        self._credential = None
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def _access_token(self) -> bytes:
        with self._lock:
            if self._token is None or self._token[1] - 300 < time.time():
                if self._credential is None:
                    from azure.identity import ManagedIdentityCredential

                    self._credential = ManagedIdentityCredential(client_id=self._s.clientId)
                t = self._credential.get_token(TOKEN_SCOPE)
                self._token = (t.token, t.expires_on)
            raw = self._token[0].encode("utf-16-le")
            return struct.pack("<I", len(raw)) + raw

    def connect(self, database: str, autocommit: bool = True) -> pyodbc.Connection:
        if self._s.odbcTemplate:
            return pyodbc.connect(self._s.odbcTemplate.replace("{db}", database), autocommit=autocommit, timeout=60)
        cs = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server=tcp:{self._s.sqlServer},1433;Database={database};"
            "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;APP=cdc-impact-rig"
        )
        return pyodbc.connect(cs, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: self._access_token()}, autocommit=autocommit)


def execute(conn: pyodbc.Connection, sql: str, *params, timeout: int = 600) -> int:
    conn.timeout = timeout
    cur = conn.cursor()
    cur.execute(sql, *params)
    count = cur.rowcount
    # Drain every result set so procedures run to completion and errors surface.
    while cur.nextset():
        pass
    cur.close()
    return count


def scalar(conn: pyodbc.Connection, sql: str, *params, timeout: int = 600):
    conn.timeout = timeout
    cur = conn.cursor()
    cur.execute(sql, *params)
    row = cur.fetchone()
    cur.close()
    return None if row is None else row[0]


def rows(conn: pyodbc.Connection, sql: str, timeout: int = 60) -> list[dict]:
    conn.timeout = timeout
    cur = conn.cursor()
    cur.execute(sql)
    names = [c[0] for c in cur.description]
    out = []
    for r in cur.fetchall():
        item = {}
        for n, v in zip(names, r):
            if isinstance(v, (bytes, bytearray)):
                v = v.hex().upper()
            elif hasattr(v, "isoformat"):
                v = v.isoformat()
            elif v is not None and not isinstance(v, (int, float, str, bool)):
                v = str(v)
            item[n] = v
        out.append(item)
    cur.close()
    return out

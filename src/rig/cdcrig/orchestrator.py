"""Walks the test matrix (design section 7).

Setup and seed, then for each repeat and configuration: reset, (C) enable CDC
and start Debezium, the load ladder, (B, C) the purge phase, (C) the CDC
cleanup phase. Database 2 carries a constant configuration B load throughout.
"""
from __future__ import annotations

import threading
import time
from contextlib import nullcontext

from . import collector, db, debezium, loadgen
from .settings import Settings
from .sink import Context, Sink, utcnow


def tracer_span(name: str, **attrs):
    try:
        from opentelemetry import trace

        span = trace.get_tracer("cdcrig").start_as_current_span(name)
        return _Span(span, attrs)
    except Exception:  # noqa: BLE001 - telemetry must never break a run
        return nullcontext()


class _Span:
    def __init__(self, cm, attrs):
        self._cm = cm
        self._attrs = attrs

    def __enter__(self):
        span = self._cm.__enter__()
        for k, v in self._attrs.items():
            if v is not None:
                span.set_attribute(k, v)
        return span

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def stop_rule(result: dict, target: int, ratio: float) -> bool:
    """True when the measured window fell below ratio x target: higher steps would measure a saturated system."""
    return result["achieved_rate"] < ratio * target


class Orchestrator:
    def __init__(self, settings: Settings, sink: Sink, connector: db.Connector | None = None):
        self.s = settings
        self.sink = sink
        self.connector = connector or db.Connector(settings)
        self.control = debezium.Control(settings.sharedDirectory)
        self.checks: dict[str, dict] = {}
        self._ctx = Context(settings.runId)
        self._ctx_lock = threading.Lock()
        self.max_task_id: dict[str, int] = {}

    # -- context and checks -------------------------------------------------

    @property
    def ctx(self) -> Context:
        with self._ctx_lock:
            return self._ctx

    def _set(self, phase: str, configuration: str | None = None, step: int | None = None, repeat: int | None = None) -> None:
        with self._ctx_lock:
            self._ctx = self._ctx.at(phase, configuration, step, repeat)

    def check(self, name: str, ok: bool, detail=None) -> None:
        self.checks[name] = {"ok": ok, "detail": detail}
        self.sink.write("checks", self.ctx, {"name": name, "ok": ok, "detail": detail})
        print(f"check {name}: {'ok' if ok else 'FAILED'} {detail if detail is not None else ''}", flush=True)

    def log(self, msg: str) -> None:
        c = self.ctx
        print(f"{utcnow()} [{c.phase} {c.configuration} {c.step_tps}] {msg}", flush=True)

    # -- run ---------------------------------------------------------------

    def run(self) -> None:
        self.control.prepare()
        self.control.heartbeat()
        self.sink.write("settings", self.ctx, self.s.public())
        stop_heartbeat = threading.Event()
        hb = threading.Thread(target=self._heartbeat_loop, args=(stop_heartbeat,), daemon=True)
        hb.start()
        try:
            with tracer_span("setup", run_id=self.s.runId):
                self.record_environment()
                self.setup_schema()
                self.seed()
            self.sink.upload()
            if self.s.injectFailure == "fail":
                raise RuntimeError("Injected failure (injectFailure=fail) to prove the destroy path")

            coll = collector.Collector(self.connector, self.sink, [self.s.database1, self.s.database2], lambda: self.ctx,
                                       self.s.collectorIntervalSeconds)
            coll.start()
            db2 = loadgen.start_steady(self.s, self.s.database2, "B", self.s.database2Rate, self.max_task_id[self.s.database2],
                                       self.sink.dir / "db2_windows.jsonl", {"run_id": self.s.runId, "role": "neighbour"})
            try:
                for repeat in range(1, self.s.repeats + 1):
                    for configuration in self.s.configurations:
                        with tracer_span("configuration", run_id=self.s.runId, configuration=configuration, repeat=repeat):
                            self.run_configuration(configuration, repeat)
                        self.sink.upload()
            finally:
                self._set("teardown")
                db2.terminate()
                try:
                    db2.wait(timeout=60)
                except Exception:  # noqa: BLE001
                    db2.kill()
                if db2.returncode not in (0, None, -15):
                    self.check("database2_load", False, (db2.stderr.read() if db2.stderr else "")[-2000:])
                coll.stop()
        finally:
            stop_heartbeat.set()

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        while not stop.wait(30):
            self.control.heartbeat()

    # -- setup -------------------------------------------------------------

    def record_environment(self) -> None:
        for database in (self.s.database1, self.s.database2):
            conn = self.connector.connect(database)
            try:
                self.check(f"connect_{database}", True)
                env = db.rows(conn,
                              "SELECT DB_NAME() AS database_name, CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition, "
                              "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version, "
                              "CAST(DATABASEPROPERTYEX(DB_NAME(), 'Edition') AS nvarchar(128)) AS edition, "
                              "CAST(DATABASEPROPERTYEX(DB_NAME(), 'ServiceObjective') AS nvarchar(128)) AS service_objective, "
                              "IS_ROLEMEMBER('db_owner') AS is_db_owner")
                self.sink.write("environment", self.ctx, {"database": database, "rows": env})
                try:
                    gov = db.rows(conn, collector.GOVERNANCE)
                    self.sink.write("governance", self.ctx, {"database": database, "rows": gov})
                    self.check(f"governance_{database}", len(gov) > 0, f"{len(gov)} rows")
                except Exception as e:  # noqa: BLE001
                    self.check(f"governance_{database}", False, str(e))
            finally:
                conn.close()
        # Spike item 6: every DMV the collector needs is readable with this identity.
        conn = self.connector.connect(self.s.database1)
        try:
            for stream, sql in {**collector.PER_DATABASE, **collector.POOL_LEVEL}.items():
                try:
                    self.check(f"dmv_{stream}", True, f"{len(db.rows(conn, sql))} rows")
                except Exception as e:  # noqa: BLE001
                    self.check(f"dmv_{stream}", False, str(e))
        finally:
            conn.close()

    def setup_schema(self) -> None:
        self._set("schema")
        schema = db.load("schema.sql").replace("{{PAYLOAD_TYPE}}", self.s.payloadType)
        seed_proc = db.load("seed-proc.sql")
        for database in (self.s.database1, self.s.database2):
            conn = self.connector.connect(database)
            try:
                db.execute(conn, schema)
                db.execute(conn, seed_proc)
                db.execute(conn, db.block("cdc.sql", "disable"))  # a previous configuration C may have left CDC on
            finally:
                conn.close()

    def seed(self) -> None:
        self._set("seed")
        results: dict[str, int] = {}
        errors: list[BaseException] = []

        def worker(database: str) -> None:
            try:
                results[database] = self.seed_database(database)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(d,)) for d in (self.s.database1, self.s.database2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        self.max_task_id = results

    def seed_database(self, database: str) -> int:
        """Seeds dbo.Task until its reserved size reaches seedGb GiB. Returns the highest TaskId."""
        conn = self.connector.connect(database)
        try:
            db.execute(conn,
                       "IF NOT EXISTS (SELECT 1 FROM dbo.Matter) INSERT dbo.Matter (MatterId, Name, CreatedAt) "
                       "SELECT TOP (?) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)), CONCAT(N'Matter ', ROW_NUMBER() OVER (ORDER BY (SELECT NULL))), "
                       "'2026-01-01' FROM sys.all_columns a CROSS JOIN sys.all_columns b", self.s.matters)
            target = int(self.s.seedGb * 1024 ** 3)
            size_sql = "SELECT ISNULL(SUM(reserved_page_count), 0) * 8192 FROM sys.dm_db_partition_stats WHERE object_id = OBJECT_ID('dbo.Task')"
            max_id = db.scalar(conn, "SELECT ISNULL(MAX(TaskId), 0) FROM dbo.Task")
            started = time.monotonic()
            batches = 0
            while db.scalar(conn, size_sql) < target:
                to = max_id + self.s.seedBatchRows
                db.execute(conn, "EXEC dbo.SeedTaskBatch ?, ?, ?", max_id, to, self.s.matters, timeout=1800)
                max_id = to
                batches += 1
                if batches % 20 == 0:
                    self.log(f"seed {database}: {db.scalar(conn, size_sql) // 2**20} MiB after {time.monotonic() - started:.0f} s")
            n = db.scalar(conn, "SELECT COUNT_BIG(*) FROM dbo.Task", timeout=1800)
            reserved = db.scalar(conn, size_sql)
            seconds = time.monotonic() - started
            self.sink.write("seed", self.ctx, {"database": database, "rows": n, "reserved_bytes": reserved, "max_task_id": max_id,
                                               "batches": batches, "seconds": seconds})
            self.log(f"seed {database} done: {n} rows, {reserved // 2**20} MiB, {seconds:.0f} s")
            return int(max_id)
        finally:
            conn.close()

    # -- one configuration --------------------------------------------------

    def run_configuration(self, configuration: str, repeat: int) -> None:
        self._set("reset", configuration, None, repeat)
        self.reset_database1()
        started_debezium = False
        try:
            if configuration == "C":
                self._set("cdc_enable", configuration, None, repeat)
                self.enable_cdc()
                self.start_debezium()
                started_debezium = True
            self.ladder(configuration, repeat)
            if configuration == "C":
                self._set("ladder_catchup", configuration, None, repeat)
                self.wait_for_catchup("ladder")
            if self.s.runPurge and configuration != "A":
                self.purge(configuration, repeat)
            if self.s.runCleanup and configuration == "C":
                self.cdc_cleanup(repeat)
        finally:
            if started_debezium:
                self.check(f"debezium_alive_at_end_r{repeat}", self.control.running())
                try:
                    self.control.stop()
                except Exception as e:  # noqa: BLE001
                    self.check("debezium_stop", False, str(e))

    def reset_database1(self) -> None:
        conn = self.connector.connect(self.s.database1)
        try:
            db.execute(conn, db.block("cdc.sql", "disable"))
            db.execute(conn, "TRUNCATE TABLE dbo.Outbox; CHECKPOINT;")
            started = time.monotonic()
            # Wait until CDC and open transactions release the log, then let it settle.
            while time.monotonic() - started < self.s.logDrainTimeoutSeconds:
                wait = db.scalar(conn, "SELECT log_reuse_wait_desc FROM sys.databases WHERE database_id = DB_ID()")
                if wait not in ("REPLICATION", "ACTIVE_TRANSACTION"):
                    break
                time.sleep(10)
            time.sleep(min(60, self.s.warmupSeconds))
            log = db.rows(conn, "SELECT u.used_log_space_in_percent, d.log_reuse_wait_desc FROM sys.dm_db_log_space_usage u "
                                "CROSS JOIN sys.databases d WHERE d.database_id = DB_ID()")
            self.sink.write("reset", self.ctx, {"seconds": time.monotonic() - started, "log": log})
        finally:
            conn.close()

    def enable_cdc(self) -> None:
        conn = self.connector.connect(self.s.database1)
        try:
            db.execute(conn, db.block("cdc.sql", "enable"))
            on = bool(db.scalar(conn, "SELECT is_tracked_by_cdc FROM sys.tables WHERE object_id = OBJECT_ID('dbo.Outbox')"))
            self.check("cdc_enable", on)
            if not on:
                raise RuntimeError("CDC was not enabled on dbo.Outbox")
        except Exception as e:
            if "cdc_enable" not in self.checks:
                self.check("cdc_enable", False, str(e))
            raise
        finally:
            conn.close()

    def start_debezium(self) -> None:
        started = time.monotonic()
        try:
            self.control.start(debezium.properties(self.s), timeout=60)
        except Exception as e:
            self.check("debezium_started", False, f"{e}; log tail: {self.control.log_tail()}")
            raise
        while time.monotonic() - started < self.s.debeziumStartTimeoutSeconds:
            if not self.control.running():
                break
            try:
                m = debezium.scrape(self.s.debeziumMetricsUrl)
                if debezium.find(m, "streaming", "Connected") == 1:
                    self.check("debezium_started", True, f"{time.monotonic() - started:.0f} s")
                    self.check("debezium_metrics_readable", len(m) > 0, f"{len(m)} series")
                    return
            except OSError:
                pass
            time.sleep(5)
        self.check("debezium_started", False, "not connected; log tail: " + self.control.log_tail())
        raise TimeoutError("Debezium did not connect")

    def ladder(self, configuration: str, repeat: int) -> None:
        for step in self.s.steps:
            self._set("measure", configuration, step, repeat)
            labels = {"run_id": self.s.runId, "configuration": configuration, "step_tps": step, "repeat": repeat}
            with tracer_span("step", **labels):
                result = loadgen.window(self.s, self.s.database1, configuration, step, self.max_task_id[self.s.database1], labels)
            if configuration == "C":
                result["debezium"] = self._debezium_snapshot()
            stop = stop_rule(result, step, self.s.stopRatio)
            self.sink.write("steps", self.ctx, {**result, "stopped": stop})
            m = result["measured"]
            self.log(f"achieved {result['achieved_rate']:.1f}/{step} tps, p95 {m['latency']['p95_ms']:.1f} ms, errors {m['errors']}, "
                     f"shed {m['shed']}, client cpu {m['client_cpu_percent']:.1f}%")
            if stop:
                self.log("stop rule: achieved rate below the stop ratio; not climbing further")
                break

    def _debezium_snapshot(self) -> dict | None:
        try:
            return debezium.scrape(self.s.debeziumMetricsUrl)
        except OSError:
            return None

    def wait_for_catchup(self, label: str) -> None:
        """Waits until CDC capture and then Debezium have processed every committed change.

        The change table alone is not a target: capture copies commits into it
        asynchronously, so it can lag the log (spike run 45054291170: 8,841 rows
        against 9,000 commits). A marker row is written first. Capture reads the
        log in order, so once the marker is in the change table every earlier
        commit is too. Only then is Debezium compared with the change table.
        """
        conn = self.connector.connect(self.s.database1)
        started = time.monotonic()
        ct_rows, seen, captured, capture_seconds = 0, None, False, None
        try:
            marker = db.scalar(conn, "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) "
                                     "OUTPUT inserted.OutboxId VALUES (0, 'CatchUpMarker', SYSUTCDATETIME(), N'{}')")
            while time.monotonic() - started < self.s.catchUpTimeoutSeconds:
                if not captured:
                    captured = db.scalar(conn, "SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT WHERE OutboxId = ?", marker) > 0
                    if captured:
                        capture_seconds = time.monotonic() - started
                if captured:
                    ct_rows = db.scalar(conn, "SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT")
                    try:
                        seen = debezium.find(debezium.scrape(self.s.debeziumMetricsUrl), "streaming", "TotalNumberOfEventsSeen")
                    except OSError:
                        seen = None
                    if seen is not None and seen >= ct_rows:
                        break
                time.sleep(2)
        finally:
            conn.close()
        caught = captured and seen is not None and seen >= ct_rows
        seconds = time.monotonic() - started
        self.sink.write("catchup", self.ctx, {"label": label, "seconds": seconds, "capture_seconds": capture_seconds,
                                              "change_table_rows": ct_rows, "debezium_events_seen": seen,
                                              "marker_captured": captured, "caught_up": caught})
        self.check(f"catchup_{label}_{self.ctx.configuration}_r{self.ctx.repeat}", caught,
                   f"{seconds:.0f} s (capture {capture_seconds if capture_seconds is None else round(capture_seconds)} s), "
                   f"events {seen}, change rows {ct_rows}")

    @staticmethod
    def _file_bytes(conn, type_desc: str) -> int:
        return int(db.scalar(conn, "SELECT SUM(s.num_of_bytes_written) FROM sys.dm_io_virtual_file_stats(DB_ID(), NULL) s "
                                   "JOIN sys.database_files f ON f.file_id = s.file_id WHERE f.type_desc = ?", type_desc))

    def purge(self, configuration: str, repeat: int) -> None:
        conn = self.connector.connect(self.s.database1)
        try:
            self._set("purge_topup", configuration, None, repeat)
            n = db.scalar(conn, "SELECT COUNT_BIG(*) FROM dbo.Outbox")
            if n < self.s.purgeMinRows:
                need = self.s.purgeMinRows - n
                payload = _payload(self.s.payloadChars)
                started = time.monotonic()
                while need > 0:
                    k = min(need, 10_000)
                    db.execute(conn, "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) "
                                     "SELECT TOP (?) TaskId, 'TopUp', SYSUTCDATETIME(), ? FROM dbo.Task", k, payload, timeout=1800)
                    need -= k
                self.sink.write("purge_topup", self.ctx, {"added": self.s.purgeMinRows - n, "seconds": time.monotonic() - started})
                if configuration == "C":
                    self.wait_for_catchup("topup")

            self._set("purge", configuration, None, repeat)
            before = db.scalar(conn, "SELECT COUNT_BIG(*) FROM dbo.Outbox")
            log0, data0 = self._file_bytes(conn, "LOG"), self._file_bytes(conn, "ROWS")
            started_at = utcnow()
            started = time.monotonic()
            batches = 0
            with tracer_span("purge", run_id=self.s.runId, configuration=configuration):
                while True:
                    deleted = db.execute(conn, "DELETE TOP (?) FROM dbo.Outbox", self.s.purgeBatch, timeout=1800)
                    batches += 1
                    if deleted < self.s.purgeBatch:
                        break
            seconds = time.monotonic() - started
            log1, data1 = self._file_bytes(conn, "LOG"), self._file_bytes(conn, "ROWS")
            self.sink.write("purge", self.ctx, {"rows_deleted": before, "batches": batches, "batch_size": self.s.purgeBatch,
                                                "seconds": seconds, "started": started_at, "log_bytes_written": log1 - log0,
                                                "data_bytes_written": data1 - data0})
            self.log(f"purge: {before} rows in {seconds:.0f} s, log {(log1 - log0) // 2**20} MiB")
        finally:
            conn.close()
        if configuration == "C":
            self._set("purge_catchup", configuration, None, repeat)
            self.wait_for_catchup("purge")

    def cdc_cleanup(self, repeat: int) -> None:
        self._set("cdc_cleanup", "C", None, repeat)
        conn = self.connector.connect(self.s.database1)
        try:
            try:
                db.execute(conn, db.block("cdc.sql", "lower-retention"))
                self.check("cdc_change_job_retention", True)
            except Exception as e:  # noqa: BLE001 - not documented for Azure SQL Database; cleanup below does not need it
                self.check("cdc_change_job_retention", False, str(e))
            # Cleanup removes change rows strictly below the low water mark (the
            # current maximum LSN). Write one marker row and wait for capture to
            # move past it, so every earlier change is below the mark.
            lsn0 = db.scalar(conn, "SELECT sys.fn_cdc_get_max_lsn()")
            db.execute(conn, "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) VALUES (0, 'CleanupMarker', SYSUTCDATETIME(), N'')")
            waited = time.monotonic()
            while db.scalar(conn, "SELECT sys.fn_cdc_get_max_lsn()") == lsn0 and time.monotonic() - waited < 180:
                time.sleep(2)
            self.check("cdc_marker_captured", db.scalar(conn, "SELECT sys.fn_cdc_get_max_lsn()") != lsn0, f"{time.monotonic() - waited:.0f} s")
            rows0 = db.scalar(conn, "SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT")
            log0, data0 = self._file_bytes(conn, "LOG"), self._file_bytes(conn, "ROWS")
            started = time.monotonic()
            try:
                with tracer_span("cdc_cleanup", run_id=self.s.runId):
                    db.execute(conn, db.block("cdc.sql", "cleanup"), timeout=3600)
                self.check("cdc_cleanup_on_demand", True)
            except Exception as e:  # noqa: BLE001
                self.check("cdc_cleanup_on_demand", False, str(e))
            seconds = time.monotonic() - started
            rows1 = db.scalar(conn, "SELECT COUNT_BIG(*) FROM cdc.dbo_Outbox_CT")
            log1, data1 = self._file_bytes(conn, "LOG"), self._file_bytes(conn, "ROWS")
            self.sink.write("cdc_cleanup", self.ctx, {"change_rows_before": rows0, "change_rows_after": rows1, "seconds": seconds,
                                                      "log_bytes_written": log1 - log0, "data_bytes_written": data1 - data0})
            self.log(f"cdc cleanup: {rows0} -> {rows1} change rows in {seconds:.0f} s")
        finally:
            conn.close()


def _payload(chars: int) -> str:
    """Same shape and length as the load generator's payload (src/loadgen/TaskWorkload.cs)."""
    head = '{"type":"TaskUpdated","taskId":"' + "0" * 20 + '","body":"'
    tail = '"}'
    return head + "x" * (chars - len(head) - len(tail)) + tail

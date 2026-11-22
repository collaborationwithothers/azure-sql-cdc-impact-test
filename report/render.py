"""Writes the report as Markdown. Leads with the answer, then evidence, then limitations.

Every table names the run ID it comes from. Sections say whether their figures
are measured (read from the raw files), derived (arithmetic on measured
figures) or an EXTRAPOLATION (a model beyond what was measured).
"""
from __future__ import annotations

import math
from collections import defaultdict

from . import analysis as A
from . import limits as L
from . import prices as P
from . import sizing as S
from . import storage as ST
from .load import Run, num
from .windows import Step

MIB = 1024 * 1024


def f(x, digits: int = 1, unit: str = "") -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if isinstance(x, float) and abs(x) >= 1000 and digits <= 1:
        s = f"{x:,.0f}"
    else:
        s = f"{x:,.{digits}f}"
    return s + (f" {unit}" if unit else "")


def table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return out + [""]


class Report:
    def __init__(self, runs: list[Run], metrics: list[A.StepMetrics], steps: dict[str, list[Step]],
                 applied: dict[str, S.Applied], price_data: dict | None, price_error: str | None, charts: list[tuple[str, str]],
                 duty_hours: float | None = None):
        self.duty_hours = duty_hours
        # {load: (C - B monthly compute difference, label)}, filled by sizing_section for storage_section.
        self.compute_diff: dict[int, tuple[float, str]] = {}
        self.runs = runs
        self.metrics = metrics
        self.steps = steps
        self.applied = applied
        self.price_data = price_data
        self.price_error = price_error
        self.charts = charts
        self.lines: list[str] = []
        self.data_notes: list[str] = []

    def w(self, *lines: str) -> None:
        self.lines.extend(lines)

    # -- helpers -------------------------------------------------------------

    def run_metrics(self, run_id: str) -> list[A.StepMetrics]:
        return [m for m in self.metrics if m.step.run_id == run_id]

    def first_limit(self, ms: list[A.StepMetrics]) -> str:
        by_tps: dict[int, list[A.StepMetrics]] = defaultdict(list)
        for m in ms:
            by_tps[m.step.step_tps].append(m)
        prev = None
        for t in sorted(by_tps):
            group = by_tps[t]
            reached = [m for m in group if m.binding.reached]
            failed = [m for m in group if not m.step.carries]
            if reached or failed:
                where = f"between {prev:,} and {t:,} tps" if prev is not None else f"at or below {t:,} tps (the first step)"
                if reached:
                    b = max(reached, key=lambda m: m.binding.percent).binding
                    conf = {True: "confirmed by wait types", False: "not confirmed by wait types",
                            None: "no wait confirmation available"}[b.confirmed]
                    carried = "load still carried" if all(m.step.carries for m in group) else "load not carried"
                    return f"**{b.label}** reached {where}: {f(b.percent)}% of limit at {t:,} tps, {conf}, {carried}."
                m = failed[0]
                return (f"**load not carried** {where} with no resource at or above {A.THRESHOLD:g}% of its limit "
                        f"(achieved {f(m.step.achieved)} of {f(m.step.target, 0)} tps, errors {f(m.step.errors, 0)}, "
                        f"shed {f(m.step.shed, 0)}). Look for a client, connection or capture limit.")
            prev = t
        top = max(by_tps) if by_tps else None
        return f"no limit reached up to {top:,} tps, the highest step measured." if top else "no steps measured."

    def governed_log(self, m: A.StepMetrics) -> float:
        a = self.applied.get(m.step.run_id)
        st = m.db1.get("avg_log_write_percent")
        return st.mean / 100 * a.db_log_rate if a and st else math.nan

    # -- sections --------------------------------------------------------------

    def header(self) -> None:
        ids = ", ".join(r.run_id for r in self.runs)
        self.w("# CDC impact on an Azure SQL elastic pool: report", "",
               f"Runs: {ids}. Generated from the raw result files of each run by `python -m report`.", "",
               "Figures are labelled **measured** (read from the run's raw files), **derived** (arithmetic on measured "
               "figures) or **EXTRAPOLATION** (a model beyond what was measured). Configurations: A no outbox, "
               "B outbox only, C outbox with change data capture (CDC) and Debezium reading it. CDC is SQL's built-in "
               "change log; Debezium is the reader that publishes those changes onward.", "")

    def answer(self) -> None:
        self.w("## Answer: which limit is reached first", "")
        for run in self.runs:
            s = run.settings
            a = self.applied.get(run.run_id)
            pool = f"{a.pool.vcores} vCore pool" if a and a.pool else "pool of unknown size"
            cap = f"per-database cap {f(a.db_cap_vcores, 0)} vCores" if a else "per-database cap unknown"
            self.w(f"Run {run.run_id} (mode {s.get('mode', 'unknown')}, {pool}, {cap}; measured):", "")
            ms = self.run_metrics(run.run_id)
            for c in sorted({m.step.configuration for m in ms}):
                self.w(f"- Configuration {c}: " + self.first_limit([m for m in ms if m.step.configuration == c]))
            if not ms:
                self.w("- No steps were measured.")
            if s.get("mode") in ("spike", "smoke"):
                self.w("", f"Mode {s.get('mode')} validates the harness. Its figures prove mechanics only and are not results "
                       "(design section 7; decision 0014).")
            if (s.get("repeats") or 1) == 1:
                self.w("", "One repeat: noise cannot be told from effect. A limit's load is known only to within the gap "
                       "between steps.")
            self.w("")

    def runs_table(self) -> None:
        self.w("## Runs (measured)", "")
        rows = []
        for run in self.runs:
            s, m = run.settings, run.manifest or {}
            a = self.applied.get(run.run_id)
            rows.append([run.run_id, s.get("mode", "n/a"), m.get("status", "no manifest"),
                         f"{a.pool.vcores}" if a and a.pool else "unknown", f(a.db_cap_vcores, 0) if a else "n/a",
                         ", ".join(s.get("configurations", [])), ", ".join(str(x) for x in s.get("steps", [])),
                         f"{s.get('warmupSeconds', 'n/a')} / {s.get('measureSeconds', 'n/a')}", s.get("repeats", "n/a"),
                         f"{s.get('database2Rate', 'n/a')} tps B", f"{s.get('payloadChars', 'n/a')} chars {s.get('payloadType', '')}",
                         m.get("started", "n/a"), m.get("finished", "n/a")])
        self.w(*table(["Run", "Mode", "Status", "Pool vCores", "DB cap vCores", "Configurations", "Steps (tps)",
                       "Warm-up / measured (s)", "Repeats", "Database 2 load", "Payload", "Started (UTC)", "Finished (UTC)"], rows))
        self.w("Pool vCores: the documented General Purpose standard-series size whose pool log rate and pool IOPS limits "
               "both equal the limits the run recorded from `sys.dm_user_db_resource_governance`.", "")

    def applied_table(self) -> None:
        self.w("## Applied limits (measured: recorded by each run)", "",
               "Recorded values win over documented ones. Documented values for the same size are shown beside them "
               f"(Microsoft Learn, checked {L.CHECKED}; see `docs/verification/2026-11-22-report-limits.md`).", "")
        rows = []
        for run in self.runs:
            a = self.applied.get(run.run_id)
            if not a:
                continue
            doc = a.pool
            rows.append([run.run_id, f(a.db_cap_vcores, 0), f(a.db_log_rate / MIB, 2), f(a.db_iops, 0), f(a.db_workers, 0),
                         f(a.pool_log_rate / MIB, 2) + (f" (doc {doc.max_log_rate_mib_s:g})" if doc else ""),
                         f(a.pool_iops, 0) + (f" (doc {doc.max_data_iops:,})" if doc else ""),
                         f(a.pool_workers, 0) + (f" (doc {doc.max_workers:,})" if doc else ""), f(a.instance_log_rate / MIB, 2)])
        self.w(*table(["Run", "DB 1 cap (vCores)", "DB 1 max log rate (MiB/s)", "DB 1 max IOPS", "DB 1 max workers",
                       "Pool max log rate (MiB/s)", "Pool max IOPS", "Pool max workers", "Instance max log rate (MiB/s)"], rows))

    def per_step(self) -> None:
        self.w("## Per step: load carried and the binding limit (measured)", "",
               f"A step carries its load when achieved rate is at least 95% of target and errors plus shed are 0. The "
               f"binding limit is the resource with the highest mean percent of its limit over the resource-stats rows "
               f"that lie wholly inside the measured window, among CPU, data IO, log write, workers and sessions at "
               f"database 1 and pool level. Below {A.THRESHOLD:g}% the step says \"none reached\". Wait-type deltas over "
               "the window confirm it: `SOS_SCHEDULER_YIELD` for CPU, `PAGEIOLATCH_*` for data IO, `LOG_RATE_GOVERNOR` "
               "(database) or `POOL_LOG_RATE_GOVERNOR` and `INSTANCE_LOG_RATE_GOVERNOR` (pool) for log write, "
               "`THREADPOOL` for workers.", "")
        for run in self.runs:
            ms = self.run_metrics(run.run_id)
            if not ms:
                continue
            self.w(f"Run {run.run_id}:", "")
            rows = []
            for m in ms:
                st = m.step
                rows.append([st.configuration, st.repeat, f"{st.step_tps:,}", f(st.seconds, 0), f(st.achieved),
                             f(st.ratio * 100), f(st.errors, 0), f(st.shed, 0), "yes" if st.carries else "**no**",
                             m.binding.label, f(m.binding.percent), m.binding.evidence])
            self.w(*table(["Config", "Repeat", "Target tps", "Window (s)", "Achieved tps", "% of target", "Errors", "Shed",
                           "Carries", "Binding limit", "Highest % of limit", "Wait evidence"], rows))
            rows = []
            for m in ms:
                st = m.step
                g = lambda src, c: f(src[c].mean) if c in src else "n/a"
                rows.append([st.configuration, st.repeat, f"{st.step_tps:,}",
                             g(m.db1, "avg_cpu_percent"), g(m.db1, "avg_data_io_percent"), g(m.db1, "avg_log_write_percent"),
                             g(m.db1, "max_worker_percent"), g(m.pool, "avg_cpu_percent"), g(m.pool, "avg_data_io_percent"),
                             g(m.pool, "avg_log_write_percent"), g(m.pool, "max_worker_percent"),
                             f"{m.db1.get('avg_cpu_percent').n if 'avg_cpu_percent' in m.db1 else 0} / "
                             f"{m.pool.get('avg_cpu_percent').n if 'avg_cpu_percent' in m.pool else 0}",
                             f(st.p50_ms, 2), f(st.p95_ms, 2), f(st.p99_ms, 2),
                             f(st.debezium_lag_ms, 0) if st.configuration == "C" else "-"])
            self.w(*table(["Config", "Repeat", "Target tps", "DB1 CPU %", "DB1 data IO %", "DB1 log write %", "DB1 workers %",
                           "Pool CPU %", "Pool data IO %", "Pool log write %", "Pool workers %", "Rows in window (DB1 / pool)",
                           "p50 ms", "p95 ms", "p99 ms", "Debezium ms behind source"], rows))
            rows = []
            for m in ms:
                st = m.step
                rows.append([st.configuration, st.repeat, f"{st.step_tps:,}", f(m.db1_log_bytes_s / MIB, 2),
                             f(m.log_flushed_bytes_s / MIB, 2), f(self.governed_log(m) / MIB, 2), f(m.per_txn(m.db1_log_bytes_s), 0),
                             f(m.per_txn(m.db1_data_write_bytes_s), 0), f(m.db1_data_iops, 0),
                             f(m.counter_seconds, 0) + ("" if m.counter_covers_window else " (partial)")])
            self.w("Absolute IO for database 1 (derived: deltas of cumulative counters over the polls that bracket the window). "
                   "Governed log rate is database 1's `avg_log_write_percent` times its recorded log rate limit.", "")
            self.w(*table(["Config", "Repeat", "Target tps", "Log written (MiB/s)", "Log bytes flushed counter (MiB/s)",
                           "Governed log rate (MiB/s)", "Log bytes per transaction", "Data bytes written per transaction", "Data IOPS",
                           "Counter interval (s)"], rows))

    def chart_section(self) -> None:
        self.w("## Charts (measured)", "")
        for caption, name in self.charts:
            self.w(f"![{caption}]({name})", "", f"*{caption}.*", "")

    def uplift_section(self) -> None:
        self.w("## Uplift (derived)", "",
               "C - B is CDC alone, B - A the outbox alone, C - A both. Only steps where both configurations carried their "
               "load in every repeat. Jitter is the spread across repeats when there are several; with one repeat it is "
               "the standard deviation of the samples inside the window (15 s resource rows, or the poll intervals of the "
               "cumulative counters). When the difference is not larger than the jitter, no uplift figure is given.", "")
        for run in self.runs:
            ms = self.run_metrics(run.run_id)
            rows = A.uplift_rows(ms)
            if not rows:
                self.w(f"Run {run.run_id}: no step was measured in two configurations; no uplift can be derived.", "")
                continue
            if (run.settings.get("repeats") or 1) == 1:
                self.w(f"Run {run.run_id} has one repeat: noise cannot be told from effect. Read every figure below as "
                       "one observation.", "")
            out = []
            for r in rows:
                if r["metric"] is None:
                    out.append([r["pair"], f"{r['step']:,}", "-", "-", "-", "-", "-", "-", r["repeats"],
                                "; ".join(x for x in (r["note"], r["scope"]) if x)])
                    continue
                u: A.Uplift = r["uplift"]
                d = 0 if "bytes" in r["unit"] and "%" not in r["unit"] else 2
                show = u.suppressed is None
                out.append([r["pair"], f"{r['step']:,}", f"{r['metric']} ({r['unit']})", f(r["x"].value, d), f(r["y"].value, d),
                            f(u.absolute, d) if show else "-", f(u.percent) + " %" if show else "-", f(u.jitter, d),
                            r["repeats"], "; ".join(x for x in (u.suppressed or "", r["scope"]) if x)])
            self.w(f"Run {run.run_id}:", "")
            if any(r["scope"] for r in rows):
                self.w("Pairs with A use only the repeats in which A ran (the quick run runs A in repeat 1 only, decision "
                       "0018), so their jitter is the within-window spread of one repeat. C - B uses every repeat.", "")
            self.w(*table(["Pair", "Step (tps)", "Metric", "First", "Second", "Difference", "Difference %", "Jitter",
                           "Repeats used", "Note"], out))

    def neighbour_section(self) -> None:
        self.w("## Neighbour impact: database 2 (measured)", "",
               "Database 2 runs a constant configuration B load in 10 s windows. A window counts for a step when it lies "
               "wholly inside the step's measured window. Latency is the mean of the windows' p50 and p99. The baseline "
               "is database 2 during the reset phases, when database 1 has no load generator running.", "")
        for run in self.runs:
            b = A.neighbour_baseline(run)
            ms = self.run_metrics(run.run_id)
            rows = [["baseline (reset)", "-", "-", b.get("n", 0), f(b.get("achieved", math.nan)), f(b.get("p50_ms", math.nan), 2),
                     f(b.get("p99_ms", math.nan), 2), f(b.get("p99_max_ms", math.nan), 2), f(b.get("errors_shed", math.nan), 0)]]
            for m in ms:
                n = m.neighbour
                g = lambda k, d=2: f(n.get(k, math.nan), d)
                rows.append([m.step.configuration, m.step.repeat, f"{m.step.step_tps:,}", n.get("n", 0), g("achieved", 1),
                             g("p50_ms"), g("p99_ms"), g("p99_max_ms"), g("errors_shed", 0)])
            self.w(f"Run {run.run_id}:", "")
            self.w(*table(["Database 1 config", "Repeat", "Database 1 target tps", "DB2 windows", "DB2 achieved tps",
                           "DB2 p50 ms", "DB2 p99 ms (mean)", "DB2 p99 ms (worst window)", "DB2 errors + shed"], rows))
        self.w("Small neighbour effects need a targeted pass with repeats before they are trusted (design section 11).", "")

    def purge_section(self) -> None:
        self.w("## Purge, CDC cleanup and catch-up", "")
        for run in self.runs:
            self.w(f"Run {run.run_id}:", "")
            rows = []
            for r in run.get("purge"):
                d = r.get("data") or {}
                secs, n = num(d.get("seconds")), num(d.get("rows_deleted"))
                lb, db = num(d.get("log_bytes_written")), num(d.get("data_bytes_written"))
                rows.append([r.get("configuration"), r.get("repeat"), f(n, 0), f(num(d.get("batches")), 0), f(num(d.get("batch_size")), 0),
                             f(secs), f(lb / MIB), f(db / MIB), f(n / secs if secs else math.nan, 0), f(lb / n if n else math.nan, 0),
                             f(lb / MIB / secs if secs else math.nan, 2)])
            if rows:
                self.w("Purge (measured; last three columns derived):", "")
                self.w(*table(["Config", "Repeat", "Rows deleted", "Batches", "Batch size", "Seconds", "Log written (MiB)",
                               "Data written (MiB)", "Rows per second", "Log bytes per row", "Log MiB/s"], rows))
            rows = [[r.get("configuration"), r.get("repeat"), f(num((r.get("data") or {}).get("added")), 0),
                     f(num((r.get("data") or {}).get("seconds")))] for r in run.get("purge_topup")]
            if rows:
                self.w("Outbox top-up before the purge (measured):", "")
                self.w(*table(["Config", "Repeat", "Rows added", "Seconds"], rows))
            rows = []
            for r in run.get("cdc_cleanup"):
                d = r.get("data") or {}
                b0, b1, secs = num(d.get("change_rows_before")), num(d.get("change_rows_after")), num(d.get("seconds"))
                lb = num(d.get("log_bytes_written"))
                removed = b0 - b1
                rows.append([r.get("repeat"), f(b0, 0), f(b1, 0), f(secs), f(lb / MIB), f(num(d.get("data_bytes_written")) / MIB),
                             f(removed / secs if secs else math.nan, 0), f(lb / removed if removed else math.nan, 0)])
            if rows:
                self.w("On-demand CDC cleanup (measured; last two columns derived). One on-demand cleanup, not the hourly "
                       "job under live load:", "")
                self.w(*table(["Repeat", "Change rows before", "Change rows after", "Seconds", "Log written (MiB)",
                               "Data written (MiB)", "Rows removed per second", "Log bytes per removed row"], rows))
            checks = A.catchup_checks(run, self.steps.get(run.run_id, []))
            if checks:
                rows = []
                for c in checks:
                    flag = "**capture behind: time understates catch-up**" if c["early"] else ""
                    rows.append([c["configuration"], c["repeat"], c["label"], f(c["seconds"], 2), f(c["change_rows"], 0),
                                 f(c["events_seen"], 0), f(c["expected_rows"], 0), flag])
                    if c["early"]:
                        self.data_notes.append(
                            f"Run {run.run_id}, catch-up '{c['label']}': the change table held {f(c['change_rows'], 0)} rows "
                            f"when catch-up was declared, but {f(c['expected_rows'], 0)} changes had been committed. "
                            "The orchestrator compares Debezium's events with the change table, not with committed "
                            "changes, so capture lag is not in the catch-up time.")
                self.w("Debezium catch-up (measured). Expected change rows (derived): committed outbox inserts in the "
                       "ladder, plus top-up rows, plus purged rows:", "")
                self.w(*table(["Config", "Repeat", "After", "Seconds", "Change table rows", "Debezium events seen",
                               "Expected change rows", "Check"], rows))

    def eventhubs_section(self) -> None:
        for run in self.runs:
            if not run.eventhubs:
                continue
            rows = []
            for m in run.eventhubs.get("value", []) if isinstance(run.eventhubs, dict) else []:
                total = sum(num(p.get("total")) for t in m.get("timeseries", []) for p in t.get("data", [])
                            if not math.isnan(num(p.get("total"))))
                rows.append([(m.get("name") or {}).get("value", "n/a"), m.get("unit", ""), f(total, 0)])
            if rows:
                self.w(f"## Event Hubs delivery (measured, Azure Monitor), run {run.run_id}", "",
                       "Totals over the metric query's time range (three hours before export), not only the run.", "")
                self.w(*table(["Metric", "Unit", "Total"], rows))

    def client_section(self) -> None:
        self.w("## Client-side proof (measured)", "",
               "Load generator process CPU as percent of all client cores. A client near saturation would limit the test, "
               "not the database.", "")
        for run in self.runs:
            ms = self.run_metrics(run.run_id)
            rows = [[m.step.configuration, m.step.repeat, f"{m.step.step_tps:,}", f(m.step.client_cpu_percent),
                     f(m.step.processor_count, 0)] for m in ms]
            if rows:
                self.w(f"Run {run.run_id}:", "")
                self.w(*table(["Config", "Repeat", "Target tps", "Client CPU %", "Client cores"], rows))

    def sizing_section(self) -> None:
        self.w("## Sizing: smallest zone-redundant General Purpose pool per load", "")
        costs = S.pick_costs(self.metrics, self.applied)
        loads = sorted({int(x) for r in self.runs for x in r.settings.get("steps", [])} | {m.step.step_tps for m in self.metrics})
        prices: dict[int, P.PoolPrice] = {}
        notes: list[str] = []
        if self.price_data:
            prices, notes = P.parse(self.price_data, [p.vcores for p in L.GP_GEN5_POOLS])
        self.w("A pool size a run used is **measured** for the loads that run stepped through. Every other size is an "
               "**EXTRAPOLATION**: database 1's measured per-transaction CPU, log and data IO use is scaled linearly with "
               "load, the rest of the pool's measured use (database 2 and internal work) is added unchanged, and the "
               f"total must stay below {A.THRESHOLD:g}% of each documented pool limit and of the recorded per-database "
               "limits. The per-database cap stays at the recorded value. Workers, sessions, latency and capture "
               "throughput are not modelled.", "")
        if costs:
            rows = []
            for c, cost in sorted(costs.items()):
                rows.append([c, cost.run_id, f"{cost.step_tps:,}", f(cost.per_txn_db["CPU"] * 1000, 3),
                             f(cost.per_txn_pool["CPU"] * 1000, 3), f(cost.per_txn_db["log write"], 0),
                             f(cost.per_txn_pool["log write"], 0), f(cost.per_txn_db["data IO"], 4), f(cost.per_txn_pool["data IO"], 4),
                             f(cost.other["CPU"], 3), f(cost.other["log write"] / MIB, 3), f(cost.other["data IO"], 1)])
            self.w("Cost basis (derived from the highest step that carried its load and reached no limit):", "")
            self.w(*table(["Config", "Run", "Step (tps)", "DB1 milli-vCores per tps", "Pool milli-vCores per tps",
                           "DB1 log bytes per transaction", "Pool log bytes per transaction", "DB1 IOPS per tps",
                           "Pool IOPS per tps", "DB2 vCores", "DB2 log MiB/s", "DB2 IOPS"], rows))
            self.w("All costs use the governed percentages (`avg_cpu_percent`, `avg_log_write_percent`, "
                   "`avg_data_io_percent`) times the recorded limits, because those are what the governor enforces. "
                   "Database level: database 1's use. Pool level: the pool's use minus database 2's, so work counted "
                   "only at pool level (internal processes, which per-database limits do not cover, for example CDC "
                   "capture) scales with load. Database 2's use is held constant.", "")
        else:
            self.w("No configuration has a step that carried its load without reaching a limit; no cost model.", "")
        configs = sorted({m.step.configuration for m in self.metrics})
        rows = []
        for load in loads:
            cells = {c: S.smallest(load, c, self.metrics, costs, self.applied) for c in configs}
            row = [f"{load:,}"] + [cells[c].label for c in configs]
            for c in ("B", "C"):
                if c in cells and cells[c].vcores in prices:
                    row.append(f(prices[cells[c].vcores].monthly, 2))
                elif c in configs:
                    row.append("n/a")
            if "B" in cells and "C" in cells and cells["B"].vcores in prices and cells["C"].vcores in prices:
                diff = prices[cells["C"].vcores].monthly - prices[cells["B"].vcores].monthly
                extra = not (cells["B"].measured and cells["C"].measured)
                row.append(f(diff, 2) + (" (EXTRAPOLATION)" if extra else " (measured sizes)"))
                self.compute_diff[load] = (diff, "EXTRAPOLATION" if extra else "measured sizes")
            elif "B" in configs and "C" in configs:
                row.append("n/a")
            rows.append(row)
        header = ["Load (tps)"] + [f"Config {c}" for c in configs]
        header += [f"Monthly price {c}" for c in ("B", "C") if c in configs]
        if "B" in configs and "C" in configs:
            header.append("CDC monthly difference (C - B)")
        self.w(f"Run IDs: {', '.join(r.run_id for r in self.runs)}.", "")
        self.w(*table(header, rows))
        if "B" not in configs or "C" not in configs:
            self.w("The monthly difference with and without CDC needs configurations B and C; these runs lack one.", "")
        if self.price_data:
            cur = next(iter(prices.values())).currency if prices else ""
            self.w(f"Prices: Azure Retail Prices API, queried {self.price_data.get('queried_at')}, region swedencentral, "
                   f"pay-as-you-go ('Consumption'), product '{P.PRODUCT}', meters 'vCore' plus 'Zone Redundancy vCore', "
                   f"currency {cur}. Monthly = hourly x {P.HOURS_PER_MONTH} hours (assumption). Compute and zone "
                   "redundancy only: licence discounts are excluded, and storage and backup storage are in the next "
                   "section. Query: "
                   f"`{self.price_data.get('query')}`.", "")
            rows = [[p.vcores, f(p.compute_hourly, 4), f(p.zone_redundancy_hourly, 4), f(p.hourly, 4), f(p.monthly, 2)]
                    for p in sorted(prices.values(), key=lambda p: p.vcores)]
            self.w(*table(["Pool vCores", "Compute per hour", "Zone redundancy per hour", "Total per hour", "Monthly (730 h)"], rows))
            for n in notes:
                self.w(f"- {n}")
            self.w("")
        else:
            self.w(f"Prices were not read: {self.price_error or 'no price source'}. No price is given.", "")

    def storage_section(self) -> None:
        R = ST
        self.w("## Storage and backup cost of CDC", "",
               "CDC costs storage in two ways. Each captured outbox insert becomes a row in the change table "
               f"`{R.CT_SCHEMA}.{R.CT_TABLE}`, kept until the CDC cleanup job's retention expires. And configuration C "
               "writes more transaction log per transaction than B; log backups carry that log, and point-in-time "
               "restore (PITR) keeps them. Figures below are **derived** (arithmetic on measured counters) unless "
               "labelled **EXTRAPOLATION** (sustained load beyond the measured windows). Microsoft Learn facts were "
               "checked on 2026-11-22 (`docs/verification/2026-11-22-report-limits.md`).", "")
        sp = P.parse_storage(self.price_data) if self.price_data else None
        backup_price, backup_note = R.price("backup", sp.backup_zrs if sp else None)
        data_price, data_note = R.price("data storage", sp.data_zone_redundancy if sp else None)
        if not self.price_data:
            backup_note = data_note = f"not priced: prices were not read ({self.price_error or 'no price source'})"
        retention_s = R.CDC_RETENTION_MINUTES * 60
        days_lo, days_hi = R.PITR_RETENTION_DAYS, R.PITR_RETENTION_DAYS + R.PITR_EXTRA_DAYS
        totals: dict[int, dict] = {}

        self.w("### Change-table storage (derived)", "",
               f"Bytes per change row: growth of `reserved_bytes` divided by growth of `row_count` for "
               f"`{R.CT_SCHEMA}.{R.CT_TABLE}` across each configuration C step window (`table_sizes.jsonl`, the polls that "
               "bracket the window). Capture lag does not bias the ratio: both counts cover the rows capture wrote in the "
               "window. Retained size = bytes per row x step load x retention, assuming one change row per transaction "
               "(one outbox insert; `supports_net_changes = 0`). The table compares change rows written per second with "
               f"achieved tps as a check. Retention: the cleanup job's default, {R.CDC_RETENTION_MINUTES:,} minutes "
               "(three days; Microsoft Learn). The harness lowers retention to 1 minute in its cleanup phase, after the "
               "load steps (check `cdc_change_job_retention` records whether that worked); production keeps the default "
               "unless someone changes it. `cdc.lsn_time_mapping` also grows and is not counted.", "")
        for run in self.runs:
            items = R.change_rows(run, self.steps.get(run.run_id, []))
            if not items:
                self.w(f"Run {run.run_id}: no configuration C step; no change-table figure.", "")
                continue
            per_step = R.bytes_per_row(items)
            rows = []
            for t, rng in per_step.items():
                its = [c for c in items if c.step.step_tps == t]
                rps = R.Range.of([c.rows_per_s for c in its])
                ach = R.Range.of([c.step.achieved for c in its])
                ret = R.retained_bytes(rng.mean, t, retention_s) / R.GB
                ret_lo = R.retained_bytes(rng.low, t, retention_s) / R.GB
                ret_hi = R.retained_bytes(rng.high, t, retention_s) / R.GB
                row = [f"{t:,}", rng.n, f(rng.mean, 0), f"{f(rng.low, 0)} to {f(rng.high, 0)}", f(rps.mean, 1), f(ach.mean, 1),
                       f(ret, 2), f"{f(ret_lo, 2)} to {f(ret_hi, 2)}"]
                if self.duty_hours:
                    row.append(f(ret * self.duty_hours / 24, 2))
                rows.append(row)
                totals.setdefault(t, {})["ct_gb"] = ret
            header = ["Step (tps)", "Repeats", "Bytes per change row (mean)", "Range across repeats",
                      "Change rows written per s (mean)", "Achieved tps (mean)",
                      f"Retained at {R.CDC_RETENTION_MINUTES:,} min (GB, mean)", "Retained range (GB)"]
            if self.duty_hours:
                header.append(f"Retained at {f(self.duty_hours, 1)} h/day of this load (GB, assumption)")
            self.w(f"Run {run.run_id}:", "")
            self.w(*table(header, rows))
        self.w(f"Data storage cost of the change table: {data_note}. Separately, General Purpose pool data storage is "
               "billed on the pool's configured max data size, not on space used (Microsoft Learn): the change table costs "
               "money only when the max data size must be raised to hold it.", "")

        self.w("### Extra transaction log (derived)", "",
               f"Log bytes per transaction: database 1's LOG file `num_of_bytes_written` delta over the polls that bracket "
               "each measured window, divided by achieved tps (as in the per-step table). A counter that goes down inside "
               "a window (failover or restart) gives no value for that repeat. C - B is the mean over every repeat in which "
               "both ran, with the uplift rule: no figure when the difference is not larger than the spread across "
               "repeats (the jitter). The paired column is C - B within each repeat. GB per day = C - B bytes per "
               "transaction x step load x 86,400 s, with GB = 10^9 bytes.", "")
        any_pair = False
        for run in self.runs:
            diffs = R.log_diffs(self.run_metrics(run.run_id))
            if not diffs:
                self.w(f"Run {run.run_id}: needs configurations B and C at the same step; this run lacks one. No extra-log "
                       "figure.", "")
                continue
            any_pair = True
            rows = []
            for d in diffs:
                u = d.uplift
                pr = d.paired_range
                show = u.suppressed is None
                gbd = R.log_gb_per_day(u.absolute, d.step_tps) if show else math.nan
                row = [f"{d.step_tps:,}", ", ".join(map(str, d.repeats)) or "-", f(d.b.value, 0), f(d.c.value, 0),
                       f(u.absolute, 0) if show else "-", f(u.jitter, 0),
                       f"{f(pr.low, 0)} to {f(pr.high, 0)}" if pr.n else "-", f(gbd, 2) if show else "-",
                       f(gbd * R.DAYS_PER_MONTH, 1) if show else "-"]
                if self.duty_hours:
                    row.append(f(R.log_gb_per_day(u.absolute, d.step_tps, self.duty_hours), 2) if show else "-")
                note = "; ".join(x for x in (d.note, u.suppressed or "") if x)
                if len(d.repeats) == 1 and show:
                    note = "; ".join(x for x in (note, "one repeat: noise cannot be told from effect") if x)
                rows.append(row + [note])
                if show and "log_gb_day" not in totals.get(d.step_tps, {}):  # the first run listed wins
                    totals.setdefault(d.step_tps, {})["log_gb_day"] = gbd
                    totals[d.step_tps]["log_run"] = run.run_id
            header = ["Step (tps)", "Repeats used", "B log bytes per txn", "C log bytes per txn", "C - B", "Jitter",
                      "Paired C - B per repeat (min to max)", "Extra log at step load (GB/day)",
                      f"Extra log ({R.DAYS_PER_MONTH}-day month, GB)"]
            if self.duty_hours:
                header.append(f"Extra log at {f(self.duty_hours, 1)} h/day (GB/day, assumption)")
            self.w(f"Run {run.run_id}:", "")
            self.w(*table(header + ["Note"], rows))

        self.w("### Backup storage (EXTRAPOLATION)", "",
               "Billing rules (Microsoft Learn, verified 2026-11-22):", "",
               f"- PITR keeps {R.PITR_RETENTION_DAYS} days by default. Azure keeps full, differential and log backups for up "
               f"to a week longer, because it purges them in weekly sets. So each day's log backups are held between "
               f"{days_lo} and {days_hi} days.",
               "- Log backups are taken about every 10 minutes. Each log backup holds all log records not in a previous log "
               "backup. Log backups of TDE-encrypted databases are not compressed; TDE is on by default for new databases. "
               "So log backup bytes are taken to equal log bytes written (inference from those facts; the log file counter "
               "may include some log the backup does not).",
               "- For an elastic pool, backup storage up to the pool's max data storage is free. Billable backup storage is "
               "the total of all full, differential and log backups of the pooled databases minus the pool's max data "
               "storage, charged per GB-month at the backup storage redundancy's rate. A steady amount stored all month "
               "bills as that many GB-months.",
               "- The databases use zone-redundant backup storage (`storage_account_type = \"Zone\"`), so the rate is the "
               f"API's '{P.BACKUP_METER}' meter, product '{P.BACKUP_PRODUCT}'.", "",
               "The pool's total backup size is not measured, so whether the extra log falls inside the free allowance is "
               "unknown. The cost below is the upper bound: every extra GB billed. If the pool's backups stay under the "
               "allowance, CDC's extra log backups cost 0. CDC also enlarges full and differential backups by the change "
               "table (compressed); that is not modelled.", "")
        if backup_price is None:
            self.w(f"Backup storage: {backup_note}.", "")
        rows = []
        for t in sorted(totals):
            g = totals[t].get("log_gb_day")
            if g is None:
                continue
            lo, hi = R.backup_gb(g, days_lo), R.backup_gb(g, days_hi)
            cost_lo = lo * backup_price if backup_price is not None else math.nan
            cost_hi = hi * backup_price if backup_price is not None else math.nan
            totals[t]["backup_hi"] = cost_hi
            rows.append([f"{t:,}", totals[t]["log_run"], f(g, 2), f"{f(lo, 1)} to {f(hi, 1)}",
                         f"{f(cost_lo, 2)} to {f(cost_hi, 2)}" if backup_price is not None else backup_note])
        if rows:
            cur = sp.backup_zrs.currency if sp and sp.backup_zrs else ""
            self.w(*table(["Load (tps)", "Run", "Extra log (GB/day)", f"Extra log backups held, {days_lo} to {days_hi} days (GB)",
                           f"Monthly cost if above the free allowance ({cur})"], rows))
            if backup_price is not None:
                self.w(f"Rate: {f(backup_price, 4)} {cur} per GB-month, meter '{P.BACKUP_METER}', SKU '{sp.backup_zrs.sku}', "
                       f"effective {sp.backup_zrs.effective}, queried {self.price_data.get('queried_at')}.", "")
        elif not any_pair:
            self.w("No extra-log figure, so no backup figure.", "")
        else:
            self.w("No step has an extra-log figure larger than its jitter, so no backup figure.", "")

        self.w("### Monthly cost difference with CDC, C - B (EXTRAPOLATION)", "",
               "Compute difference from the sizing table, plus change-table data storage, plus the backup upper bound. "
               "Assumptions:", "",
               "- The step load runs 24 hours a day, every day (an upper bound for a workload that is busy only part of "
               "the day).",
               f"- {P.HOURS_PER_MONTH} hours per month for compute; {R.DAYS_PER_MONTH}-day month where log is summed per month; "
               "backup storage held steadily, so GB held equals GB-months billed.",
               f"- CDC retention {R.CDC_RETENTION_MINUTES:,} minutes; PITR retention {R.PITR_RETENTION_DAYS} days, each day's "
               f"log held up to {days_hi} days (upper bound).",
               "- Backup: every extra GB billed (upper bound; 0 inside the free allowance).",
               f"- Change-table data storage: {data_note}. The total excludes it.",
               "- Pay-as-you-go retail prices, no reservations or licence discounts.")
        if self.duty_hours:
            self.w(f"- Reader's assumption, change it with `--duty-hours`: the step load runs {f(self.duty_hours, 1)} hours a "
                   "day. That scales log and change rows only; compute is provisioned all day.")
        self.w("")
        rows = []
        for t in sorted(set(totals) | set(self.compute_diff)):
            comp, label = self.compute_diff.get(t, (math.nan, "n/a"))
            bk = totals.get(t, {}).get("backup_hi", math.nan)
            total = comp + bk if not (math.isnan(comp) or math.isnan(bk)) else math.nan
            row = [f"{t:,}", f(comp, 2) + (f" ({label})" if label != "n/a" else ""), "not priced", f(bk, 2), f(total, 2)]
            if self.duty_hours:
                frac = self.duty_hours / 24
                row.append(f(comp + bk * frac, 2) if not math.isnan(total) else "n/a")
            rows.append(row)
        if rows:
            header = ["Load (tps)", "Compute C - B per month", "Change-table storage", "Backup per month (upper bound)",
                      "Total per month (upper bound)"]
            if self.duty_hours:
                header.append(f"Total at {f(self.duty_hours, 1)} h/day (assumption)")
            self.w(f"Runs: {', '.join(r.run_id for r in self.runs)}.", "")
            self.w(*table(header, rows))
            self.w("n/a: a part is missing (no B and C sizes with prices, or no extra-log figure larger than its jitter). "
                   "A total is given only when both compute and backup are.", "")
        else:
            self.w("No load has both a compute difference and a backup figure; no monthly total.", "")
        if sp:
            ref = [[m.product, m.meter, m.sku, f(m.per_gb_month, 4), m.currency, m.effective]
                   for m in (sp.backup_zrs, sp.data, sp.data_zone_redundancy) if m]
            if ref:
                self.w("Storage meters read (Azure Retail Prices API, region swedencentral, 'Consumption'; only the backup "
                       f"meter is used). Query: `{self.price_data.get('storage_query')}`.", "")
                self.w(*table(["Product", "Meter", "SKU", "Per GB-month", "Currency", "Effective"], ref))

    def checks_section(self) -> None:
        self.w("## Data checks", "")
        notes = list(self.data_notes)
        for run in self.runs:
            m = run.manifest
            if m is None:
                notes.append(f"Run {run.run_id}: no manifest.json; the rig may not have finished.")
            elif m.get("status") != "succeeded":
                notes.append(f"Run {run.run_id}: manifest status {m.get('status')}.")
            for name, c in ((m or {}).get("checks") or {}).items():
                if not (c or {}).get("ok"):
                    notes.append(f"Run {run.run_id}: check {name} failed.")
            notes += [f"Run {run.run_id}: {w}" for w in run.warnings]
            a = self.applied.get(run.run_id)
            for r in run.get("purge"):
                d = r.get("data") or {}
                secs, lb = num(d.get("seconds")), num(d.get("log_bytes_written"))
                if a and secs and not math.isnan(lb) and lb / secs > a.db_log_rate:
                    notes.append(f"Run {run.run_id}, purge {r.get('configuration')}: log file writes averaged "
                                 f"{f(lb / secs / MIB, 2)} MiB/s, above database 1's recorded log rate limit of "
                                 f"{f(a.db_log_rate / MIB, 2)} MiB/s. The file counter includes log the governor does not "
                                 "count against the database; in C that includes capture of earlier changes still pending "
                                 "(see catch-up), so log bytes per purged row overstate the purge alone.")
            for x in self.run_metrics(run.run_id):
                st = x.step
                tag = f"Run {run.run_id}, {st.configuration} {st.step_tps:,} tps repeat {st.repeat}"
                pending = st.scheduled - st.completed - st.errors - st.shed
                if not math.isnan(pending) and pending != 0:
                    notes.append(f"{tag}: {f(pending, 0)} scheduled transactions had not completed when the window closed.")
                n1 = x.db1.get("avg_cpu_percent").n if "avg_cpu_percent" in x.db1 else 0
                if n1 < 2:
                    notes.append(f"{tag}: only {n1} database 1 resource rows lie inside the window.")
                if not x.counter_covers_window:
                    notes.append(f"{tag}: cumulative counters do not bracket the window; absolute IO covers part of it.")
                if st.configuration == "C" and math.isnan(st.debezium_lag_ms):
                    notes.append(f"{tag}: no Debezium MilliSecondsBehindSource reading.")
                gov = self.governed_log(x)
                if gov and not math.isnan(gov) and not math.isnan(x.db1_log_bytes_s) and abs(x.db1_log_bytes_s / gov - 1) > 0.25:
                    notes.append(f"{tag}: database 1 wrote {f(x.db1_log_bytes_s / MIB, 2)} MiB/s of log (file stats) but its "
                                 f"governed log rate was {f(gov / MIB, 2)} MiB/s. Per-database limits cover user workloads, not "
                                 "internal processes (documented); internal log such as CDC capture is a likely cause "
                                 "(inference).")
                if x.binding.reached and x.binding.confirmed is False:
                    notes.append(f"{tag}: {x.binding.label} at {f(x.binding.percent)}% but its wait types show no wait time.")
        if not notes:
            notes = ["No problems found."]
        self.w(*[f"- {n}" for n in notes], "")

    def limitations(self) -> None:
        most = max((r.settings.get("repeats") or 1 for r in self.runs), default=1)
        repeats = ("- **One repeat**: noise cannot be told from effect. Large effects will be clear; small ones, such as "
                   "neighbour impact, need a targeted pass with repeats." if most == 1 else
                   f"- **{most} repeats at most**: spread across so few repeats is a rough guide, not a confidence interval. "
                   "Configuration A ran in fewer repeats than B and C (decision 0018), so pairs with A rest on one repeat.")
        self.w("## Limitations", "", repeats,
               "- **Coarse limit location**: the load at which a limit is reached is known only to within the gap between "
               "steps. If that bracket spans a pool-size boundary, run a targeted pass.",
               "- **Made-up load numbers**: the production-shaped profile is a guess; read its position off the ladder.",
               "- **One payload size**: no curve for payload growth.",
               "- **Read IO understated**: the seeded data is smaller than pool memory, so few physical reads occur.",
               "- **Backup storage is not measured**: backup billing metrics update too slowly for a run of hours. The "
               "backup cost is derived from log written, an upper bound that ignores the free allowance.",
               "- **No retry amplification**: the load generator calls SQL directly, without an API's retries.",
               "- **Platform noise**: General Purpose uses remote storage on a shared platform.",
               "- **Sizing beyond the measured pool sizes is EXTRAPOLATION** from documented limits, linear in load, with "
               "the per-database cap held at the recorded value.",
               "- **Capture may not scale with vCores**: the CDC capture cycle is fixed at 20 seconds. If capture "
               "throughput is the bottleneck, a bigger pool may not help; the model above cannot show that.",
               "- **Pool percentages**: Microsoft documents that pool CPU, data IO and log write utilisation can be less "
               "than the sum across its databases. The model's \"rest of pool\" term is floored at zero for that reason.",
               "- **Debezium lag** is one reading at the end of each step, not a trend.",
               "")

    def build(self) -> str:
        self.header()
        self.answer()
        self.runs_table()
        self.per_step()
        self.chart_section()
        self.uplift_section()
        self.neighbour_section()
        self.purge_section()
        self.eventhubs_section()
        self.client_section()
        self.sizing_section()
        self.storage_section()
        self.applied_table()
        self.checks_section()
        self.limitations()
        return "\n".join(self.lines).rstrip() + "\n"

# 0016. Report generator rules

Date: 2026-11-22
Decided by: the agent, under the autonomy model (decision 0001). Design section
9 fixes what the report contains; it leaves the rules below open.

## Context

The report turns raw run files into the answers in design section 1. Several
rules decide what a number means: when a step "carries" its load, when a limit
counts as reached, when a configuration difference is real, and how far the
sizing table may go beyond what was measured. With one repeat (the quick run),
these rules carry the whole burden of not publishing noise as a result.

## Decision

- **Step window.** A step's window is `measure_start` to `measure_end` from
  the load generator. Resource-stats rows count only when their whole interval
  lies inside it. Cumulative counters (file IO, waits, performance counters)
  use the last poll at or before the start and the first poll at or after the
  end; the report marks a step when the polls do not bracket the window.
- **Carries.** Achieved rate at least 95% of target, and errors plus shed equal
  to 0. Same ratio as the orchestrator's stop rule, plus the error condition.
- **Binding limit.** The highest mean percent of limit in the window among CPU,
  data IO, log write, workers and sessions, at database 1 and pool level.
  Reached at 90% or more; below that, "none reached". Memory and storage are
  excluded: a full buffer pool does not throttle.
- **Confirmation.** A reached limit is confirmed when a matching wait type
  accumulated wait time in the window: `SOS_SCHEDULER_YIELD` (CPU),
  `PAGEIOLATCH_*` (data IO), `LOG_RATE_GOVERNOR` (database log) or
  `POOL_LOG_RATE_GOVERNOR` and `INSTANCE_LOG_RATE_GOVERNOR` (pool log),
  `THREADPOOL` (workers). The log wait types are documented (verification
  file); the CPU, IO and worker mappings are the conventional reading of those
  wait types, not a Microsoft Learn claim. The report prints the wait rate so
  the reader can judge.
- **Uplift and jitter.** Uplift is given only at steps where both
  configurations carried their load in every repeat. Jitter is the spread
  across repeats when there are several, else the standard deviation of the
  samples inside the window. No figure is given when the difference is not
  larger than the jitter, or when the jitter is unknown.
- **Sizing.** A pool size a run used is measured. Every other size is an
  EXTRAPOLATION: database 1's governed per-transaction use scales linearly with
  load; the pool's governed use minus database 2's also scales with load (so
  internal work such as CDC capture, which per-database limits do not cover, is
  counted); database 2's use is constant. A size carries in the model when all
  of CPU, log write and data IO stay below 90% of the documented pool limit and
  of the recorded per-database limit. The per-database cap stays at the
  recorded value because no document gives the limits for other caps. A load
  that was not carried with no limit reached gets no size.
- **Prices.** Read at report time from the Azure Retail Prices API; monthly is
  hourly x 730. A second query reads the storage meters (below).
- **Repeats with A (added 2026-11-22, decision 0018).** A pair uses only the
  repeats both configurations ran. The quick run has A in repeat 1 only, so
  B - A and C - A rest on repeat 1 (jitter: within-window spread) and the
  table says so; C - B uses all three repeats (jitter: spread across repeats).
- **Change-table storage (added 2026-11-22).** Bytes per change row is the
  growth of `reserved_bytes` over the growth of `row_count` for
  `cdc.dbo_Outbox_CT` between the polls that bracket each C window. A table
  that shrank in the window gives no figure. Retained size = bytes per row x
  step load x 4,320 minutes (the documented default retention), assuming one
  change row per transaction. Derived; the harness lowers retention only in
  its cleanup phase.
- **Extra log (added 2026-11-22).** C - B log bytes per transaction from the
  LOG file counter, over every shared repeat, with the uplift suppression
  rule. A counter reset drops that repeat and the report names it. GB per day
  = difference x step load x 86,400 s, GB = 10^9 bytes (decimal gives the
  larger count, so the higher cost).
- **Backup cost (added 2026-11-22).** EXTRAPOLATION. Log backup bytes are
  taken to equal log written (inference: log backups of TDE databases are not
  compressed). Each day's log is held 7 to 14 days (7-day PITR plus up to a
  week for weekly purge sets). Priced with the `ZRS Data Stored` PITR meter as
  an upper bound: every extra GB billed. The free allowance (backups up to the
  pool's max data size) can make it 0, and no run measures the pool's total
  backup size.
- **Data storage (added 2026-11-22).** Not priced: which data storage meter a
  zone-redundant General Purpose pool pays is not documented. The report says
  "not priced: unverified". A billing rule that is not verified is never
  priced (`report/storage.py`, `UNVERIFIED`).
- **Monthly CDC difference (added 2026-11-22).** EXTRAPOLATION: compute C - B
  from the sizing table plus the backup upper bound, load sustained 24 hours a
  day. `--duty-hours H` adds a column for a reader-chosen duty cycle; it scales
  log and change rows, not compute.
- **Plain-language summary (added 2026-11-22).** The CLI also writes
  `SUMMARY.md`, linked from the top of `README.md`, for a reader with no
  context; `README.md` stays the technical appendix. It avoids the
  appendix's vocabulary: "the application database" and "the neighbouring
  database", "without CDC" (B) and "with CDC" (C), "round" for a repeat,
  "run-to-run variation" for jitter. Configuration A is not shown. One table
  per run, per load: handled or not, log per update, CPU and log write rate
  (percent of the application database's limit), p95 response time, the
  neighbouring database's p95, change-table size at the default retention,
  and the extra monthly cost. It computes nothing new: means over shared
  rounds (`analysis.paired`, `config_value`), the uplift rule
  (`analysis.uplift`; "within run-to-run variation"), the per-round range in
  brackets (`analysis.paired_diffs`, shared with the extra-log table), the
  change-table retention arithmetic, and the monthly total the README's
  storage section computed (`render.Report.monthly_total`). The opening
  paragraph takes its numbers from the run's settings, governance,
  environment and seed records. One departure from the uplift table: a load
  that one side did not handle still shows its difference, noted "overloaded:
  compares two overloaded systems, not the steady cost of CDC", because the
  owner asked to see where the system stops coping. The "added cost" bullet
  uses only the highest load both sides handled in every shared round. Any
  missing value says "not measured".

## Alternatives rejected

- Peak percent instead of mean for the binding limit: one 15 s spike would name
  a binding limit for a step that carried its load comfortably.
- Pooled standard error as the jitter test: with one repeat its samples are
  not independent (overlapping 15 s averages), so it would overstate certainty.
- Sizing from file-level log bytes: the governor does not count all of them
  (spike run 45054291170-1 wrote 2.61 MiB/s to the log file while database 1's
  governed log rate was 1.41 MiB/s). Governed percentages are what binds.
- Scaling the per-database limits with a raised cap, using the single-database
  tables: not documented for pooled databases.
- Pricing data storage with the base meter alone, or base plus zone
  redundancy meter: either is a guess about an undocumented billing rule.
- Netting the free backup allowance: needs the pool's total backup size,
  which a run of hours cannot measure.
- Pooling B's three repeats against A's one: mixes blocks (decision 0018
  blocks by repeat).

## How it was verified

- Unit tests in `tests/report/` on hand-written synthetic runs: window
  matching, carries, binding rule and confirmation, uplift suppression,
  sizing labels (measured, extrapolated, cap-bound, unexplained failure),
  price parsing, catch-up check, end to end through the CLI. Added 2026-11-22:
  bytes per change row, retention arithmetic, C - B log over three repeats with
  a counter reset, pairs with A on repeat 1 only, storage meter parsing, the
  "not priced: unverified" path (44 tests). Added 2026-11-22: `SUMMARY.md` on a
  quick-run shape with an overloaded 500 tps step, the within-variation path,
  missing figures, a banned-word check and agreement with `README.md`
  (47 tests).
- Smoke run of the storage section against smoke run 45060216523-1 (C only),
  output kept outside the repository.
- Smoke run of the CLI against spike run 45054291170-1, output kept outside
  the repository.
- Limits: `docs/verification/2026-11-22-report-limits.md`.

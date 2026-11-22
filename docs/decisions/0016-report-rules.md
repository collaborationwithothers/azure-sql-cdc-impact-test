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
  hourly x 730. Storage and backup are not priced.

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

## How it was verified

- Unit tests in `tests/report/` on hand-written synthetic runs: window
  matching, carries, binding rule and confirmation, uplift suppression,
  sizing labels (measured, extrapolated, cap-bound, unexplained failure),
  price parsing, catch-up check, end to end through the CLI.
- Smoke run of the CLI against spike run 45054291170-1, output kept outside
  the repository.
- Limits: `docs/verification/2026-11-22-report-limits.md`.

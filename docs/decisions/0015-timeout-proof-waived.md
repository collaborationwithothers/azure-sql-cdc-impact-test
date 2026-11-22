# 0015. Destroy-on-timeout proof waived for the first results

Date: 2026-11-22
Decided by: the owner, who needs results the same day ("23:00 is fine",
"no cuts then", "we are not going to keep the infra around for a month").

## Context

AGENTS.md requires destroy to be proved on success, on failure and on timeout
before the first run longer than ten minutes. On the day of the first quick
run:
- Success: proved by spike runs 45054291170 and 45056601736 (destroy, then
  `verify-clean`, both passed).
- Failure: run 45058781127 (`inject=fail`).
- Timeout: not run. It would add about 20 minutes before the quick run.

## Decision

- The timeout proof is waived for the smoke, quick and confirming runs of
  2026-11-22. It is run afterwards.
- No other cut: smoke runs, and the seed stays at 10 GB per database
  (design decision D5).
- After the confirming run the persistent Container Apps environment is
  removed (decision 0013), so nothing billed by the hour remains.

## What still protects against a leftover

- Every ephemeral resource carries a `deadline` tag (apply time plus three
  hours); `sweep` runs every 30 minutes and destroys anything past it.
- The `destroy` job runs on `always()`, including after cancellation.
- Each run's destroy ends with `verify-clean`; a leftover after destroy and
  sweep is stop condition 4.

## Risk accepted

If a run hits its job timeout and the destroy job also fails, resources live
until the deadline plus up to 30 minutes. At current prices that is a few USD.

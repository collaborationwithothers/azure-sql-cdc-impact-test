# 0014. Spike results (design section 12)

Date: 2026-11-22
Evidence: run 45054291170 (spike mode, 4 vCore pool, per-database cap 2).
Raw files: results container, prefix `45054291170-1/`. The workflow's export
step failed (see "Pipeline faults"), so there is no artifact for this run; the
files were read from the results container.

The spike proves mechanics only. 100 transactions per second binds no limit.
None of its numbers is a result.

## Items

| # | Item | Outcome | Evidence |
| --- | --- | --- | --- |
| 1 | Debezium signs in to Azure SQL with the managed identity | Passed | `debezium_started` (11 s); Debezium metrics: 8841 events seen, 0 erroneous; `cdc_marker_captured`. No SQL login exists. |
| 1b | Debezium publishes to Event Hubs with an Entra token (decision 0007) | Passed, with one gap | Debezium log: OAUTHBEARER "Successfully logged in", "4465 records sent during previous 00:00:40", engine "completed normally", exit code 0. Gap: the namespace's metrics in Log Analytics show only 2 incoming requests and no message counts, so delivery is shown by Debezium's own log, not by Event Hubs. Run 45056601736 showed the same. The export step now reads the metrics from Azure Monitor before destroy. |
| 2 | The creating principal can enable CDC | Passed | `cdc_enable`. The rig identity is the Entra admin (decision 0004). |
| 3 | Debezium lag is readable from its metrics | Passed | `debezium_metrics_readable`: 43 series, including `MilliSecondsBehindSource` (420 at the end of the step). |
| 4 | Azure Policy does not block any ephemeral resource | Passed | Apply: 14 added, 0 failed. |
| 5 | Quota for D8 and the pool in Sweden Central | Passed | Apply succeeded; the job got a node. |
| 6 | Pool-level wait statistics are readable | Passed | `dmv_os_wait_stats` (19 rows) as the Entra admin. The `##MS_ServerStateReader##` role was not needed. `governance_cdcdb1` and `governance_cdcdb2` returned rows. |
| 7 | Environment creation time | Answered | 5m50s create, 24m18s delete (run 45049809770). Environment made persistent (decision 0013). From zero nodes, job start to the rig's first record took 1m54s. |
| 8 | Debezium placement (decision 0010) | Passed | Both containers ran on one D8 node; marker-file start, `debezium_alive_at_end_r1`, clean stop. |

Also shown: `sp_cdc_change_job` works on Azure SQL Database
(`cdc_change_job_retention`), on-demand CDC cleanup removes change rows
(`cdc_cleanup_on_demand`), and Debezium caught up after the ladder, top-up and
purge phases (`catchup_*`). Correction: those checks compared Debezium with
the change table before CDC capture had finished (8,841 change rows against
9,000 commits), so the spike catch-up times are not valid. Fixed with a
marker row in `wait_for_catchup`. The step itself: 6000 of 6000 transactions, 0
errors, 0 shed.

## Open from the spike

- Debezium telemetry did not reach Application Insights. The agent loaded and
  registered the Debezium JMX metrics, but Log Analytics has no rows from a
  Debezium role. The load generator and orchestrator rows did arrive. Decision
  0011 says: Java agent first, then the orchestrator forwards the metrics it
  scrapes. The lag figures the report needs come from the orchestrator's scrape
  either way, so this does not block the measurement runs.
- `dmv_table_sizes` returned 0 rows at setup, before the schema exists. Not a
  fault; the collector's later polls cover it.

## Pipeline faults found

- The GitHub OIDC token that `azure/login` exchanges lasts five minutes. The CLI
  reuses it for every new resource token, so the export step, eleven minutes
  after login, failed with AADSTS700024. Fix: `scripts/ephemeral.sh relogin`
  requests a fresh token from GitHub and signs in again. It is called before
  the export, every four minutes while waiting for the job, and inside
  destroy and verify-clean.
- The job-status loop treated an unreadable status as "still running", so an
  expired sign-in would have looped until the workflow timeout. It now fails
  after ten minutes of unreadable status.
- `awk ... {exit}` closed the pipe early and `pipefail` turned that into exit
  code 141. The awk now reads to the end.

# Progress log

The working task list for building `docs/design.md`. A new or summarised agent
session resumes from here. AGENTS.md "Current state" holds a short summary and
points to this file. Decisions go in `docs/decisions/`, verified facts in
`docs/verification/`. This file holds only status and next steps.

Rules for this file:
- Update it in every pull request that changes status.
- Work in progress lives on an `agent/` branch. If a task below says
  "in progress", check open pull requests and `agent/` branches first.
- No identifiers, no secrets.

## Build order (design section 13)

| # | Step | Status |
| --- | --- | --- |
| 1 | Repo, `validate` workflow, persistent Terraform, bootstrap instructions | Done, PR #1 |
| 1b | Bootstrap apply of the persistent layer, environment secrets | Done 2026-11-22 (decision 0006). OIDC sign-in proof via `destroy` workflow pending |
| 2 | Spike: ephemeral layer plus one configuration C step (section 12 items 1 to 8) | In progress, branch `agent/rig`. Restructuring to decision 0010 |
| 3 | Load generator, collector, orchestrator, smoke mode | Not started |
| 4 | `run-test`, `sweep`, `destroy`. Prove destroy on success, failure, timeout | Not started |
| 5 | Report generator and sizing translation | In progress, branch `agent/report-generator` (decision 0016). Unit tests pass; smoke-tested on spike run 45054291170-1 |
| 6 | Quick run at 4 vCores, review, publish report on `results` branch | Not started |
| 7 | Confirming run at 6 vCores | Not started |

## Step 1 checklist

- [x] Setup checks (remote, GitHub admin, `azure` environment main-only, Azure
      Owner, policy assignments, quotas). 2026-11-22.
- [x] Verification of Azure SQL claims: `docs/verification/2026-11-22-azure-sql.md`.
- [x] Verification of platform claims: `docs/verification/2026-11-22-platform.md`.
- [x] Verification of Debezium, mssql-jdbc, Terraform provider and action pins:
      `docs/verification/2026-11-22-debezium-terraform.md`.
- [x] Persistent Terraform drafted (`infra/persistent/`).
- [x] Pin provider versions, `terraform fmt` and `validate`.
- [x] `validate` workflow with secret scan (compensating control for disabled
      push protection).
- [x] `docs/bootstrap.md` and `scripts/bootstrap.sh`.
- [x] AGENTS.md Bootstrap section: environment secrets need repo admin.
- [x] Decision records 0002 to 0005.
- [x] PR opened, CI green, merged (#1).

## Step 2 status (2026-11-22)

Owner decisions this step: 0008 (no VNet), 0010 (component split), 0011
(Application Insights, full tracing), 0012 (diagnostics everywhere).

Done (branch `agent/rig-v2`, superseding `agent/rig`):
- [x] Persistent: Application Insights, telemetry role, diagnostic settings
      (PR #4, applied). Plan check fixed: it had never run.
- [x] .NET load generator CLI (`window`, `steady`) with OpenTelemetry. Unit 9/9,
      container 6/6, including SQL tracing with SqlClient 7.1.2.
- [x] Java token jar; self-test passes in the image build.
- [x] Python `cdcrig`: settings, SQL, collector, Debezium control, orchestrator.
- [x] Debezium image: control script, Java agent 3.7.10 (checksum pinned).
- [x] Ephemeral Terraform: SQL, pool, Event Hubs, Container Apps environment and
      two-container job, diagnostics. `validate` passes.
- [x] Workflows `run-test`, `sweep`; sanitiser for results.
- [x] Container tests (local, linux/amd64): Python 27/27; Debezium control
      protocol 4/4. Bug found and fixed: CDC cleanup never deleted rows at the
      current maximum LSN; a marker row now moves the mark past them.
- [x] PR, CI green, merge (PRs #5, #6).
- [x] Resource group snapshot around the first ephemeral apply (run
      45049809770, 2026-11-22): 29 groups before, the same 29 while the
      Container Apps environment existed. No group outside the project
      (decision 0008 open question closed).
- [x] First spike dispatch (run 45049353274) refused by preflight: an untagged
      "Failure Anomalies" alert rule that Azure adds with Application
      Insights. Allowlisted by exact type (PR #6). Nothing was created.
- [x] Spike run 45049809770 failed at `terraform apply`: both databases
      returned "The zone resilience value of the database and elastic pool
      should match." The provider sends `zone_redundant = false` when the
      argument is unset (provider source, v5.14.0). Fix: set it to `true` on
      the databases. The rig never started, so no spike item was tested.
- [x] Spike item 7 (environment creation time): 5m50s to create, 24m18s to
      delete. Destroy and verify-clean of that run passed. Owner chose to make the
      environment persistent with D8 at zero nodes, and to raise the budget
      to 100 (decision 0013).
- [x] Persistent apply (PR #8): environment 4m58s, diagnostic setting,
      budget 100. `minimum_count = 0` accepted for D8.
- [x] Spike run 45054291170: rig succeeded, all checks passed. Items 1 to 8
      recorded in decision 0014. Export failed on an expired OIDC token
      (fixed: `ephemeral.sh relogin`). Destroy 2m52s, verify-clean passed.
- [x] Spike run 45056601736: all checks passed again; export succeeded, so
      the re-sign-in works. Event Hubs message counts still absent from Log
      Analytics although documented as exportable.
- [ ] Event Hubs delivery: the export step now reads `IncomingMessages` from
      Azure Monitor before destroy. Proof due in the next run that sends events.
- [ ] Debezium telemetry missing from Application Insights (decision 0014).
- [ ] Catch-up timing is understated: `wait_for_catchup` declares catch-up
      when Debezium has seen as many events as the change table holds, before
      capture has copied every committed change. Run 45054291170-1: 8,841 of
      9,000 ladder changes, 9,000 of 20,000 after top-up, 35,000 of 40,000
      after purge. Found by the report generator's catch-up check.
- [ ] Destroy on failure: run 45058781127 (inject=fail).
- [ ] Destroy on timeout: waived for today's runs by the owner (decision 0015); run it afterwards.
- [x] Destroy on failure proved: run 45058781127, 14 destroyed, verify-clean passed.
- [ ] Catch-up flaw (found by the report generator): catch-up compared Debezium
      with the change table before capture had copied every commit. Fixed with
      a marker row (PR: catch-up marker). Spike catch-up times are not valid.
- [x] Load steps 100 to 500 tps for quick and confirm (owner, decision 0017).
- [x] Owner: three repeats of B and C, A once; 6 vCore run dropped for today (decision 0018).
- [x] Report: storage and backup cost of CDC section; pairs with A use repeat 1 only (decision 0016, branch `agent/report-storage-cost`).
- [ ] Smoke, then quick (4 vCores), then confirm (6 vCores); remove the persistent environment after.

## Quick run 45062722769 (2026-11-22)

- Mode quick, 4 vCore pool, loads 100, 200, 300, 400, 500 updates per second; B and C three rounds each, A once; 10 GB seed. Run, destroy and verify-clean succeeded.
- Report: `results` branch, `results/45062722769-1/report/` (SUMMARY.md for readers without context, README.md technical appendix), sanitised raw files in `raw/`.
- Main measured findings: CDC adds about 10 KB of log per update (+65%), shows at pool level as +15 points of pool log write rate at 400 updates per second and about 900 ms/s of POOL_LOG_RATE_GOVERNOR waits; database CPU, database log rate and p95 latency differences are within run-to-run variation. Both B and C failed 500 in two of three rounds (workers exhausted behind data IO near its cap).
- Not verified: Event Hubs counts for this run (metrics window bug, fixed in PR #16 for later runs); Debezium telemetry in Application Insights; 6 vCore pool (not run); data storage price for a zone-redundant pool; timeout destroy proof (waived).
- After the run the Container Apps environment was switched off (decision 0013 amendment).

## Open questions carried forward

- After the 6 vCore confirming run: remove the Container Apps environment from
  the persistent layer (decision 0013), so the plan management fee stops.

- Debezium to Event Hubs without a key: the Event Hubs sink accepts only a
  connection string (verified), which would be stop condition 2. Plan: Kafka
  sink to the Event Hubs Kafka endpoint with SASL OAUTHBEARER and a small Java
  callback handler using `azure-identity` ManagedIdentityCredential. Spike item.
- Debezium Server image lacks `azure-identity` for JDBC managed identity. A
  derived image adds the jars. Spike item 1.
- Debezium's SQL Server Agent status check on Azure SQL Database is
  undocumented. Spike item.
- OIDC subject: the repo uses GitHub's immutable subject format. The
  environment form of the subject is assumed, confirmed at first sign-in.
- Local Docker daemon on the owner's machine returns HTTP 500. Container tests
  run in CI.

## Findings that change the plan

- A pooled database capped at 2 vCores probably has a log rate limit near
  9 MiB/s, below the pool's 24 MiB/s (inference). The per-database cap may bind
  first. That feeds decision D4 for the confirming run.

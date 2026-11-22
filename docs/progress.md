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
| 5 | Report generator and sizing translation | Not started |
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
- [ ] PR, CI green, merge.
- [ ] Retake resource group snapshot, dispatch `run-test` mode `spike`,
      compare snapshot (decision 0008).
- [ ] Record spike results per design section 12 items 1 to 8.

## Open questions carried forward

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

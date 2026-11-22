# AGENTS.md

Single source of truth for the rules that govern AI agents working in this
repository. It is tool-neutral.

How each tool loads this file:
- OpenAI Codex CLI reads AGENTS.md natively.
- Claude Code does not. It reads CLAUDE.md, whose first line is `@AGENTS.md`.
  That import line is load-bearing. Do not remove it.

## What this repo is

A public, automated experiment that measures what change data capture (CDC)
costs on an Azure SQL elastic pool.

The scenario: an API updates a task row and, in the same transaction, inserts a
JSON payload into an outbox table. CDC is enabled on the outbox table in one of
two databases that share a 4 vCore General Purpose pool. Debezium reads the
changes. The experiment reports which resource limit is reached first, at what
load, how much CDC adds, whether the neighbouring database suffers, and what
pool size and price that implies.

The full specification is `docs/design.md`. Read it before doing anything.
Section 2 lists what the owner decided. Section 10 lists what was delegated.
Section 13 is the build order. Section 14 is the handoff from the design
session.

Everything here is public and carries the owner's name.

## Current state

Update this section whenever the state changes. Keep it to a few lines. The
detailed task list and next steps live in `docs/progress.md`.

Last updated: 2026-11-22.

- Build order step 1 done. Persistent layer applied (decisions 0006, 0011,
  0012), including Application Insights and diagnostic settings.
- Step 2 (spike) in progress. Everything is merged through PR #7. First
  spike run 45049809770 failed at apply (database zone redundancy, fixed in
  PR #7). Its destroy and verify-clean passed. No spike item has passed yet.
- The Container Apps environment is persistent (decision 0013); budget 100.
  Remove it after the confirming run.
- No measurement exists.
- Detail and next steps: `docs/progress.md`.

## Autonomy model

Governance is fully autonomous. The owner chose this on 2026-11-22. See
`docs/decisions/0001-fully-autonomous-governance.md`.

An agent may, without asking:
- Write code, Terraform, workflows and docs.
- Open a pull request and merge it once CI is green.
- Dispatch the `run-test`, `sweep` and `destroy` workflows.
- Apply the persistent layer once from a local session (see Bootstrap).
- Make any design choice not fixed in `docs/design.md` section 2, provided it
  records the choice and the reason in `docs/decisions/`.

The owner does not review pull requests. That puts the whole burden of
correctness on the agent. Read "Verification" and "Truth rules" as binding, not
advisory.

Do not ask the owner questions that this file or the design already answers.
Decide, record the decision, and continue.

## Stop conditions

Autonomy ends here. In each case stop, state what happened in three lines (what
broke, what it means, what you propose), and wait.

1. The active Azure subscription is not the one the owner named at the start of
   the session, or the owner has not named one.
2. A task appears to need a secret, password, key or connection string.
3. A change would create, modify or delete anything outside this project's
   resource group.
4. Ephemeral resources still exist after `destroy` and `sweep` have both run.
5. The budget alert has fired.
6. A fact in `docs/design.md` section 2 turns out to be impossible to build as
   stated. Do not silently substitute something else.
7. The signed-in identity lacks the rights for the bootstrap.

## Hard safety rules

These are not relaxed by the autonomy model.

- The ephemeral layer is changed only by the pipeline. Never run
  `terraform apply` or `terraform destroy` against it from a local session.
- Nothing in the ephemeral layer may outlive a run. Prove destroy works on
  success, on failure and on timeout before the first run longer than ten
  minutes.
- A budget alert on the resource group must exist in committed Terraform before
  the first apply of the ephemeral layer.
- Never commit secrets, keys, connection strings, or subscription and tenant
  identifiers. The pipeline receives identifiers as GitHub environment secrets
  so that logs mask them. Authentication is OpenID Connect federation only. No
  stored credentials.
- Exported results and reports are public. Strip resource identifiers, server
  names and principal identifiers before anything is committed or uploaded as
  an artifact.
- Workflows that touch Azure run only on the `azure` environment, which accepts
  deployments from `main` only.
- Never use `pull_request_target` with a checkout of pull request head code.
  Pull requests from forks never receive Azure credentials.
- Workflows run on `ubuntu-latest`. Third-party actions are pinned to a commit
  hash.
- Never force-push `main`. Never delete the repo or the resource group.
- Commits and pull requests carry no Claude session links. The binding is
  `attribution.sessionUrl = false` in `.claude/settings.json`. Do not remove it.

## Truth rules

This repo publishes measurements. A wrong number here is worse than no number.

- Never state a throughput, latency, IO, cost or sizing figure that was not
  measured by a run in this repo. Estimates say "estimate", give their basis
  and their date. `docs/design.md` section 4 is estimate only and must never be
  quoted as a result.
- Every reported figure traces to a run ID and the raw files for that run.
- Before writing any Azure capability, SKU, limit, API version or preview
  status into code or docs, verify it against current Microsoft Learn. Do not
  rely on training data. Use the documentation-verification agent. If it
  returns UNVERIFIABLE, the claim does not ship.
- The same applies to Debezium and Terraform provider claims: verify against
  current project documentation.
- The limits in `docs/design.md` section 3 were checked on 2026-11-22. Re-check
  before relying on them. The harness also reads the applied limits from
  `sys.dm_user_db_resource_governance` at the start of every run and records
  them. The recorded values win over the documented ones.
- If the spread across repeats is larger than the effect, say so and give no
  uplift figure. With one repeat, say that noise cannot be told from effect.
- Keep measured results, derived figures and extrapolations visibly separate
  in every report.
- If you do not know, say so in the pull request. Do not guess.

## Verification

Never claim something works without running it.

- **unit**: plain test run. Required for every code change.
- **containers**: integration tests against SQL Server and Debezium in
  containers, in ordinary CI. Use this for the load generator's transaction,
  the schema, CDC enablement, the collector's queries and the Debezium
  configuration. Most work is verifiable this way with zero Azure.
- **live**: needs real Azure. Only for what containers cannot show: pool
  resource governance, zone redundancy, managed identity, Container Apps jobs,
  and the measurements themselves.

A container SQL Server has no elastic pool and no resource governance. A green
container test proves the mechanics work. It proves nothing about limits. Do
not infer performance from containers.

Before merging a pull request:
- CI is green.
- You ran the nearest test or build locally and read the output.
- `terraform fmt -check` and `terraform validate` pass for any Terraform change.
- The pull request description states which verification method was used and
  what was not verified.

Live runs are serialised. At most one run exists at a time. Check for an
existing run before dispatching.

## Bootstrap

The persistent layer (resource group contents, state storage, results storage,
Log Analytics workspace, Application Insights, container registry, Container
Apps environment, managed identities, role assignments, budget alert) is
created once.

- It is the only Terraform an agent may apply from a local session.
- Before applying: print the active subscription name and compare it with the
  name the owner gave at the start of the session. If they differ, or none was
  given, stop.
- Run `terraform plan` first and read it. It must create resources only in this
  project's resource group, plus the resource group itself.
- State for the persistent layer moves to the state storage account as soon as
  that account exists.
- Record the date and outcome in `docs/decisions/`, without identifiers.
- Writing the `azure` environment secrets (`AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) needs admin on the repository.
  GitHub's docs say configuring an environment in an organization repository
  needs admin access; the REST page for secrets says only "collaborator
  access", so the exact level is not documented and admin is treated as
  required (`docs/verification/2026-11-22-platform.md`, checked 2026-11-22).
  `haripraghash` has admin. `haripraghash-bot` has write only. A session
  without admin does every other step, then stops and names the secrets to set,
  never their values.
- Steps and commands: `docs/bootstrap.md`, script `scripts/bootstrap.sh`.

## Working method

- Follow the build order in `docs/design.md` section 13. Do the spike in
  section 12 first. Its first two items can change the authentication design.
- Small pull requests. One purpose each. Branch names start with `agent/`.
- Docs land in the same pull request as the code they describe.
- Record every non-trivial choice in `docs/decisions/` as a numbered file:
  context, decision, alternatives rejected, how it was verified.
- Keep the "Current state" section of this file and the status line of
  `docs/design.md` accurate.
- When a run produces results, review them before publishing. If a result
  contradicts the estimates in section 4 by a wide margin, look for a harness
  fault first.

## Style

- ASCII punctuation only: no em dashes, no en dashes, no smart quotes.
- Metric units. Binary units (MiB, GiB) where Azure uses them.
- Short sentences. Define a term the first time it appears in a document.
- Every human-facing document stands alone. Assume a reader who knows Azure but
  has never seen this repo and is new to CDC, Debezium and the outbox pattern.
- Do not use the word "seam". Say "test boundary".
- Reports lead with the answer, then the evidence, then the limitations.

## Repository layout

Planned. Create directories as they are needed, and keep this list accurate.

```
docs/design.md          The specification
docs/decisions/         Numbered decision records
docs/verification/      Claims checked against current documentation
docs/progress.md        Task list and status
docs/bootstrap.md       One-time persistent layer setup
scripts/                Bootstrap and repository checks
infra/persistent/       Terraform for the persistent layer
infra/ephemeral/        Terraform for the per-run layer
src/loadgen/            .NET load generator: the transaction and open-loop driver only (decision 0010)
src/rig/                Python package cdcrig: orchestrator and collector (decision 0010)
debezium/               Debezium image: token jar, control script, Java agent config
infra/modules/          Shared Terraform modules (diagnostics)
sql/                    Schema, seed and CDC scripts
report/                 Python report and sizing generator
tests/unit/             .NET unit tests
tests/containers/       .NET container tests (load generator against SQL Server)
tests/rig/              Python tests and the container test runner
.github/workflows/      validate, run-test, sweep, destroy
```

## Project mechanics

Append below as the codebase takes shape: exact build, test and run commands,
and anything a new session would otherwise have to rediscover.

- Progress and next steps: `docs/progress.md`. Verified facts:
  `docs/verification/`.
- Terraform: `terraform -chdir=infra/<layer> fmt -check -recursive`, then
  `init -backend=false` and `validate`. azurerm provider `~> 5.14`.
- Secret scan locally: `gitleaks git --redact --config .gitleaks.toml .` and
  `scripts/check-identifiers.sh` (fails on any GUID in a tracked file).
- Bootstrap: `scripts/bootstrap.sh plan|apply|migrate|secrets` with
  `EXPECTED_SUBSCRIPTION_NAME` and `EXPECTED_SUBSCRIPTION_ID` set.
- The pipeline finds persistent resources by their `purpose` tag in
  `rg-cdc-impact-test`. Generated names are never committed.
- .NET: `dotnet build CdcImpact.slnx`; `dotnet test --project tests/unit/UnitTests.csproj`;
  `dotnet test --project tests/containers/ContainerTests.csproj` (needs Docker).
  .NET 10 uses Microsoft.Testing.Platform (`global.json` "test" runner).
- Rig and Debezium images plus Python tests: `tests/rig/run-container-tests.sh`
  (builds linux/amd64 images, starts SQL Server, runs pytest in the rig image,
  then the Debezium control protocol test). On the owner's Mac, put
  `/Applications/Docker.app/Contents/Resources/bin` on PATH first, or image
  pulls fail on the credential helper.
- `nuget.config` clears machine feeds: the owner's machine has a private feed.
- Microsoft.Data.SqlClient rejects invariant globalization; it is off.
- Persistent plan check: `scripts/plan_check.py` (also used by run-test).
- Result files are scrubbed by `scripts/sanitize.py` before upload as artifacts.
- Pushing a rebased branch needs a force-push, which auto mode blocks. Push to
  a new branch name instead.
- The repo uses GitHub's immutable OIDC subject format. The prefix comes from
  `gh api repos/collaborationwithothers/azure-sql-cdc-impact-test/actions/oidc/customization/sub`.

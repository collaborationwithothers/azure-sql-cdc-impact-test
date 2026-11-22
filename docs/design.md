# CDC impact test: design proposal

Status: approved by the owner on 2026-11-22. Build in progress; see `docs/progress.md`. Open choices were delegated to the agent and are recorded in section 10.

**Changes since approval (owner decisions, 2026-11-22):**
- No virtual network and no private endpoint. SQL public endpoint with Entra-only sign-in (decision 0008). Section 5's networking bullets are superseded.
- .NET holds application logic only: the load generator. Orchestrator and collector are Python; Debezium runs in its own container in the same job, controlled through marker files (decision 0010). Resolves D6.
- Application Insights in the persistent layer, with full per-request tracing (decision 0011). Diagnostic settings on every resource (decision 0012).
- The quick run repeats B and C three times; A runs once. The 6 vCore confirming run is not run on 2026-11-22 (decision 0018).
- Quick and confirming runs use steps of 100, 200, 300, 400 and 500 transactions per second (decision 0017). Section 7's ladder and D4's step choice are superseded.
- The Container Apps environment is persistent, with the D8 profile at zero nodes between runs; the job stays per run. Budget 100 per month. Remove the environment after the confirming run (decision 0013, spike item 7).

## 1. What this answers

If CDC (change data capture) is enabled on an outbox table in one database of a shared elastic pool:

1. Which resource limit is reached first, and at what load?
2. How much extra log IO, data IO, CPU, storage and backup does CDC add, in percent and in absolute units?
3. Does the other database in the pool suffer?
4. What pool size and monthly price does that translate to?

## 2. Fixed inputs (agreed in the interview)

| Item | Value |
| --- | --- |
| Pool | General Purpose, standard-series (Gen5), 4 vCores, zone redundant, Sweden Central |
| Databases | 2, each capped at 2 vCores |
| Workload | Legal SaaS task update: update a task row and insert a JSON payload (about 4 KB) into an outbox table, in one transaction |
| CDC scope | Outbox table only, database 1 |
| Reader | Real Debezium |
| Database 2 | Fixed steady load, never CDC-enabled |
| Configurations | A: no outbox. B: outbox only. C: outbox with CDC and Debezium |
| Purge | Out-of-hours batch housekeeping of the outbox, measured as its own phase |
| Confirming run | Configuration C at 6 vCores |
| Delivery | Public repo under `collaborationwithothers`, Terraform, GitHub Actions |
| Cost control | Test infrastructure exists only during a run and is always destroyed |

## 3. Facts checked against Microsoft Learn

| Fact | 4 vCore pool | 6 vCore pool |
| --- | --- | --- |
| Max log rate per pool | 24 MiB/s | 36 MiB/s |
| Max data IOPS per pool | 2,800 | 4,200 |
| Memory | 20.8 GB | 31.1 GB |
| Max concurrent workers | 420 | 630 |
| Per-database vCore cap choices | 0 to 4, includes 2 | 0 to 6, includes 2 and 4 |

- Zone-redundant General Purpose is available in every region with availability zones, on standard-series hardware only. Sweden Central has availability zones.
- Microsoft states zone redundancy can increase transaction commit time.
- CDC capture runs every 20 seconds and cleanup every hour. Neither frequency can be changed.
- CDC cleanup retention defaults to 3 days and can be changed.
- With CDC on, the log cannot be truncated until the capture scan has caught up.
- Azure reports log throttling through wait types: `LOG_RATE_GOVERNOR` (database level) and `POOL_LOG_RATE_GOVERNOR` (pool level).
- `sys.dm_user_db_resource_governance` exposes the actual limits applied to each database.

Not yet verified: whether the 2 vCore per-database cap also lowers that database's log rate limit below the pool's 24 MiB/s. The harness reads the applied limit from `sys.dm_user_db_resource_governance` at the start of every run and records it, so the report does not depend on my assumption.

## 4. Expected ceilings (my estimate, not a measurement)

With the payload stored as `nvarchar(max)`, a 4,096 character payload is 8,192 bytes.

| Configuration | Approx. log written per transaction | Log-bound ceiling at 24 MiB/s |
| --- | --- | --- |
| A: no outbox | 0.3 KB | Log never binds. CPU binds first. |
| B: outbox only | 8.6 KB | about 2,900 transactions per second |
| C: outbox with CDC | 17 KB | about 1,450 transactions per second |

These figures only size the load ladder. The test exists to replace them with measurements.

## 5. Architecture

Two layers.

**Persistent layer (created once, costs close to nothing idle)**

- Resource group in the shared non-production subscription.
- Storage account holding Terraform state and exported results.
- Log Analytics workspace.
- Two managed identities with their role assignments:
  - Pipeline identity, trusted by GitHub through a federated credential limited to the repo's protected environment. Contributor on the resource group only.
  - Test-rig identity, used by the test-rig containers to reach SQL, storage and Event Hubs.
- Applied once by a person with Owner rights on the subscription. This is the only manual Azure step.

**Ephemeral layer (built per run, always destroyed)**

- Logical SQL server, Entra-only authentication, public access off.
- Elastic pool and two databases.
- Virtual network with a private endpoint to SQL.
- A Container Apps environment joined to the virtual network, with a dedicated workload profile (reserved machines of a chosen size, so client timing is steady).
- The "test rig": a Container Apps job with a manual trigger, running:
  - Load generator.
  - Debezium Server (the standalone form of Debezium, no Kafka cluster needed).
  - Metrics collector.
  - Orchestrator that walks the test matrix.
- Job retries are set to zero. Microsoft warns that platform maintenance can interrupt a job replica. A retried benchmark is a corrupted benchmark, so an interrupted run is marked failed.
- Event Hubs namespace as Debezium's publish target.
- Diagnostic settings streaming pool and database metrics to the persistent Log Analytics workspace.

**Why not the GitHub runner for load:** the runner's region and size are not under our control, and a job is killed at 6 hours.

**Why Container Apps jobs and not a VM or Container Instances:** a job starts, runs to completion and stops, which is the shape of a test run. The pipeline starts it with `az containerapp job start` and reads its status with `az containerapp job execution list`. A VM gives the steadiest timing but needs an operating system bootstrap, the most fragile part of a hands-off build. Container Instances has no dedicated compute option and no native run status.

## 6. Components

**Load generator.** A .NET console application.

- Open-loop: it sends transactions at a fixed target rate regardless of how fast the database answers. A closed-loop generator slows down when the database slows down, which hides the very degradation being measured.
- Executes the transaction directly against SQL. There is no HTTP API in the path (see decision D2).
- Records achieved rate, latency percentiles (median, 95th, 99th) and its own CPU.
- One instance per database: the ladder on database 1, the fixed load on database 2.

**Schema.**

- `Matter`, `Task` (narrow columns: status, assignee, due date, timestamps), `Outbox` (id, aggregate id, event type, created time, payload).
- `Task` pre-seeded to about 10 GB per database (parameter).
- Payload column type: `nvarchar(max)` (see decision D1).

**Debezium Server.**

- SQL Server connector, default polling interval, no initial data snapshot.
- Publishes to Event Hubs.
- Running only in configuration C.

**Metrics collector.** Polls every 10 seconds and writes files to the results storage.

| Source | What it gives |
| --- | --- |
| `sys.dm_db_resource_stats`, `sys.dm_elastic_pool_resource_stats` | CPU, data IO, log write as percent of limit, per database and per pool |
| `sys.dm_io_virtual_file_stats` | Absolute bytes and IO counts per file, write latency, IO queued by governance |
| `sys.dm_os_wait_stats` deltas | Throttling evidence: `LOG_RATE_GOVERNOR`, `POOL_LOG_RATE_GOVERNOR`, `WRITELOG`, `PAGEIOLATCH_*` |
| `sys.dm_db_log_space_usage`, `log_reuse_wait_desc` | Log space held, and whether CDC is what holds it |
| `sys.dm_cdc_log_scan_sessions`, `sys.dm_cdc_errors` | Capture throughput, latency and errors |
| `sys.dm_db_partition_stats` | Size of outbox and change table |
| `sys.dm_os_performance_counters` | Log bytes flushed per second, buffer cache counters |
| Debezium | Lag behind the change table |
| Load generator | Throughput, latency, client CPU |

Every record carries a run ID, configuration, load step and repeat number.

## 7. Test matrix

The owner chose the quick run as the default. The full run remains as an option.

**Quick run (default, about 1 hour measured).**

- Ladder steps on database 1, in transactions per second: 100, 200, 400, 800, 1,200, 1,600.
- Each step: 1 minute warm-up, 2 minutes measured. Two minutes spans 6 capture cycles and 8 resource samples. Do not shorten further.
- One repeat. Three configurations. 6 steps x 3 configurations x 3 minutes = 54 minutes.
- Stop rule per configuration: stop climbing when achieved rate falls below 95 percent of target.
- Database 2 runs a constant 50 transactions per second of configuration B throughout.
- Between configurations: outbox emptied, CDC state reset, log allowed to drain.
- Followed by the purge and cleanup phases below, about 10 minutes.
- Known weaknesses: noise cannot be told from effect with one repeat, lag trend evidence is thin, and the load at which a limit is reached is known only to within the gap between steps.

**Targeted pass (optional, run after reviewing a quick run).**

- Parameters select specific steps, configurations, repeat count and window length.
- Use it when the quick result sits near a pool-size boundary, or when a reading looks like noise.

**Full run (optional).**

- Steps: 50, 100, 200, 400, 600, 800, 1,000, 1,200, 1,400, 1,600, then 2,000, 2,400, 3,000 for configurations that have not yet hit a limit.
- Each step: 2 minutes warm-up, 6 minutes measured.
- Three repeats, blocked by repeat: all three configurations run within repeat 1, then repeat 2, then repeat 3. Slow drift in the platform then affects all configurations equally.
- Adds a second stop rule: CDC lag grows continuously through the step.
- Roughly 16 hours of pool time (estimate).

**Production-shaped profile.** Made-up figures, all parameters.

- 20 transactions per second baseline, 100 at peak.
- In the quick run this is not a separate phase. The position of 20 and 100 transactions per second is read off the ladder curve.
- In the full run: a compressed working day over 30 minutes, in all three configurations.

**Purge phase.**

- Outbox rows written during the ladder are deleted in batches of 5,000 with no user load. If the ladder left fewer than 200,000 rows, the outbox is topped up first.
- Run in configurations B and C.
- Measures: duration, log written, time for CDC and Debezium to catch up afterwards.

**CDC cleanup phase (replaces the 3 hour soak).**

- CDC's own cleanup only deletes rows older than the retention period, and its schedule is hourly. In a short run it would delete nothing.
- The harness lowers retention, then calls `sys.sp_cdc_cleanup_change_table` on demand against the change rows produced by the ladder, and measures log, data IO, CPU and duration.
- About 10 minutes with the purge phase.
- Limitation: this measures one on-demand cleanup, not the hourly job running under live load.

**Confirming run.** Configuration C at 6 vCores, at the three ladder steps around the 4 vCore limit (see decision D4). Same step length as the quick run.

**Smoke mode.** One configuration, two steps, about 10 minutes. Used to validate the harness.

**Duration.** The quick run is about 65 minutes measured. Deploy, seed and destroy add roughly 30 minutes of pool time. Both are estimates.

## 8. Pipeline

| Workflow | Trigger | Does |
| --- | --- | --- |
| `validate` | Pull request | Terraform format and validate, build, unit tests. No Azure access. |
| `run-test` | Manual dispatch, on the protected environment | Applies the ephemeral layer, starts the job, waits for it, exports results, builds the report, destroys the ephemeral layer. The destroy step runs even when earlier steps fail. |
| `sweep` | Every 30 minutes, and manual | Destroys any ephemeral resource past its deadline tag. In full-run mode it also exports results and builds the report. |
| `destroy` | Manual | Destroys the ephemeral layer immediately. |

- The quick run fits inside one workflow run, well under the 6 hour job limit.
- The full run does not. In that mode `run-test` exits after starting the job and `sweep` completes it.
- `sweep` is the safety net for a crashed or cancelled run.
- Every ephemeral resource carries a deadline tag. `sweep` destroys anything past it.
- A budget alert on the resource group exists in Terraform before the first apply of the ephemeral layer.
- Pull requests from forks never receive Azure credentials.

## 9. Report and sizing translation

Generated by a Python script using pandas (a Python data analysis library) and matplotlib (a charting library).

- Per metric: a chart of value against load, one line per configuration, with the spread across repeats.
- Per load step and configuration: which limit was reached first.
- Uplift tables: C minus B (CDC alone), B minus A (outbox alone), C minus A (both), in percent and absolute units.
- Neighbour impact: database 2 latency and throughput against database 1 load.
- Purge and soak results.
- Sizing table: for each load level, the smallest pool that carries it with and without CDC, and the monthly price difference. Prices are read from the Azure Retail Prices API at report time, for zone-redundant General Purpose in Sweden Central.
- Client-side proof: load generator CPU per step, to show the client never limited the test.
- Stated limitations (section 11).

Raw files stay in the results storage. The report is committed to a `results` branch of the repo.

## 10. Decisions delegated to the agent

The owner asked for a fully hands-off build and did not rule on these individually. They stand as decided. Any of them can be reopened by the owner.

**D1. Payload column is `nvarchar(max)`.**
- Why: it is what Entity Framework generates for a string property by default, so it is the likeliest production shape.
- Effect: a 4,096 character payload is 8,192 bytes and is always stored off-row. The result sits clearly on one side of the storage cliff.
- Alternative: `varchar(max)` halves the bytes and keeps the payload in-row. It is a parameter, so a second run can compare.

**D2. The load generator calls SQL directly. No HTTP API is deployed.**
- Why: the question is about database resources. An API tier adds a second thing that can saturate first, and more to build and destroy.
- Counterargument: a real API shapes the load through its connection pool and retry behaviour. Under throttling, retries can amplify load in a way a direct generator will not show.
- Mitigation: the generator uses a bounded connection pool like an API would. Retry amplification is listed as a limitation.

**D3. Debezium publishes to Event Hubs.**
- Why: no broker to run inside the test rig, and it is the likely Azure target.
- The target does not affect load on the pool.

**D4. The 6 vCore confirming run keeps the per-database cap at 2 vCores unless the 4 vCore results show the per-database cap was the binding limit.**
- Why: if the pool-level log rate binds, a bigger pool is the remedy. If the per-database CPU cap binds, a bigger pool with the same cap fixes nothing, and the remedy is raising the cap. The confirming run should test whichever remedy the data points to.

**D5. Seed size is 10 GB per database.**
- Why: seeding happens on every run because the databases are ephemeral. 10 GB takes roughly 15 to 20 minutes for both.
- Limitation: the pool has 20.8 GB of memory, so most data stays cached and read IO is understated compared with a large production database.

**D6. The test rig is a Container Apps job on a dedicated workload profile.**
- Why: see section 5.
- Open point for the spike: whether Debezium runs as a second container in the job, or as a long-running container app beside it. Microsoft calls several containers in one job an advanced scenario.

**D7. Governance is fully autonomous.**
- Chosen explicitly by the owner on 2026-11-22. Recorded in `docs/decisions/0001-fully-autonomous-governance.md`.

## 11. Where this fails

- **Made-up load numbers.** The production-shaped profile is a guess. The ladder is valid regardless. Position on the ladder must be re-read when real numbers exist.
- **One payload size.** No curve for payload growth. A different size needs a rerun.
- **Read IO understated.** Data smaller than memory means few physical reads (D5).
- **Backup storage is estimated, not measured.** Backup billing metrics update too slowly for a run of hours. The report derives the figure from measured log bytes and labels it as derived.
- **No retry amplification.** See D2.
- **Platform noise.** General Purpose uses remote storage and a shared platform. The quick run has one repeat, so it cannot separate noise from effect. Large effects will be clear. Small ones, such as neighbour impact, need a targeted pass with repeats before they are trusted. Where repeats exist and their spread is larger than the CDC effect at a step, the report says so and gives no uplift figure for that step.
- **Coarse limit location.** With six steps, the load at which a limit is reached is bracketed, for example "between 800 and 1,200". If that bracket spans a pool-size boundary, run a targeted pass.
- **Sizing beyond 6 vCores is extrapolated** from documented limits.
- **Capture may not scale with vCores.** The 20 second cycle is fixed. If capture throughput is the bottleneck, the sizing table may show that a bigger pool does not help. That would be a finding, but it means the cost answer becomes "CDC is not viable at that load on this tier" and not a price.

## 12. Things to prove in a spike before the full build

1. Debezium Server can authenticate to Azure SQL with a managed identity. If not, fall back to a SQL login held in Key Vault, which also means allowing SQL authentication on the server.
2. The principal that creates the database can enable CDC. Microsoft documents failures when the creator and the enabler differ in type (Entra versus SQL).
3. Debezium lag can be read from Debezium Server's metrics. If not, derive it from event timestamps in Event Hubs.
4. Azure Policy in the shared subscription does not block any ephemeral resource.
5. Quota exists in Sweden Central for the dedicated workload profile and the pool.
6. Reading `sys.dm_os_wait_stats` for a pooled database works with the server role `##MS_ServerStateReader##`.
7. How long a Container Apps environment takes to create. If it dominates run time, move it to the persistent layer on the consumption profile.
8. Debezium placement (D6).

## 13. Build order

1. Repo, `validate` workflow, persistent layer Terraform, bootstrap instructions.
2. Spike: ephemeral layer plus one configuration C step, proving the six items above.
3. Load generator, collector, orchestrator, smoke mode.
4. `run-test`, `sweep`, `destroy` workflows. Prove destroy on success, failure and timeout.
5. Report generator and sizing translation.
6. First full grid at 4 vCores. Review results.
7. Confirming run at 6 vCores.

## 14. Handoff notes for the implementing session

This document was produced in a requirements and design session. Implementation happens in a different session that has no access to that conversation. Everything needed is in this file.

- **Settled, do not re-ask:** every row in section 2. Each was an explicit choice by the owner.
- **Also settled:** decisions D1 to D7 in section 10. The owner delegated D1 to D6 and chose D7.
- **Why three configurations:** the owner wants the cost of the outbox separated from the cost of CDC.
- **Why database 2 has a fixed load:** it is the instrument for detecting neighbour impact. Do not vary it.
- **Why the purge is a separate phase:** production purges out of business hours, so it must not overlap the ladder.
- **Why Log Analytics is persistent:** platform metrics cannot be queried in the normal way after the pool is destroyed.
- **Hard rule on cost:** nothing in the ephemeral layer may outlive a run. Prove destroy on success, failure and timeout before any long run.
- **Hard rule on the public repo:** no Azure credentials reach pull requests from forks. No subscription or tenant identifiers in code, logs or results.
- **Facts in section 3 were checked on 2026-11-22.** Re-check limits before relying on them. Section 4 is estimate only.
- **Start with the spike in section 12.** Items 1 and 2 can each force a change to the authentication design.
- **Not started:** no repo, no Azure resources, no code.

## 15. What the owner must still supply

1. The name of the Azure subscription to use, given once at the start of the implementing session. It is never committed.
2. Nothing else. If the signed-in Azure identity lacks the rights to apply the persistent layer, the agent stops and says so.

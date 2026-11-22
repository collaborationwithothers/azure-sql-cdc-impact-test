# 0010. .NET holds application logic only; Python orchestrates; Debezium is its own container

Date: 2026-11-22
Decided by: the owner ("dotnet code should only have application logic",
option 1 when asked). Supersedes decision 0009.

## Context

The first draft put everything in one .NET program: the transaction, the
open-loop driver, the DMV collector, the test matrix, CDC control, purge,
cleanup, and Debezium started as a child process. The owner ruled that .NET
code holds only application logic.

## Decision

Three pieces, two containers in one Container Apps job replica.

1. **Load generator, .NET** (`src/loadgen`). The application logic only: the
   task update plus outbox insert transaction, and the open-loop driver that
   stands in for the API. A small command-line tool:
   - `loadgen window` runs a warm-up window then a measured window at one
     target rate, and prints both results as JSON.
   - `loadgen steady` runs a constant rate in fixed windows until stopped,
     appending one JSON line per window (database 2's neighbour load).
   It connects with the managed identity. It writes no files except its
   output and talks to nothing but SQL.
2. **Orchestrator and collector, Python** (`src/rig`, package `cdcrig`). Walks
   the test matrix, runs the SQL scripts in `sql/`, calls the load generator
   per step, polls the DMVs, reads Debezium's metrics, controls Debezium
   through marker files, and uploads results. Python sits next to the report
   generator, which also handles metrics.
3. **Debezium Server, its own container** (`debezium/`). The official image
   plus the token jar (decision 0007) and a control script. It has its own CPU
   allocation, so Debezium cannot slow the load generator.

### Debezium control protocol

Both containers mount one EmptyDir volume at `/shared`. Containers in one
replica share the network, so the orchestrator reads Debezium's metrics on
`localhost:9404`.

- The orchestrator writes `/shared/debezium/application.properties`, then
  creates `/shared/debezium/start`.
- `debezium/control.sh` sees `start`, clears Debezium's offset and history
  files, starts Debezium Server, and writes `running`.
- `stop` makes it send SIGTERM, wait, and write `stopped`.
- `/shared/done` makes it stop Debezium if running and exit 0, so the job can
  complete.
- Watchdog: if the orchestrator's `/shared/heartbeat` is older than ten
  minutes, the control script exits non-zero. A crashed orchestrator therefore
  cannot leave the job running until its replica timeout.

## Alternatives rejected

- **Everything in .NET** (decision 0009). Ruled out by the owner.
- **Debezium as a separate long-running Container App.** Starting and stopping
  it needs Azure management rights the rig identity does not have.
- **Collector in .NET as part of the measurement app** (option 2). Not chosen.

## Consequences

- The SQL control logic moves from C# to Python. The .NET container tests now
  cover only the transaction and the open-loop driver. Python tests cover
  schema, seed, CDC, purge, cleanup and the collector queries.
- Two images: `rig` (Python, the self-contained load generator, ODBC Driver 18)
  and `debezium`.
- Microsoft calls several containers in one job an advanced scenario. The
  spike proves it.

## How it is verified

Unit tests per component; container tests run the rig image against SQL
Server in a container; the spike proves the two-container job, the shared
volume, managed identity in both containers, and the control protocol.

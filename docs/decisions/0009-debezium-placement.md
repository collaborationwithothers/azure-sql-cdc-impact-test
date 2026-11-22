# 0009. Debezium runs as a child process of the orchestrator (D6)

Date: 2026-11-22
Decided by: agent (design D6 left this open for the spike).
Status: superseded by 0010 on 2026-11-22. Never built as described.

## Context

Debezium Server must run only in configuration C, start with fresh state each
time, and stop afterwards. The test rig is a Container Apps job. A job
execution completes only when all its containers exit. Microsoft calls several
containers in one job "an advanced scenario". The rig identity has no Azure
management rights.

## Decision

The rig image contains a Java 21 runtime and the Debezium Server 3.7.1
distribution next to the .NET orchestrator. In configuration C the
orchestrator writes `application.properties`, runs `/debezium/run.sh` as a
child process, waits for the streaming "Connected" metric, runs the ladder,
purge and cleanup, then sends SIGTERM so offsets are flushed. Debezium's log
is saved as `debezium.log` with the results. Its JMX metrics are read from the
bundled Prometheus exporter on `localhost:9404`.

## Alternatives rejected

- **Second container in the job.** Own CPU allocation, which is better
  isolation. But start, stop and exit would need signalling through marker
  files on a shared volume, and the job would hang if Debezium never exited.
- **Long-running container app beside the job.** Starting and stopping it
  needs Azure management rights the rig identity does not have, and the
  pipeline identity cannot grant roles.

## Risk and mitigation

Debezium shares the container's CPU and memory with the load generator, so it
could delay the client and inflate client-side latency in configuration C.
- Process CPU is recorded for every load window (design section 9,
  client-side proof).
- The container gets most of a dedicated D8 node.
- SQL-side figures (log rate, IO, CPU, waits) come from server DMVs and are not
  affected by client contention.
- If client CPU is high in configuration C, the report says the client may have
  limited the test, and the sidecar option is reopened.

## How it is verified

The spike checks that Debezium starts, connects and its metrics are readable,
and records client CPU with Debezium running.

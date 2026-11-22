# 0004. The test-rig identity is the SQL server's Entra admin

Date: 2026-11-22
Decided by: agent. Provisional until spike items 1, 2 and 6 pass.

## Context

The ephemeral SQL server uses Microsoft Entra-only authentication (design
section 5). Something must create the schema, enable CDC (change data capture,
the SQL feature that copies row changes from the transaction log into change
tables), read server-level DMVs (dynamic management views, the system views
that expose resource usage and waits), and run the workload.

Microsoft documents that CDC breaks when the database creator and the CDC
enabler differ in type, Entra versus SQL. It also documents that
`sys.dm_os_wait_stats` and `sys.dm_user_db_resource_governance` for pooled
databases need the server admin, the Entra admin, or
`##MS_ServerStateReader##`.

## Decision

The ephemeral server's Entra admin is the persistent test-rig user-assigned
managed identity. Every rig component (load generator, collector, orchestrator,
Debezium) connects as that identity. Entra-only authentication stays on, so no
SQL login exists.

## Alternatives rejected

- **Separate least-privilege identities per component.** Better practice, but
  each extra principal is created inside the database by the rig, and the
  ephemeral server lives for under two hours. It adds failure points without
  reducing any real exposure.
- **The pipeline identity as Entra admin.** The pipeline cannot reach the
  server, because public network access is off and the runner is outside the
  virtual network.

## Consequence for the spike

Spike item 6 (`##MS_ServerStateReader##`) becomes: confirm the Entra admin can
read every DMV the collector needs. If Debezium cannot use a managed identity
(spike item 1), the design fallback is a SQL login in Key Vault, which would
also need SQL authentication enabled. That fallback needs a secret and is
stop condition 2, so it is not taken without the owner.

## How it was verified

Permission requirements: `docs/verification/2026-11-22-azure-sql.md`.
Whether this identity can enable CDC is not documented. The spike tests it.

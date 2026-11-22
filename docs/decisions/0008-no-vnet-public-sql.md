# 0008. No virtual network; SQL public endpoint with Entra-only sign-in

Date: 2026-11-22
Decided by: the owner, when asked.

## Context

Design section 5 put the Container Apps environment in a virtual network with
a private endpoint to SQL. Microsoft Learn says an environment in a custom
virtual network makes Azure create an extra resource group, prefixed `ME_` by
default, outside the project resource group. Learn does not say whether
deleting the environment removes it. AGENTS.md stop condition 3 forbids
creating anything outside the project resource group, so the agent stopped and
asked. See `docs/verification/2026-11-22-platform.md` and the spike-prep
verification notes.

## Decision

The owner chose: no virtual network, and public network access on the SQL
server, limited to Microsoft Entra sign-in.

- The Container Apps environment uses the platform's default network, with a
  dedicated workload profile.
- The SQL server has public network access on, Entra-only authentication, no
  SQL logins, and TLS 1.2 minimum.
- Firewall: the "Allow Azure services and resources" rule (start and end
  `0.0.0.0`). The Container Apps environment has no fixed, documented outbound
  address that could be allow-listed instead.
- No private endpoint and no private DNS zone.

## Consequences

- Any Azure-hosted client can reach the SQL login endpoint. Only the rig's
  managed identity can sign in, because it is the only Entra principal with
  access and SQL authentication is off. The server exists for about two hours.
- Traffic between the rig and SQL crosses the public Azure backbone instead of
  a private endpoint. Latency may differ slightly from a private-endpoint
  production setup. The report states this as a limitation.
- Learn documents the `ME_` group only for custom virtual networks. Whether an
  environment on the default network creates any hidden resource group is not
  documented. Before the first ephemeral apply, the agent records the
  subscription's resource group list, compares it right after, and stops if a
  new group appears.

## Alternatives rejected

- **Keep the virtual network and accept the `ME_` group with safeguards.**
  Recommended by the agent; not chosen by the owner.
- **Stop and redesign hosting.** Not chosen.

## How it is verified

The resource group comparison around the first apply, recorded in
`docs/progress.md`.

# 0012. Diagnostic settings on every resource

Date: 2026-11-22
Decided by: the owner ("wire up diagnostic settings to every Azure resource so
that metrics and platform logs go to Log Analytics in the persistent layer").

## Decision

`infra/modules/diagnostics` creates one diagnostic setting per target. It reads
the categories each resource supports (`azurerm_monitor_diagnostic_categories`)
and enables all of them: the `allLogs` category group where offered, otherwise
every log category, plus every metric category. Both layers use it.

Covered: storage account and its blob service, Log Analytics workspace,
container registry (persistent); SQL elastic pool, both databases, the
`master` database, Event Hubs namespace, Container Apps environment
(ephemeral).

Not covered, and why:
- Managed identities, role assignments, budgets, firewall rules: Azure offers
  no diagnostic settings for them.
- Application Insights: workspace-based, so its data is already in the
  workspace. A diagnostic setting would store it twice.
- The SQL logical server itself has no diagnostic settings; its `master`
  database does, and is covered.
- The Container Apps job: categories are not documented for jobs. Its console
  and system logs arrive through the environment. Revisit after the spike.

The workspace daily cap rises from 2 GB to 5 GB, so diagnostic volume during a
run is not cut off.

## How it is verified

`terraform plan` lists one setting per target. After the spike, a Log
Analytics query per resource type confirms data arrived.

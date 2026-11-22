# 0013. Container Apps environment moves to the persistent layer

Date: 2026-11-22
Decided by: the owner, choosing between three options the agent laid out
("option 2", then "raise it" for the budget).

## Context

Design section 12 item 7 asks how long a Container Apps environment takes to
create, and says to move it to the persistent layer if that dominates run time.

Measured in spike run 45049809770 (apply failed later for an unrelated reason):
- Container Apps environment: created in 5m50s.
- Event Hubs namespace 30s, SQL server 1m03s, elastic pool 1m18s.
- Destroy: the environment was the only ephemeral resource left 13 minutes into
  the destroy job. The exact delete time is recorded in progress once the job
  ends.

The environment is the largest fixed cost of every run in wall-clock time.

## Decision

- The environment `cae-cdc-impact-<suffix>` is persistent, tagged
  `purpose=rig-environment`, with its diagnostic setting (decision 0012).
- It keeps the dedicated profile `rig` (D8, decision D6) with
  `minimum_count = 0` and `maximum_count = 1`, so no node runs between runs.
- The job stays ephemeral. The pipeline finds the environment by its tag.
- The resource group budget rises from 50 to 100 per month (billing currency).
- After the confirming run, the environment is removed from the persistent
  layer, so the fee stops.

## Cost (Retail Prices API, Sweden Central, USD, read 2026-11-22)

- Dedicated Plan Management: 0.10 per hour while a dedicated profile exists in
  the environment, including at zero nodes. About 73 per month.
- D8 node while a run is active: 8 vCPU at 0.057077 plus 32 GiB at 0.004978 per
  hour, about 0.62 per hour.
- Microsoft Learn (Container Apps billing): "You aren't billed any plan
  management charges unless you use a Dedicated workload profile in your
  environment."

At 50 per month the 100% forecast alert would likely fire from the management
fee alone, which is stop condition 5. Hence the budget change.

## Alternatives rejected

- Keep the environment per run: no new parts, about 20 minutes of overhead per
  run. Rejected by the owner.
- Persistent consumption-only environment, with the pipeline adding the D8
  profile per run: no idle fee, but profile add and remove times are unknown
  and a leftover profile is not a tagged resource, so sweep could not find it
  by tag.
- Consumption profile for the job: at most 4 vCPU and 8 GiB per replica; the
  job needs 6.5 vCPU and 26 GiB.

## Side effect on diagnostics

In run 45049809770 the environment existed for several minutes without a
diagnostic setting. The diagnostics module's category lookups take the whole
`targets` map as input, so every setting waited for every target, including the
databases that failed. In the persistent layer the setting is created once,
right after the environment, and stays.

## How it is verified

- `terraform plan` (persistent): 2 to add (environment, diagnostic setting),
  1 to change (budget), 0 to destroy. Plan check passed.
- Not documented: whether `minimum_count = 0` is accepted for a dedicated
  profile. The persistent apply tests it.
- Not yet measured: how long the first job start waits for a D8 node from zero.
  The next spike run records it.

# 0001. Fully autonomous governance

Date: 2026-11-22
Decided by: the owner

## Context

The owner wants the build and the test runs to need no action from them. The
sibling repository `cdc-platform-azure` uses the opposite model: agents open
pull requests and stop, only the owner merges, and only the owner starts
anything that touches Azure.

Both repositories are public and deploy into a subscription shared with other
projects.

## Decision

Agents merge their own pull requests once CI is green, and dispatch the
workflows that create and destroy Azure resources. The owner reviews nothing.

## Alternatives rejected

- **The sibling repository's model.** Rejected by the owner because it is not
  hands-off.
- **A middle ground**: agents merge without review, and Azure is changed only
  by the pipeline. This was the design session's recommendation. The owner
  chose full autonomy instead.

## Consequences

- No human reads public code before it merges. The verification and truth
  rules in AGENTS.md carry the weight a reviewer would.
- Safeguards that do not need a human remain in force: the ephemeral layer is
  changed only by the pipeline, a budget alert exists before the first apply,
  every ephemeral resource carries a destroy deadline, and federation is bound
  to the `azure` environment on `main`.
- A short list of stop conditions in AGENTS.md is the only place an agent
  waits for the owner.
- One input is still required from the owner: the name of the Azure
  subscription, given once per session and never committed.

## How this is verified

Not applicable. This is a governance choice, not a technical claim.

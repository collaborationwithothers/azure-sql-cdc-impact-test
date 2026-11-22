# 0018. Three repeats of B and C instead of the 6 vCore confirming run

Date: 2026-11-22
Decided by: the owner, choosing the agent's recommended option. The owner
wants to cite the cost of adopting CDC, which needs an uplift that can be
told apart from noise.

## Plain terms

- A: the API updates the task row only.
- B: the API updates the task row and writes the event to the outbox table
  (no CDC).
- C: B plus CDC on the outbox table and Debezium publishing the changes.
- C minus B is the cost of adopting CDC.

## Decision

- Quick run (4 vCores): repeat 1 runs A, B and C; repeats 2 and 3 run B and C.
  Blocked by repeat, so slow platform drift affects B and C alike.
- Purge and CDC cleanup are measured in repeat 1 only (`phasesEveryRepeat`
  false), to fit the day.
- The 6 vCore confirming run (design section 2, fixed input) is not run today.
- The job's replica timeout now follows the workflow's `timeout_minutes`;
  it was fixed at 3 hours.

## Why

With one repeat, AGENTS.md's truth rules forbid an uplift figure, because
noise cannot be told from effect. Three repeats of B and C give a spread per
step. The confirming run answers whether a bigger pool removes a binding
limit; at 100 to 500 transactions per second (decision 0017) a limit may not
bind at all, so it adds less to the owner's claim than the repeats.

## Not covered

- Whether 6 vCores changes anything. Any statement about another pool size
  is an extrapolation and labelled as one.
- Spread of the purge and cleanup figures (one measurement each).

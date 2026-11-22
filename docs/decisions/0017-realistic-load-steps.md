# 0017. Load steps 100 to 500 transactions per second

Date: 2026-11-22
Decided by: the owner ("lets stick to 100 task updates per sec, 200, 300,
400, 500 and capture the impact for those; 1600 is not realistic in my view").

## Decision

- Quick run steps on database 1: 100, 200, 300, 400, 500 transactions per
  second, for configurations A, B and C. Replaces design section 7's
  100, 200, 400, 800, 1,200, 1,600.
- Confirming run (configuration C, 6 vCores): the same five steps. Design
  decision D4 asked for the three steps around the 4 vCore limit; if no limit
  binds by 500 there is no such step, so the same range is used and the
  comparison is headroom at equal load.
- Step length is unchanged: 1 minute warm-up, 2 minutes measured.

## Consequences

- The report answers "how much of each limit does CDC use at realistic load",
  not "at what load does a limit bind". If nothing binds by 500, the report
  says so and gives headroom per limit at 500; any figure above 500 is an
  extrapolation and labelled as one.
- The quick run is shorter: 5 steps x 3 configurations x 3 minutes = 45
  minutes of steps instead of 54.
- Inference to check against the run: the spike recorded a per-database log
  limit of 9 MiB/s at the 2 vCore cap. If log per transaction is near the
  spike's figure, 500 tps could approach that cap in configuration C.

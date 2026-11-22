# What switching on CDC costs: plain-language summary

Run 45062722769-1.

We tested an API that updates a task record and, in the same transaction, saves a JSON event of 4,096 characters (about 8 KB as stored, because the column type nvarchar(max) uses 2 bytes per character) to an outbox table. The databases sit in an Azure SQL elastic pool of 4 vCores, General Purpose, zone redundant, in Sweden Central. Each database is capped at 2 vCores; the application database holds 10.0 GiB of task data, and the neighbouring database in the same pool takes a steady 50 updates per second throughout. CDC is SQL Server's built-in change log: it copies every change to the outbox into a change table, so that a reader, here Debezium, can publish it. Each load level ran for 2 minutes after a 1-minute warm-up, in 3 rounds without CDC and 3 with CDC.

## What this means

- At 400 updates per second, the highest load handled both without and with CDC, CDC added: log written per update +10.0 KB, +65%; pool log write rate +15.2 percentage points of the pool's limit, +66%; time held back by the pool's log write limit +897 ms per second. Not distinguishable: CPU used within run-to-run variation; log write rate within run-to-run variation; response time, 95th percentile within run-to-run variation; data read and write within run-to-run variation (run 45062722769-1).
- Without CDC, the database first stopped coping at 500 updates per second: the database's worker thread limit was hit (100% of it on average); worker threads pile up when updates are already waiting on something else. At 400 updates per second the resource closest to its limit was the database's data read and write (IO) limit (94% in the busiest round) (run 45062722769-1).
- With CDC, the database first stopped coping at 500 updates per second: the database's worker thread limit was hit (100% of it on average); worker threads pile up when updates are already waiting on something else. At 400 updates per second the resource closest to its limit was the database's data read and write (IO) limit (86% in the busiest round) (run 45062722769-1).
- At 100 updates per second, CDC's change table holds about 221.0 GB when changes are kept for the default 3 days (8,527 bytes per change, measured, times 100 per second times 3 days) (run 45062722769-1).
- With CDC, running 400 updates per second all day, every day would add at most about 573.59 USD a month (estimate, upper bound; at least one pool size is extrapolated from documented limits): no extra compute, because the same pool size handles both, plus 573.59 USD of backup storage for the extra log, which is 0 if the pool's backups stay inside the free allowance. Not included: data storage for the change table (run 45062722769-1).

## The measurements

How to read the tables. Each figure is the average over the rounds that ran both without and with CDC (where only one side ran, over its own rounds). "Added by CDC" is with minus without; the brackets give its lowest and highest value in a single round. "Within run-to-run variation" means the difference is not larger than how much the same setup varied from round to round, so it cannot be told apart from chance. KB = 1,000 bytes, GB = 10^9 bytes.

### Handled the load?

Whether the application database kept up with the requested updates per second: at least 95% of them done, with no errors and nothing dropped. A "no" means that load is too much for this setup.

| Load (updates per second) | Without CDC | With CDC | Note |
| --- | --- | --- | --- |
| 100 | yes | yes |  |
| 200 | yes | yes |  |
| 300 | yes | yes |  |
| 400 | yes | yes |  |
| 500 | no: 449.4 of 500 per second, 34 errors, 1,147 dropped (round 1, 2) | no: 440.8 of 500 per second, 77 errors, 2,078 dropped (round 1, 2) | overloaded, see note |

Note: overloaded: compares two overloaded systems, not the steady cost of CDC. At such a load at least one side could not keep up, so its figures describe a system under strain.

### Response time, 95th percentile (ms)

How long the slowest 5 in 100 updates took, in milliseconds; this is the delay the API's users notice when the system is busy.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 16.58 | 14.01 | within run-to-run variation |
| 200 | 15.90 | 19.62 | within run-to-run variation |
| 300 | 23.02 | 20.29 | within run-to-run variation |
| 400 | 34.85 | 34.34 | within run-to-run variation |
| 500 (overloaded, see note) | 5,596.72 | 5,673.84 | within run-to-run variation |

### Log written per update (KB)

How much transaction log, the database's record of every change used for recovery and backups, each update writes; CDC can add to it, because copying each change into the change table is itself a logged write.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 16.2 | 26.0 | +9.8 [9.0 to 10.6] |
| 200 | 16.1 | 25.7 | +9.6 [9.3 to 10.1] |
| 300 | 15.6 | 25.1 | +9.5 [9.4 to 9.9] |
| 400 | 15.3 | 25.3 | +10.0 [9.4 to 10.3] |
| 500 (overloaded, see note) | 14.7 | 24.5 | +9.8 [8.9 to 10.4] |

### Log write rate (% of the database's limit)

How close the application database is to its cap on log writing speed; near 100% the database holds updates back until the log catches up, so they slow down.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 13.8 | 13.4 | within run-to-run variation |
| 200 | 27.4 | 26.7 | within run-to-run variation |
| 300 | 40.9 | 39.8 | within run-to-run variation |
| 400 | 53.9 | 52.6 | within run-to-run variation |
| 500 (overloaded, see note) | 63.4 | 61.9 | within run-to-run variation |

### Pool log write rate (% of the pool's limit)

How close the whole pool is to its shared cap on log writing speed. CDC's own copying into the change table runs as an internal SQL process, so its log writes count here, at pool level, and not in the database's own log write rate above.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 8.0 | 11.7 | +3.7 [3.4 to 3.9] |
| 200 | 13.1 | 20.4 | +7.2 [7.1 to 7.4] |
| 300 | 18.2 | 29.3 | +11.1 [11.0 to 11.4] |
| 400 | 23.1 | 38.3 | +15.2 [14.9 to 15.4] |
| 500 (overloaded, see note) | 26.5 | 44.7 | +18.2 [14.5 to 21.5] |

### Time held back by the pool's log write limit (ms per second)

For each second of the measured window, how many milliseconds of waiting, summed over all waiting tasks in the pool, were spent held back by the pool's log write limit (wait type POOL_LOG_RATE_GOVERNOR). 0 means the limit never held anything back. Several tasks can wait at once, so the figure can exceed 1,000.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 0 | 0 | within run-to-run variation |
| 200 | 0 | 0 | within run-to-run variation |
| 300 | 0 | 33 | within run-to-run variation |
| 400 | 0 | 897 | +897 [697 to 1,191] |
| 500 (overloaded, see note) | 0 | 1,707 | +1,707 [1,611 to 1,823] |

### Data read and write (IO) used (% of the database's limit)

How close the application database is to its cap on reading and writing data pages; near 100% updates wait for the disk.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 21.8 | 20.0 | within run-to-run variation |
| 200 | 43.1 | 39.1 | within run-to-run variation |
| 300 | 62.8 | 57.9 | within run-to-run variation |
| 400 | 81.4 | 75.8 | within run-to-run variation |
| 500 (overloaded, see note) | 95.6 | 91.1 | within run-to-run variation |

### CPU used (% of the database's limit)

How much of the application database's processing allowance (its vCore cap) the updates used; near 100% the database cannot work any faster.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 7.6 | 8.0 | +0.4 [0.1 to 0.7] |
| 200 | 13.4 | 14.0 | within run-to-run variation |
| 300 | 18.8 | 20.2 | +1.4 [0.5 to 1.9] |
| 400 | 24.2 | 25.8 | within run-to-run variation |
| 500 (overloaded, see note) | 30.4 | 33.0 | within run-to-run variation |

### Neighbouring database response time, 95th percentile (ms)

How long the other database in the same pool took for its own steady updates; it shows whether switching on CDC in one database slows down its neighbours.

| Load (updates per second) | Without CDC | With CDC | Added by CDC |
| --- | --- | --- | --- |
| 100 | 15.28 | 14.44 | within run-to-run variation |
| 200 | 15.19 | 25.69 | within run-to-run variation |
| 300 | 30.24 | 15.25 | within run-to-run variation |
| 400 | 13.86 | 26.87 | +13.01 [7.33 to 19.20] |
| 500 (overloaded, see note) | 75.04 | 218.60 | within run-to-run variation |

### Storage and cost

How much space CDC's change table takes when changes are kept for the default 3 days, and an estimate of what CDC adds to the monthly bill.

| Load | Change table kept for 3 days (GB) | Extra monthly cost (USD) |
| --- | --- | --- |
| 100 | 221.02 | 140.91 (estimate) |
| 200 | 442.09 | 276.63 (estimate) |
| 300 | 663.26 | 411.81 (estimate) |
| 400 | 884.28 | 573.59 (estimate) |
| 500 (overloaded, see note) | 1,105.41 | not available |

## How sure are we

- Each load level ran in 3 rounds. The brackets show the range across rounds. 3 rounds give a rough idea of run-to-run variation, not a statistical guarantee.
- Measured: every table except "Storage and cost", read from the databases and the load generator during the run. Calculated: the change-table size (measured bytes per change, times the load, times 3 days). Extrapolated: the monthly cost, which assumes the load runs all day, every day, takes unmeasured pool sizes from documented limits, and counts backup storage at its upper bound.
- Limitations: the task data fits mostly in the pool's memory, so the database reads from disk less than a production database would. One pool size (4 vCores) was measured; other sizes are extrapolated. Prices are pay-as-you-go list prices for compute and backup storage only; data storage, licence discounts and reservations are not included.

Glossary: **vCore**, a virtual CPU core, the unit Azure SQL sizes and prices compute in. **Elastic pool**, a group of databases that share one set of compute, memory and log limits. **CDC** (change data capture), SQL Server's built-in change log. **Debezium**, open-source software that reads CDC's change table and publishes each change as an event. **Outbox**, a table the application writes its outgoing events to in the same transaction as the business change, so an event exists exactly when the change does. **95th percentile**, the response time that 95 out of 100 requests beat.

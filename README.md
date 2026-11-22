# Azure SQL CDC impact test

An automated experiment that measures what change data capture (CDC) costs on
an Azure SQL elastic pool.

**Status: design only. Nothing is built and there are no results yet.**

## The question

CDC is a SQL Server feature that records every insert, update and delete on a
table into a separate change table, so other systems can read the changes.

Suppose a 4 vCore General Purpose elastic pool holds two databases. An
application updates a task row and, in the same transaction, writes a JSON
event into an outbox table. CDC is then enabled on that outbox table in one
database, and Debezium (an open source tool that reads CDC change tables and
publishes each change as an event) reads from it.

- Which resource limit is reached first, and at what load?
- How much extra log IO, data IO, CPU and storage does CDC add?
- Does the other database in the pool slow down?
- What pool size and monthly price does that imply?

## How it will work

- Terraform builds a pool, two databases and a load rig in Azure for each run,
  and destroys them afterwards.
- A load generator steps the transaction rate up a ladder in three
  configurations: no outbox, outbox only, and outbox with CDC.
- A collector records resource use from inside the databases.
- A report turns the measurements into uplift tables and a sizing table.

The full specification is [docs/design.md](docs/design.md).

## Reading the results

When results exist they will be published on the `results` branch. Every figure
will trace to a run. Figures in `docs/design.md` section 4 are estimates used
to plan the test. They are not results.

## For agents

Read [AGENTS.md](AGENTS.md) first, then [docs/design.md](docs/design.md).
Decision records are in [docs/decisions](docs/decisions).

## Licence

MIT. See [LICENSE](LICENSE).

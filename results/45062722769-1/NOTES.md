# Run 45062722769-1: notes

- Plain-language summary: `report/SUMMARY.md`. Technical appendix: `report/README.md`.
- Raw files: `raw/`, as uploaded by the pipeline after `scripts/sanitize.py`.
- Left out of `raw/`: `debezium.log` and `eventhubs_metrics.json`, because they contain GUID-shaped strings and the repository allows none. `eventhubs_metrics.json` from this run is also wrong: the workflow version used for it queried a window that did not cover the run (fixed in PR #16 for later runs), so it reads zero. Event Hubs delivery for this run is therefore not verified by Azure Monitor.
- Report generated from `main` at the commit that added this directory, with live Azure Retail Prices API prices queried at generation time (`report/prices.json`).
- Per-run resources were destroyed; `verify-clean` reported only persistent resources remaining.

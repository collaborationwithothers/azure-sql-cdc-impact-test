# Report generator: developer notes

`python -m report` turns the raw result files of one or more runs into
`README.md` plus PNG charts (design section 9). It reads only local files and,
unless told otherwise, the public Azure Retail Prices API.

## Run it

```bash
python3 -m venv .venv-report
.venv-report/bin/pip install -r report/requirements.txt
.venv-report/bin/python -m report results 45054291170-1 --out out/report
# Several runs (for example the quick run and the 6 vCore confirming run):
.venv-report/bin/python -m report results <quick-run-id> <confirm-run-id> --out out/report
# Offline, with a saved Retail Prices API response:
.venv-report/bin/python -m report results <run-id> --out out/report --prices-file prices.json
# No prices at all:
.venv-report/bin/python -m report results <run-id> --out out/report --no-prices
# Add a reader-chosen duty cycle (hours a day at the step load) to the storage and backup cost section:
.venv-report/bin/python -m report results <run-id> --out out/report --duty-hours 8
```

`results` holds one directory per run ID, as the `run-test` export step
downloads them (`results/<run_id>/*.jsonl`). The prices used are written to
`<out>/prices.json` (without meter IDs) so a report can be rebuilt offline.

Raw result files are untrusted input. When running against downloaded files,
run Python with `-I` from outside the results directory.

## Tests

```bash
.venv-report/bin/python -m pytest tests/report -q
```

Fixtures are synthetic and built in `tests/report/conftest.py`. Never copy real
result files into the repository: they can contain resource names.

## Hook for `run-test` (not wired in yet)

A step after "Export results" (which already sanitises the files) could run:

```bash
python3 -m venv /tmp/report-venv
/tmp/report-venv/bin/pip install -q -r report/requirements.txt
/tmp/report-venv/bin/python -I -c "import sys; sys.path.insert(0, '.'); from report.__main__ import main; sys.exit(main(sys.argv[1:]))" \
  results "$RUN_ID" --out "results/$RUN_ID/report" \
  || echo "Report not built" | tee -a "$GITHUB_STEP_SUMMARY"
```

The step needs no Azure credentials. It should run before the artifact upload
so the report travels with the raw files.

## Rules the code implements

Recorded in `docs/decisions/0016-report-rules.md`. In short: a step carries its
load at 95% of target with no errors or shed; a limit is reached at 90% of its
limit; uplift is suppressed when not larger than the jitter; any pool size a
run did not use is labelled EXTRAPOLATION. Limits come from
`docs/verification/2026-11-22-report-limits.md`.

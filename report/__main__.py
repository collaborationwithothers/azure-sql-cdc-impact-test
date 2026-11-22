"""python -m report <results_dir> <run_id> [<run_id> ...] --out <dir> [--prices-file <file>]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analysis, charts, prices, render, sizing, windows
from .load import load_run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m report", description="Builds the CDC impact report from raw run results.")
    p.add_argument("results_dir", help="directory holding one subdirectory per run ID")
    p.add_argument("run_ids", nargs="+", help="run IDs to include")
    p.add_argument("--out", required=True, help="output directory for README.md and the charts")
    p.add_argument("--prices-file", help="saved Retail Prices API response; skips the network call")
    p.add_argument("--no-prices", action="store_true", help="give no prices (no network call)")
    a = p.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [load_run(a.results_dir, r) for r in a.run_ids]
    steps = {r.run_id: windows.parse_steps(r) for r in runs}
    metrics = [m for r in runs for m in analysis.step_metrics(r, steps[r.run_id])]
    applied = {r.run_id: sizing.applied_limits(r) for r in runs}

    price_data, price_error = None, None
    if a.prices_file:
        price_data = prices.load_file(a.prices_file)
    elif not a.no_prices:
        try:
            price_data = prices.fetch()
        except Exception as e:  # noqa: BLE001 - the report says prices were not read
            price_error = f"{type(e).__name__}: {e}"
    else:
        price_error = "--no-prices was given"
    if price_data:
        (out / "prices.json").write_text(json.dumps(price_data, indent=1) + "\n")

    made = charts.render(metrics, out) if metrics else []
    text = render.Report(runs, metrics, steps, applied, price_data, price_error, made).build()
    (out / "README.md").write_text(text)
    print(f"report: {len(runs)} runs, {len(metrics)} steps, {len(made)} charts -> {out / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Report generator tests on synthetic fixtures (tests/report/conftest.py)."""
from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd
import pytest

from conftest import MIB, governance_row, write_run
from report import analysis as A
from report import limits as L
from report import prices as P
from report import sizing as S
from report import windows as W
from report.__main__ import main
from report.load import load_run

FIXTURES = Path(__file__).parent / "fixtures"


def build(results, steps, **kw):
    write_run(results, "run-1", steps, **kw)
    run = load_run(results, "run-1")
    st = W.parse_steps(run)
    return run, st, A.step_metrics(run, st)


# -- step-window matching ------------------------------------------------------

def test_resource_rows_only_inside_the_window(results):
    run, steps, _ = build(results, {"B": [(100, 100, 0, 0)]})
    df = W.resource_frame(run, "db_resource_stats")
    st = steps[0]
    rows = W.rows_in_window(df, "db1", st.start, st.end)
    # Rows end every 15 s from start+15 s; the 8 whose whole interval lies in the 120 s window count.
    assert len(rows) == 8
    assert rows["start_time"].min() >= st.start - W.TOLERANCE
    assert rows["end_time"].max() <= st.end + W.TOLERANCE


def test_resource_rows_deduplicated_across_polls(results):
    run, steps, _ = build(results, {"B": [(100, 100, 0, 0)]})
    # A second poll returning the same rows must not double count.
    for r in list(run.get("db_resource_stats")):
        run.streams["db_resource_stats"].append(r)
    df = W.resource_frame(run, "db_resource_stats")
    assert not df.duplicated(["database", "end_time"]).any()


def test_cumulative_delta_brackets_the_window():
    t0 = pd.Timestamp("2030-01-01T00:00:00Z")
    series = [(t0 + pd.Timedelta(seconds=10 * i), {"x": 100.0 * i}) for i in range(10)]
    d = W.delta(series, t0 + pd.Timedelta(seconds=15), t0 + pd.Timedelta(seconds=45))
    # Last poll at or before 15 s is 10 s; first at or after 45 s is 50 s.
    assert d.seconds == 40 and d.totals["x"] == 400 and d.covers_window
    assert d.rate("x") == pytest.approx(10.0)
    assert len(d.intervals) == 4


def test_cumulative_delta_partial_and_reset():
    t0 = pd.Timestamp("2030-01-01T00:00:00Z")
    series = [(t0 + pd.Timedelta(seconds=10 * i), {"x": 100.0 * i}) for i in range(3)]
    d = W.delta(series, t0 + pd.Timedelta(seconds=5), t0 + pd.Timedelta(seconds=60))
    assert d is not None and not d.covers_window
    reset = [(t0, {"x": 500.0}), (t0 + pd.Timedelta(seconds=10), {"x": 10.0})]
    assert W.delta(reset, t0, t0 + pd.Timedelta(seconds=10)) is None


def test_step_metrics_absolute_log_per_transaction(results):
    _, _, ms = build(results, {"B": [(100, 100, 0, 0)], "C": [(100, 100, 0, 0)]})
    by = {m.step.configuration: m for m in ms}
    assert by["B"].per_txn(by["B"].db1_log_bytes_s) == pytest.approx(9000)
    assert by["C"].per_txn(by["C"].db1_log_bytes_s) == pytest.approx(17000)
    assert by["C"].step.debezium_lag_ms == 400.0


def test_neighbour_windows_matched_to_step(results):
    run, _, ms = build(results, {"B": [(100, 100, 0, 0), (400, 400, 0, 0)]})
    n = {m.step.step_tps: m.neighbour for m in ms}
    assert n[100]["n"] == 12 and n[400]["n"] == 12
    assert n[400]["p99_ms"] == pytest.approx(14.0)
    base = A.neighbour_baseline(run)
    assert base["n"] == 3 and base["p99_ms"] == pytest.approx(6.0)


# -- carries and the binding-limit rule ------------------------------------------

@pytest.mark.parametrize("achieved,errors,shed,ok", [(95, 0, 0, True), (94.9, 0, 0, False), (100, 1, 0, False),
                                                      (100, 0, 3, False)])
def test_carries(achieved, errors, shed, ok):
    assert W.carries(achieved, 100, errors, shed) is ok


def test_binding_none_reached_below_threshold():
    b = A.binding_limit({("database 1", "CPU"): 50.0, ("pool", "log write"): 89.9}, {}, {})
    assert not b.reached and b.label == "none reached" and b.percent == 89.9


def test_binding_pool_log_confirmed_by_pool_governor():
    b = A.binding_limit({("database 1", "log write"): 80.0, ("pool", "log write"): 97.0}, {"LOG_RATE_GOVERNOR": 0.0},
                        {"POOL_LOG_RATE_GOVERNOR": 250.0})
    assert b.reached and b.label == "pool log write" and b.confirmed is True
    assert "POOL_LOG_RATE_GOVERNOR" in b.evidence


def test_binding_database_log_uses_database_waits():
    # The pool shows governor waits but the binding limit is the database's: confirmation needs LOG_RATE_GOVERNOR.
    b = A.binding_limit({("database 1", "log write"): 99.0}, {}, {"POOL_LOG_RATE_GOVERNOR": 10.0})
    assert b.reached and b.confirmed is None  # no database wait data at all
    b = A.binding_limit({("database 1", "log write"): 99.0}, {"LOG_RATE_GOVERNOR": 0.0}, {})
    assert b.confirmed is False


def test_binding_cpu_confirmed_by_scheduler_yield():
    b = A.binding_limit({("database 1", "CPU"): 95.0, ("pool", "CPU"): 60.0}, {"SOS_SCHEDULER_YIELD": 30.0}, {})
    assert b.label == "database 1 CPU" and b.confirmed is True


def test_binding_from_synthetic_run(results):
    _, _, ms = build(results, {"C": [(100, 100, 0, 0), (800, 700, 0, 0)]}, pool_log_pct_per_100=7.0,
                     db1_log_pct_per_100=2.0)
    hi = [m for m in ms if m.step.step_tps == 800][0]
    assert not hi.step.carries
    assert hi.binding.reached and hi.binding.scope == "pool" and hi.binding.limit == "log write"
    assert hi.binding.confirmed is True


# -- uplift with jitter suppression ------------------------------------------------

def test_uplift_figure_when_larger_than_jitter():
    u = A.uplift(A.Value(17000, 300), A.Value(9000, 200))
    assert u.suppressed is None and u.absolute == 8000 and u.percent == pytest.approx(88.888, rel=1e-3)


def test_uplift_suppressed_within_jitter():
    u = A.uplift(A.Value(10.4, 0.6), A.Value(10.0, 0.5))
    assert u.suppressed == "difference within jitter"


def test_uplift_suppressed_when_jitter_unknown():
    assert A.uplift(A.Value(2.0, math.nan), A.Value(1.0, 0.1)).suppressed.startswith("jitter unknown")


def test_uplift_rows_one_repeat_uses_within_window_spread(results):
    # CPU per 100 tps differs by less than the sample jitter: suppressed. Log per transaction differs a lot: given.
    _, _, ms = build(results, {"B": [(100, 100, 0, 0)], "C": [(100, 100, 0, 0)]}, cpu_jitter=2.0)
    rows = {r["metric"]: r for r in A.uplift_rows(ms) if r["pair"] == "C - B"}
    assert rows["database 1 CPU % per 100 tps"]["uplift"].suppressed == "difference within jitter"
    log = rows["log bytes per transaction (database 1 log file)"]["uplift"]
    assert log.suppressed is None and log.absolute == pytest.approx(8000)


def test_uplift_not_compared_when_a_configuration_did_not_carry(results):
    _, _, ms = build(results, {"B": [(100, 100, 0, 0)], "C": [(100, 90, 0, 0)]})
    rows = [r for r in A.uplift_rows(ms) if r["pair"] == "C - B"]
    assert len(rows) == 1 and rows[0]["metric"] is None


def test_uplift_with_repeats_uses_spread_across_repeats():
    # Two repeats measured 10 and 12: mean 11, jitter is the spread, 2. Within-window samples are ignored.
    v = A.config_value([10.0, 12.0], lambda m: (m, [0.0, 100.0]))
    assert v.value == 11.0 and v.jitter == 2.0
    assert A.uplift(A.Value(12.5, 0.1), v).suppressed == "difference within jitter"
    assert math.isnan(A.config_value([], lambda m: (1.0, [])).value)


# -- sizing -------------------------------------------------------------------

def _applied(results, run_id="run-1"):
    return {run_id: S.applied_limits(load_run(results, run_id))}


def test_identify_pool_from_recorded_limits():
    assert L.identify_pool(24 * MIB, 2800).vcores == 4
    assert L.identify_pool(62.5 * MIB, 9800).vcores == 14  # log rate alone is ambiguous here
    assert L.identify_pool(24 * MIB, 2801) is None


def test_sizing_measured_and_extrapolated_labels(results):
    _, _, ms = build(results, {"B": [(100, 100, 0, 0), (400, 400, 0, 0)], "C": [(100, 100, 0, 0), (400, 300, 0, 0)]},
                     pool_log_pct_per_100=10.0, db1_log_pct_per_100=5.0)
    applied = _applied(results)
    assert applied["run-1"].pool.vcores == 4
    costs = S.pick_costs(ms, applied)
    assert costs["C"].step_tps == 100 and costs["B"].step_tps == 400
    # B carried 400 at 4 vCores: the model may find a smaller size, which must be labelled EXTRAPOLATION.
    b400 = S.smallest(400, "B", ms, costs, applied)
    assert b400.vcores is not None
    if b400.vcores == 4:
        assert b400.measured and "measured" in b400.label
    else:
        assert b400.vcores < 4 and not b400.measured and "EXTRAPOLATION" in b400.label
    # C did not carry 400 at 4 vCores and reached no limit: no size is inferred.
    c400 = S.smallest(400, "C", ms, costs, applied)
    assert c400.vcores is None and "no resource limit reached" in c400.label
    # A load nobody measured is always extrapolated.
    c1600 = S.smallest(1600, "C", ms, costs, applied)
    assert "EXTRAPOLATION" in c1600.label and not c1600.measured


def test_sizing_measured_size_wins_when_model_has_no_smaller_size(results):
    # Pool log at 49% of the recorded 24 MiB/s: about 11.8 MiB/s, above 90% of a 2 vCore pool's 12 MiB/s.
    # The model rejects 2 vCores, so the answer is the measured 4.
    _, _, ms = build(results, {"B": [(400, 400, 0, 0)]}, pool_log_pct_per_100=12.0)
    applied = _applied(results)
    costs = S.pick_costs(ms, applied)
    cell = S.smallest(400, "B", ms, costs, applied)
    assert cell.vcores == 4 and cell.measured and cell.label == "4 vCores (measured, run run-1)"


def test_sizing_larger_pool_extrapolated_after_a_measured_limit(results):
    # C at 800 reaches the pool log limit at 4 vCores and is not carried: a larger size, labelled EXTRAPOLATION.
    _, _, ms = build(results, {"C": [(100, 100, 0, 0), (800, 700, 0, 0)]}, pool_log_pct_per_100=7.0,
                     db1_log_pct_per_100=2.0)
    applied = _applied(results)
    cell = S.smallest(800, "C", ms, S.pick_costs(ms, applied), applied)
    assert cell.vcores is not None and cell.vcores > 4 and not cell.measured and "EXTRAPOLATION" in cell.label


def test_sizing_database_cap_binds_for_every_pool(results):
    # Database 1 log write at 40% per 100 tps (C doubles it): 1,200 tps needs 960% of the per-database limit.
    _, _, ms = build(results, {"C": [(100, 100, 0, 0)]}, db1_log_pct_per_100=20.0)
    applied = _applied(results)
    costs = S.pick_costs(ms, applied)
    cell = S.smallest(1200, "C", ms, costs, applied)
    assert cell.vcores is None and "per-database cap" in cell.label and "EXTRAPOLATION" in cell.label


def test_sizing_model_uses_recorded_limits_for_the_measured_size():
    a = S.Applied("r", L.by_vcores(4), "", 2, 9 * MIB, 640, 200, 20 * MIB, 2800, 420, 36 * MIB)
    cost = S.Cost("r", "C", 100, {"CPU": 0.0, "log write": 1000.0, "data IO": 0.0},
                  {"CPU": 0.0, "log write": 200_000.0, "data IO": 0.0}, {"CPU": 0.0, "log write": 0.0, "data IO": 0.0}, a)
    # 100 tps x 200,000 B = 19.07 MiB/s: above 90% of the recorded 20 MiB/s, below 90% of the documented 24.
    ok, reason = S.model(cost, 100, L.by_vcores(4))
    assert not ok and reason == "pool log write"
    assert S.model(cost, 100, L.by_vcores(6))[0]


# -- prices --------------------------------------------------------------------

def test_price_parsing_from_saved_sample():
    data = P.load_file(str(FIXTURES / "prices_sample.json"))
    prices, notes = P.parse(data, [2, 4, 6, 8, 10])
    assert sorted(prices) == [2, 4, 6, 8]
    assert prices[2].compute_hourly == 0.30  # the 2025 price, not the superseded 2021 one
    assert prices[4].hourly == pytest.approx(1.0)
    assert prices[4].monthly == pytest.approx(730.0)
    assert prices[4].currency == "USD"
    assert any(n.startswith("10 vCores: no compute price") for n in notes)


def test_price_conflict_is_an_error():
    item = {"productName": P.PRODUCT, "unitOfMeasure": "1 Hour", "type": "Consumption", "meterName": "vCore",
            "skuName": "4 vCore", "effectiveStartDate": "2025-06-01T00:00:00Z", "currencyCode": "USD"}
    data = {"Items": [{**item, "retailPrice": 1.0}, {**item, "retailPrice": 2.0}]}
    with pytest.raises(ValueError):
        P.parse(data, [4])


def test_price_filter_names_region_product_and_consumption():
    f = P.odata_filter()
    assert "armRegionName eq 'swedencentral'" in f and "priceType eq 'Consumption'" in f and P.PRODUCT in f


# -- catch-up and the CLI ---------------------------------------------------------------

def test_catchup_flagged_when_change_table_is_behind(results):
    run, steps, _ = build(results, {"C": [(100, 100, 0, 0)]}, catchup_rows=17000)
    c = A.catchup_checks(run, steps)[0]
    assert c["expected_rows"] == 18000 and c["early"]


def test_cli_end_to_end(results, tmp_path):
    write_run(results, "run-1", {"A": [(100, 100, 0, 0)], "B": [(100, 100, 0, 0), (400, 400, 0, 0)],
                                 "C": [(100, 100, 0, 0), (400, 300, 0, 0)]})
    out = tmp_path / "out"
    assert main([str(results), "run-1", "--out", str(out), "--prices-file", str(FIXTURES / "prices_sample.json")]) == 0
    text = (out / "README.md").read_text()
    first = text.split("## Runs")[0]
    assert "## Answer" in first and "Configuration C" in first
    assert "noise cannot be told from effect" in text
    assert "Run run-1" in text and "EXTRAPOLATION" in text
    assert "CDC monthly difference (C - B)" in text
    assert (out / "avg_log_write_percent.png").exists() and (out / "prices.json").exists()
    assert text.isascii(), "ASCII punctuation only"
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", text)


def test_report_never_prints_governance_identifiers(results, tmp_path):
    w = write_run(results, "run-1", {"C": [(100, 100, 0, 0)]})
    row = {**governance_row("db1"), "server_name": "secret-server-name", "slo_name": "SECRET_SLO",
           "logical_database_guid": "01234567-89ab-cdef-0123-456789abcdef"}  # synthetic, allow-guid
    w.rec("governance", pd.Timestamp("2030-01-01T00:00:01Z").to_pydatetime(), {"database": "db1", "rows": [row]}, phase="setup")
    out = tmp_path / "out"
    main([str(results), "run-1", "--out", str(out), "--no-prices"])
    text = (out / "README.md").read_text()
    assert "secret-server-name" not in text and "SECRET_SLO" not in text and "01234567-89ab" not in text
    assert "Prices were not read" in text


def test_malformed_lines_are_reported_not_fatal(results):
    write_run(results, "run-1", {"B": [(100, 100, 0, 0)]})
    with open(results / "run-1" / "steps.jsonl", "a") as f:
        f.write("{not json\n")
    run = load_run(results, "run-1")
    assert any("not valid JSON" in w for w in run.warnings)
    assert len(W.parse_steps(run)) == 1

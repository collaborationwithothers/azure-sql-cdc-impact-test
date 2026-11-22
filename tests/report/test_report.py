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
from report import storage as ST
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


# -- storage and backup cost of CDC ---------------------------------------------------

QUICK = {1: ["A", "B", "C"], 2: ["B", "C"], 3: ["B", "C"]}  # decision 0018
SPREAD = {1: 1.0, 2: 1.02, 3: 0.98}


def quick(results, **kw):
    steps = {"A": [(100, 100, 0, 0)], "B": [(100, 100, 0, 0)], "C": [(100, 100, 0, 0)]}
    return build(results, steps, repeats=3, configs_by_repeat=QUICK, log_scale_by_repeat=SPREAD, **kw)


def test_bytes_per_change_row_from_growth_across_the_window(results):
    run, steps, _ = build(results, {"B": [(100, 100, 0, 0)], "C": [(100, 100, 0, 0), (200, 200, 0, 0)]},
                          ct_bytes_per_row=8600)
    items = ST.change_rows(run, steps)
    assert [c.step.configuration for c in items] == ["C", "C"]  # B steps give no change rows
    assert all(c.bytes_per_row == pytest.approx(8600) for c in items)
    assert [c.rows_per_s for c in items] == [pytest.approx(100), pytest.approx(200)]
    per = ST.bytes_per_row(items)
    assert per[100].mean == pytest.approx(8600) and per[100].n == 1


def test_change_table_shrinking_in_the_window_gives_no_figure():
    t0 = pd.Timestamp("2030-01-01T00:00:00Z")
    # The cleanup job removed rows between the bracketing polls: no valid growth.
    series = [(t0, {"reserved": 9e8, "rows": 1e5}), (t0 + pd.Timedelta(seconds=10), {"reserved": 5e8, "rows": 5e4})]
    assert W.delta(series, t0, t0 + pd.Timedelta(seconds=10)) is None
    assert ST.ct_values([{"schema_name": "dbo", "table_name": "Outbox", "reserved_bytes": 1, "row_count": 1}]) == {}


def test_retention_arithmetic():
    assert ST.CDC_RETENTION_MINUTES == 4320  # three days, Microsoft Learn (verification file)
    # 8,600 bytes per row x 100 rows/s x 259,200 s = 222.9 GB.
    assert ST.retained_bytes(8600, 100, ST.CDC_RETENTION_MINUTES * 60) / ST.GB == pytest.approx(222.912)
    # 8,000 extra log bytes per transaction at 100 tps for a day = 69.12 GB; 8 hours a day = 23.04 GB.
    assert ST.log_gb_per_day(8000, 100) == pytest.approx(69.12)
    assert ST.log_gb_per_day(8000, 100, 8) == pytest.approx(23.04)
    assert ST.backup_gb(69.12, ST.PITR_RETENTION_DAYS + ST.PITR_EXTRA_DAYS) == pytest.approx(967.68)


def test_log_c_minus_b_uses_all_repeats_with_spread(results):
    _, _, ms = quick(results)
    (d,) = ST.log_diffs(ms)
    assert d.repeats == [1, 2, 3]
    assert d.b.value == pytest.approx(9000) and d.c.value == pytest.approx(17000)
    assert d.uplift.absolute == pytest.approx(8000) and d.uplift.suppressed is None
    # Spread across repeats: B 8,820 to 9,180 (360), C 16,660 to 17,340 (680); jitter is the larger.
    assert d.uplift.jitter == pytest.approx(680)
    assert d.paired_range.low == pytest.approx(7840) and d.paired_range.high == pytest.approx(8160)


def test_pairs_with_a_use_repeat_1_only(results):
    _, _, ms = quick(results)
    rows = A.uplift_rows(ms)
    ba = [r for r in rows if r["pair"] == "B - A"]
    cb = [r for r in rows if r["pair"] == "C - B"]
    assert ba and all(r["repeats"] == 1 and r["scope"].startswith("repeat 1 only") for r in ba)
    assert cb and all(r["repeats"] == 3 and r["scope"] == "" for r in cb)
    log = next(r for r in ba if r["metric"].startswith("log bytes"))
    assert log["x"].value == pytest.approx(9000) and log["y"].value == pytest.approx(500)  # B repeat 1, not the mean


def test_log_counter_reset_drops_that_repeat(results):
    run, steps, _ = quick(results)
    st = next(s for s in steps if s.configuration == "C" and s.repeat == 2)
    hit = 0
    for r in run.streams["file_stats"]:
        if r["data"]["database"] == "db1" and pd.Timestamp(r["ts"]) == st.end:
            for row in r["data"]["rows"]:
                if row["type_desc"] == "LOG":
                    row["num_of_bytes_written"] = 0.0  # the counter restarted inside the window
                    hit += 1
    assert hit == 1
    (d,) = ST.log_diffs(A.step_metrics(run, steps))
    assert d.repeats == [1, 3] and "repeat 2" in d.note
    assert d.paired_range.n == 2 and d.c.value == pytest.approx((17000 + 16660) / 2)


def test_storage_price_parsing_of_new_meters():
    sp = P.parse_storage(P.load_file(str(FIXTURES / "prices_storage_sample.json")))
    assert sp.backup_zrs.per_gb_month == 0.25  # the 2025 price, not the superseded one; not the LTR meter
    assert sp.backup_zrs.sku == "Backup ZRS" and sp.backup_zrs.currency == "USD"
    assert sp.data.per_gb_month == 0.12 and sp.data_zone_redundancy.per_gb_month == 0.24
    none = P.parse_storage(P.load_file(str(FIXTURES / "prices_sample.json")))
    assert none.backup_zrs is None and none.data is None
    f = P.storage_filter()
    assert P.BACKUP_PRODUCT in f and P.STORAGE_PRODUCT in f and "priceType eq 'Consumption'" in f


def test_not_priced_unverified_path(monkeypatch):
    sp = P.parse_storage(P.load_file(str(FIXTURES / "prices_storage_sample.json")))
    value, note = ST.price("data storage", sp.data_zone_redundancy)
    assert value is None and note.startswith("not priced: unverified")
    assert ST.price("backup", sp.backup_zrs) == (0.25, "")
    assert ST.price("backup", None)[1] == "not priced: no price for this meter in the API response"
    assert ST.price("something else", sp.data)[0] is None
    monkeypatch.setitem(ST.UNVERIFIED, "backup", "test reason")
    assert ST.price("backup", sp.backup_zrs) == (None, "not priced: unverified (test reason)")


def test_cli_storage_section(results, tmp_path):
    write_run(results, "run-1", {"A": [(100, 100, 0, 0)], "B": [(100, 100, 0, 0)], "C": [(100, 100, 0, 0)]},
              repeats=3, configs_by_repeat=QUICK, log_scale_by_repeat=SPREAD)
    out = tmp_path / "out"
    assert main([str(results), "run-1", "--out", str(out), "--prices-file", str(FIXTURES / "prices_storage_sample.json"),
                 "--duty-hours", "8"]) == 0
    text = (out / "README.md").read_text()
    sec = text.split("## Storage and backup cost of CDC")[1].split("\n## ")[0]
    assert "222.91" in sec  # 8,600 bytes x 100 rows/s x 3 days
    assert "69.12" in sec  # extra log GB per day at 100 tps
    # 69.12 GB/day held 7 to 14 days = 483.84 to 967.68 GB, at 0.25 per GB-month.
    assert "120.96 to 241.92" in sec
    assert "not priced: unverified" in sec and "EXTRAPOLATION" in sec
    assert "h/day (assumption)" in sec
    assert "repeat 1 only" in text  # pairs with A say so
    assert text.isascii()


def test_cli_storage_section_without_prices(results, tmp_path):
    write_run(results, "run-1", {"C": [(100, 100, 0, 0)]})
    out = tmp_path / "out"
    main([str(results), "run-1", "--out", str(out), "--no-prices"])
    sec = (out / "README.md").read_text().split("## Storage and backup cost of CDC")[1].split("\n## ")[0]
    assert "needs configurations B and C" in sec and "not priced: prices were not read" in sec


# -- SUMMARY.md: plain-language summary --------------------------------------------------

LADDER = [(100, 100, 0, 0), (200, 200, 0, 0), (300, 300, 0, 0), (400, 400, 0, 0)]
BANNED = (r"\bdatabase [12]\b", r"\bconfigurations? [abc]\b", r"\bdb1\b", r"\bdb2\b", r"\brepeat\w*",
          r"\bgoverned\b", r"\buplift\w*", r"\bjitter\b")


def _summary(results, tmp_path, steps, *args, **kw):
    write_run(results, "run-1", steps, **kw)
    out = tmp_path / "out"
    assert main([str(results), "run-1", "--out", str(out), *args]) == 0
    readme, summary = (out / "README.md").read_text(), (out / "SUMMARY.md").read_text()
    assert summary.isascii() and readme.isascii()
    for pattern in BANNED:
        assert not re.search(pattern, summary, re.IGNORECASE), pattern
    assert "[SUMMARY.md](SUMMARY.md)" in readme.split("## Answer")[0]
    for line in summary.splitlines():
        if line.startswith("|"):
            assert len(line.strip().strip("|").split("|")) <= 5, line
    return readme, summary


def _row(text: str, load: str) -> list[str]:
    line = next(x for x in text.splitlines() if x.startswith(f"| {load} |"))
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _section(summary: str, heading: str) -> str:
    return summary.split(f"### {heading}\n")[1].split("\n#")[0]


HANDLED, P95, LOG, LOGRATE, CPU = ("Handled the load?", "Response time, 95th percentile (ms)",
                                   "Log written per update (KB)", "Log write rate (% of the database's limit)",
                                   "CPU used (% of the database's limit)")
NEIGH, STORAGE = "Neighbouring database response time, 95th percentile (ms)", "Storage and cost"
POOLLOG, POOLWAIT = "Pool log write rate (% of the pool's limit)", "Time held back by the pool's log write limit (ms per second)"
DATAIO = "Data read and write (IO) used (% of the database's limit)"


def test_summary_quick_run_with_overloaded_step(results, tmp_path):
    steps = {"A": LADDER + [(500, 500, 0, 0)], "B": LADDER + [(500, 500, 0, 0)], "C": LADDER + [(500, 400, 12, 0)]}
    readme, s = _summary(results, tmp_path, steps, "--prices-file", str(FIXTURES / "prices_storage_sample.json"),
                         repeats=3, configs_by_repeat=QUICK, log_scale_by_repeat=SPREAD)
    # Opening: every number from the run's data.
    assert "elastic pool of 4 vCores, General Purpose, zone redundant, in Sweden Central" in s
    assert "capped at 2 vCores" in s and "10.0 GiB of task data" in s and "steady 50 updates per second" in s
    assert "4,096 characters" in s and "2 minutes after a 1-minute warm-up" in s
    assert "3 rounds without CDC and 3 with CDC" in s
    # "What this means" comes before the tables, in the order the owner asked for.
    order = ["## What this means"] + [f"### {h}" for h in (HANDLED, P95, LOG, LOGRATE, POOLLOG, POOLWAIT, DATAIO, CPU, NEIGH, STORAGE)]
    assert [s.index(h) for h in order] == sorted(s.index(h) for h in order)
    assert _row(_section(s, HANDLED), "100") == ["100", "yes", "yes", ""]
    # Log per update: 9.0 KB without, 17.0 KB with; per-round differences 8.0, 8.16, 7.84 KB.
    assert _row(_section(s, LOG), "100")[1:] == ["9.0", "17.0", "+8.0 [7.8 to 8.2]"]
    # CPU and response time are the same with and without CDC, with no spread: within run-to-run variation.
    assert _row(_section(s, CPU), "100")[3] == "within run-to-run variation"
    assert _row(_section(s, P95), "100")[3] == "within run-to-run variation"
    assert _row(_section(s, LOGRATE), "100")[1:] == ["10.0", "20.0", "+10.0 [10.0 to 10.0]"]
    assert _row(_section(s, NEIGH), "100")[1:3] == ["8.00", "8.00"]
    # Pool-level log: CDC's capture writes count only here. Each new table has a row per load.
    for h in (POOLLOG, POOLWAIT, DATAIO):
        assert len(_row(_section(s, h), "100")) == 4
    storage = _row(_section(s, STORAGE), "100")
    assert storage[1] == "222.91"
    # The overloaded load: full note once under the first table, short form on its row in every table.
    handled = _section(s, HANDLED)
    assert _row(handled, "500") == ["500", "yes", "no: 400.0 of 500 per second, 36 errors", "overloaded, see note"]
    assert handled.count("overloaded: compares two overloaded systems, not the steady cost of CDC") == 1
    for h in (P95, LOG, LOGRATE, CPU, NEIGH, STORAGE):
        assert "| 500 (overloaded, see note) |" in _section(s, h), h
    assert "At 400 updates per second, the highest load handled both without and with CDC" in s
    assert "Without CDC, the database handled every load tested, up to 500 updates per second (run run-1)." in s
    assert "With CDC, the database first stopped coping at 500 updates per second" in s
    assert "about 222.9 GB" in s and "## How sure are we" in s and "Glossary:" in s
    # Same functions as README.md: the change table and the monthly total agree.
    assert "222.91" in readme.split("### Change-table storage")[1]
    month = storage[2].split(" ")[0]
    monthly = readme.split("### Monthly cost difference with CDC")[1]
    assert month != "not" and _row(monthly, "100")[4] == month


def test_summary_within_variation_and_not_priced(results, tmp_path):
    # Log per update 9,000 without and 9,100 with, rounds 4% apart: the difference is within the variation.
    _, s = _summary(results, tmp_path, {"B": [(100, 100, 0, 0)], "C": [(100, 100, 0, 0)]}, "--no-prices",
                    repeats=3, log_bytes_per_txn={"B": 9000, "C": 9100}, log_scale_by_repeat=SPREAD)
    assert _row(_section(s, LOG), "100")[3] == "within run-to-run variation"
    assert _row(_section(s, STORAGE), "100")[2] == "not priced (prices were not read)"
    assert "not priced, because prices were not read" in s
    assert "overloaded" not in s


def test_summary_missing_figures_say_not_measured(results, tmp_path):
    _, s = _summary(results, tmp_path, {"C": [(200, 200, 0, 0)]}, "--no-prices")
    assert _row(_section(s, HANDLED), "200")[1:3] == ["not measured", "yes"]
    assert _row(_section(s, LOG), "200")[1] == "not measured" and _row(_section(s, LOG), "200")[3] == "not measured"
    assert _row(_section(s, NEIGH), "200")[1] == "not measured"
    assert _row(_section(s, STORAGE), "200")[2] == "not measured"
    assert "- Without CDC: not measured (run run-1)." in s
    assert "Change-table storage at 100 updates per second: not measured (run run-1)." in s
    assert "Added cost of CDC: not measured" in s
    assert "with CDC only (this run did not test the application without CDC)" in s

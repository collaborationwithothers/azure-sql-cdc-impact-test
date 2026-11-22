"""Unit tests for the orchestrator and collector. No database needed."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cdcrig import db, debezium, loadgen, settings
from cdcrig.orchestrator import _payload, stop_rule

REPO = Path(__file__).resolve().parents[2]


def test_quick_preset_matches_design_section_7():
    s = settings.resolve('{"mode":"quick"}')
    assert s.steps == [100, 200, 300, 400, 500]
    assert s.configurations == ["A", "B", "C"]
    assert s.repeats == 3 and s.laterRepeatConfigurations == ["B", "C"] and s.phasesEveryRepeat is False

    assert (s.warmupSeconds, s.measureSeconds, s.seedGb, s.database2Rate, s.stopRatio) == (60, 120, 10, 50, 0.95)
    assert s.payloadType == "nvarchar(max)"
    s.validate()


def test_given_keys_override_preset():
    s = settings.resolve('{"mode":"confirm","steps":[800,1200,1600],"runId":"r1"}')
    assert s.steps == [800, 1200, 1600] and s.configurations == ["C"] and s.runId == "r1"


def test_quick_rejects_shortened_window():
    with pytest.raises(ValueError):
        settings.resolve('{"mode":"quick","measureSeconds":60}').validate()


def test_unknown_mode_and_keys_rejected():
    with pytest.raises(ValueError):
        settings.resolve('{"mode":"nope"}')
    with pytest.raises(ValueError):
        settings.resolve('{"mode":"smoke","typo":1}')


def test_public_settings_hold_no_names():
    s = settings.resolve('{"mode":"smoke","sqlServer":"srv.database.windows.net","clientId":"abc","resultsAccount":"acct","eventHubsNamespace":"ns"}')
    text = json.dumps(s.public())
    for secret in ("srv.database.windows.net", "abc", "acct", '"ns"'):
        assert secret not in text


def test_debezium_properties_use_managed_identity_and_no_secret():
    s = settings.Settings(sqlServer="srv.database.windows.net", eventHubsNamespace="ns")
    p = debezium.properties(s)
    assert "debezium.source.driver.accessTokenCallbackClass=cdcimpact.SqlTokenCallback" in p
    assert "debezium.sink.kafka.producer.sasl.login.callback.handler.class=cdcimpact.EventHubsTokenHandler" in p
    assert "debezium.sink.kafka.producer.bootstrap.servers=ns.servicebus.windows.net:9093" in p
    assert "debezium.source.table.include.list=dbo.Outbox" in p
    assert "debezium.source.snapshot.mode=no_data" in p
    for bad in ("password", "connectionstring", "sharedaccesskey"):
        assert bad not in p.lower()


SAMPLE = """# HELP x
debezium_metrics_TotalNumberOfEventsSeen{context="streaming",server="cdc",task="0",database="cdcdb1",} 1234.0
debezium_metrics_MilliSecondsBehindSource{context="streaming",server="cdc",} 21500.0
debezium_metrics_Connected{context="streaming",server="cdc",} 1.0
jvm_memory_bytes_used{area="heap",} 1.0E8
"""


def test_metrics_parse_and_find():
    m = debezium.parse_metrics(SAMPLE)
    assert len(m) == 3
    assert debezium.find(m, "streaming", "TotalNumberOfEventsSeen") == 1234
    assert debezium.find(m, "milli_seconds_behind_source") == 21500
    assert debezium.find(m, "streaming", "Connected") == 1
    assert debezium.find(m, "snapshot", "Connected") is None


def test_sql_blocks():
    assert "sp_cdc_enable_table" in db.block("cdc.sql", "enable")
    assert "sp_cdc_disable_db" in db.block("cdc.sql", "disable")
    assert "sp_cdc_cleanup_change_table" in db.block("cdc.sql", "cleanup")
    assert "sp_cdc_cleanup_change_table" not in db.block("cdc.sql", "enable")
    assert "{{PAYLOAD_TYPE}}" in db.load("schema.sql")
    assert "CREATE OR ALTER PROCEDURE dbo.SeedTaskBatch" in db.load("seed-proc.sql")


def test_stop_rule():
    assert stop_rule({"achieved_rate": 94.9}, 100, 0.95)
    assert not stop_rule({"achieved_rate": 95.0}, 100, 0.95)


def test_topup_payload_matches_loadgen_length():
    assert len(_payload(4096)) == 4096
    assert _payload(4096).startswith('{"type":"TaskUpdated"')


def test_transaction_mapping():
    assert loadgen.TRANSACTION == {"A": "update", "B": "update-outbox", "C": "update-outbox"}


def test_odbc_to_sqlclient_conversion():
    cs = loadgen._sqlclient_from_odbc("Driver={ODBC Driver 18 for SQL Server};Server=tcp:sql,1433;Database=cdcdb1;UID=sa;PWD=x;TrustServerCertificate=yes")
    assert "Server=sql,1433" in cs and "Database=cdcdb1" in cs and "User ID=sa" in cs


def _plan(changes):
    return json.dumps({"resource_changes": changes})


def _run_plan_check(plan_json, *args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "plan_check.py"), "rg-x", *args], input=plan_json, capture_output=True, text=True)


@pytest.mark.skipif(not (REPO / "scripts" / "plan_check.py").exists(), reason="scripts not in image")
def test_plan_check_rejects_other_group_and_deletes():
    ok = _plan([{"address": "a", "type": "azurerm_storage_account", "change": {"actions": ["create"], "after": {"resource_group_name": "rg-x"}}}])
    assert _run_plan_check(ok).returncode == 0
    other = _plan([{"address": "b", "type": "azurerm_storage_account", "change": {"actions": ["create"], "after": {"resource_group_name": "rg-y"}}}])
    assert _run_plan_check(other).returncode == 1
    delete = _plan([{"address": "c", "type": "x", "change": {"actions": ["delete"], "after": None}}])
    assert _run_plan_check(delete).returncode == 1
    assert _run_plan_check(delete, "--allow-delete").returncode == 0
    scope = _plan([{"address": "d", "type": "azurerm_role_assignment", "change": {"actions": ["create"], "after": {"scope": "/subscriptions/s/resourceGroups/other"}}}])
    assert _run_plan_check(scope).returncode == 1


def test_later_repeat_configurations_must_be_a_subset():
    import pytest
    with pytest.raises(ValueError):
        settings.resolve('{"mode":"smoke","configurations":["C"],"laterRepeatConfigurations":["B"]}').validate()

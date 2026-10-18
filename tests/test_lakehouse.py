"""Tests for the lakehouse package. All use tmp_path; deterministic."""

import os

import pytest

from lakehouse.checks import (
    CheckViolation,
    expect_no_nulls,
    expect_range,
    expect_unique,
)
from lakehouse.maintenance import optimize, vacuum, z_order_hint
from lakehouse.schema import Schema, SchemaViolation
from lakehouse.table import DeltaTable
from lakehouse.time_travel import as_of, as_of_timestamp, diff, diff_by_key
from lakehouse.transactions import ConcurrentWriteError


def make_table(tmp_path, schema=None):
    return DeltaTable(str(tmp_path / "orders"), schema=schema)


ORDERS_SCHEMA = Schema({"id": "int", "item": "str", "amount": "float"})


# ------------------------------------------------------------- table/versions
def test_first_write_creates_version_0(tmp_path):
    t = make_table(tmp_path)
    v = t.write([{"id": 1}])
    assert v == 0
    assert t.current_version() == 0


def test_writes_create_new_immutable_versions(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    t.write([{"id": 2}])
    assert t.current_version() == 1
    # version 0 still has exactly the original row
    assert t.read(version=0) == [{"id": 1}]
    assert t.read() == [{"id": 1}, {"id": 2}]


def test_overwrite_mode_replaces_rows(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}, {"id": 2}])
    t.write([{"id": 9}], mode="overwrite")
    assert t.read() == [{"id": 9}]
    # old version intact
    assert t.read(version=0) == [{"id": 1}, {"id": 2}]


def test_history_lists_versions_oldest_first(tmp_path):
    t = make_table(tmp_path)
    t.write([{"a": 1}], operation="SEED")
    t.write([{"a": 2}], operation="APPEND")
    hist = t.history()
    assert [e["version"] for e in hist] == [0, 1]
    assert hist[0]["operation"] == "SEED"
    assert hist[1]["num_rows"] == 2


def test_unknown_mode_rejected(tmp_path):
    t = make_table(tmp_path)
    with pytest.raises(ValueError):
        t.write([{"a": 1}], mode="upsert")


def test_read_empty_table_returns_empty_list(tmp_path):
    t = make_table(tmp_path)
    assert t.read() == []


# --------------------------------------------------------------- transactions
def test_transaction_commits_atomically(tmp_path):
    t = make_table(tmp_path)
    with t.transaction() as txn:
        txn.write([{"id": 1}])
        txn.write([{"id": 2}])
    assert t.current_version() == 0  # one version, not two
    assert t.read() == [{"id": 1}, {"id": 2}]
    entry = t.history()[0]
    assert entry["operation"] == "TRANSACTION"
    assert entry["num_statements"] == 2


def test_transaction_rollback_on_exception(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    with pytest.raises(RuntimeError):
        with t.transaction() as txn:
            txn.write([{"id": 2}])
            txn.write([{"id": 3}])
            raise RuntimeError("boom")
    # No partial version: still exactly version 0 with the original row.
    assert t.current_version() == 0
    assert t.read() == [{"id": 1}]
    assert len(os.listdir(t.log_dir)) == 1


def test_concurrent_write_conflict_raises(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    txn = t.transaction()
    txn.__enter__()
    # Another writer commits while the transaction is open.
    t.write([{"id": 2}])
    txn.write([{"id": 3}])
    with pytest.raises(ConcurrentWriteError):
        txn.__exit__(None, None, None)
    # The failed transaction left nothing behind.
    assert t.read() == [{"id": 1}, {"id": 2}]


def test_concurrent_write_retry_succeeds(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    for _attempt in range(3):
        try:
            with t.transaction() as txn:
                txn.write([{"id": 99}])
                # Simulate a concurrent writer on the first attempt only.
                if _attempt == 0:
                    t2 = DeltaTable(t.path)
                    t2.write([{"id": 2}])
            break
        except ConcurrentWriteError:
            continue
    rows = t.read()
    assert {"id": 99} in rows
    assert {"id": 2} in rows


def test_write_with_expected_version_guard(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    with pytest.raises(ConcurrentWriteError):
        t.write([{"id": 2}], expected_version=99)
    t.write([{"id": 2}], expected_version=0)  # correct guard passes
    assert t.current_version() == 1


# -------------------------------------------------------------------- schema
def test_schema_enforced_on_write(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    t.write([{"id": 1, "item": "book", "amount": 9.5}])
    assert t.current_version() == 0


def test_schema_violation_wrong_type(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    with pytest.raises(SchemaViolation):
        t.write([{"id": "not-an-int", "item": "book", "amount": 9.5}])
    assert t.current_version() == -1  # rejected write left no version


def test_schema_violation_missing_column(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    with pytest.raises(SchemaViolation):
        t.write([{"id": 1, "item": "book"}])


def test_schema_violation_extra_column(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    with pytest.raises(SchemaViolation):
        t.write([{"id": 1, "item": "book", "amount": 1.0, "oops": 2}])


def test_schema_evolution_adds_column(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    t.write([{"id": 1, "item": "book", "amount": 9.5}])
    evolved = ORDERS_SCHEMA.evolve({"region": "str"})
    t2 = DeltaTable(t.path, schema=evolved)
    t2.write([{"id": 2, "item": "pen", "amount": 1.5, "region": "EU"}])
    assert t2.read()[-1]["region"] == "EU"
    # Old schema object unchanged.
    assert "region" not in ORDERS_SCHEMA.columns


def test_merge_schema_widens_compatible(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    widened = ORDERS_SCHEMA.merge_schema(
        [{"id": 1, "item": "x", "amount": 1.0, "region": "US"}]
    )
    assert widened.columns["region"] == "str"
    t2 = DeltaTable(t.path, schema=widened)
    t2.write([{"id": 1, "item": "x", "amount": 1.0, "region": "US"}])
    assert t2.current_version() == 0


def test_bool_rejected_for_int_column(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA)
    with pytest.raises(SchemaViolation):
        t.write([{"id": True, "item": "book", "amount": 1.0}])


# ---------------------------------------------------------------- time travel
def test_as_of_version(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    t.write([{"id": 2}])
    assert as_of(t, 0) == [{"id": 1}]
    assert as_of(t, 1) == [{"id": 1}, {"id": 2}]


def test_as_of_timestamp(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    ts_v0 = t.version_timestamp(0)
    t.write([{"id": 2}])
    # Querying at v0's exact commit timestamp resolves to version 0.
    assert as_of_timestamp(t, ts_v0) == [{"id": 1}]
    # Far-future timestamp resolves to the latest version.
    assert as_of_timestamp(t, ts_v0 + 10**6) == [{"id": 1}, {"id": 2}]
    with pytest.raises(ValueError):
        as_of_timestamp(t, 0.0)  # before any commit


def test_diff_added_removed(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}, {"id": 2}])
    t.write([{"id": 3}], mode="overwrite")
    d = diff(t, 0, 1)
    assert d["added_count"] == 1 and d["removed_count"] == 2
    assert d["added"] == [{"id": 3}]


def test_diff_by_key_reports_changed(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1, "status": "new"}, {"id": 2, "status": "new"}])
    t.write(
        [{"id": 1, "status": "shipped"}, {"id": 2, "status": "new"}],
        mode="overwrite",
    )
    d = diff_by_key(t, 0, 1, key="id")
    assert d["changed_count"] == 1
    change = d["changed"][0]
    assert change["key"] == 1
    assert change["before"]["status"] == "new"
    assert change["after"]["status"] == "shipped"


# --------------------------------------------------------------- maintenance
def test_optimize_compacts_files(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    t.write([{"id": 2}])
    report = optimize(t)
    assert report["operation"] == "OPTIMIZE"
    assert report["files_after"] <= report["files_before"]
    # Data unchanged.
    assert t.read() == [{"id": 1}, {"id": 2}]
    assert t.read(version=0) == [{"id": 1}]
    # Maintenance action recorded.
    with open(os.path.join(t.path, "_maintenance_log.json")) as f:
        log = __import__("json").load(f)
    assert log[-1]["operation"] == "OPTIMIZE"


def test_vacuum_deletes_old_versions(tmp_path):
    t = make_table(tmp_path)
    for i in range(4):
        t.write([{"id": i}])
    report = vacuum(t, retain_versions=2)
    assert report["removed_versions"] == [0, 1]
    assert t.read() == [{"id": i} for i in range(4)]  # latest intact
    assert t.read(version=3) == [{"id": i} for i in range(4)]
    # Time travel past the horizon fails.
    from lakehouse.table import VersionNotAvailable
    with pytest.raises(VersionNotAvailable):
        t.read(version=0)


def test_vacuum_rejects_zero_retention(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    with pytest.raises(ValueError):
        vacuum(t, retain_versions=0)


def test_z_order_hint_recorded_as_metadata(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    hint = z_order_hint(t, ["id"])
    assert hint["columns"] == ["id"]
    assert hint["hint_only"] is True
    assert os.path.exists(os.path.join(t.path, "_zorder.json"))


# -------------------------------------------------------------------- checks
def test_no_nulls_check_rejects(tmp_path):
    t = make_table(tmp_path).register_checks(expect_no_nulls("item"))
    with pytest.raises(CheckViolation) as exc:
        t.write([{"id": 1, "item": None}])
    assert exc.value.check_id == "CHK-NULL-item"
    assert t.current_version() == -1


def test_unique_check_rejects_duplicates(tmp_path):
    t = make_table(tmp_path).register_checks(expect_unique("id"))
    t.write([{"id": 1}, {"id": 2}])
    with pytest.raises(CheckViolation) as exc:
        t.write([{"id": 2}])  # duplicate against existing data
    assert "CHK-UNIQ-id" in str(exc.value)
    assert t.current_version() == 0  # failed write added no version


def test_range_check_rejects_out_of_range(tmp_path):
    t = make_table(tmp_path).register_checks(expect_range("amount", 0, 1000))
    with pytest.raises(CheckViolation) as exc:
        t.write([{"id": 1, "amount": 5000}])
    assert exc.value.check_id == "CHK-RANGE-amount"


def test_checks_pass_for_good_data(tmp_path):
    t = make_table(tmp_path).register_checks(
        expect_no_nulls("item"), expect_unique("id"), expect_range("amount", 0, 1000)
    )
    v = t.write([{"id": 1, "item": "book", "amount": 25.0}])
    assert v == 0


def test_checks_run_inside_transactions(tmp_path):
    t = make_table(tmp_path).register_checks(expect_no_nulls("item"))
    with pytest.raises(CheckViolation):
        with t.transaction() as txn:
            txn.write([{"id": 1, "item": "ok"}])
            txn.write([{"id": 2, "item": None}])
    assert t.current_version() == -1


def test_check_ids_are_stable_and_cited(tmp_path):
    t = make_table(tmp_path).register_checks(
        expect_no_nulls("a"), expect_unique("b")
    )
    gate = t._quality_gate
    assert gate.check_ids() == ["CHK-NULL-a", "CHK-UNIQ-b"]


# ------------------------------------------------------- combined / regression
def test_schema_and_checks_compose(tmp_path):
    t = make_table(tmp_path, schema=ORDERS_SCHEMA).register_checks(
        expect_unique("id")
    )
    t.write([{"id": 1, "item": "book", "amount": 9.5}])
    with pytest.raises(CheckViolation):
        t.write([{"id": 1, "item": "pen", "amount": 1.5}])
    with pytest.raises(SchemaViolation):
        t.write([{"id": "x", "item": "pen", "amount": 1.5}])


def test_time_travel_after_vacuum_horizon(tmp_path):
    t = make_table(tmp_path)
    t.write([{"id": 1}])
    t.write([{"id": 2}])
    vacuum(t, retain_versions=1)
    assert as_of(t, 1) == [{"id": 1}, {"id": 2}]


def test_time_travel_past_vacuum_raises_clear_error(tmp_path):
    """Added after review: used to leak a raw FileNotFoundError with a file path."""
    from lakehouse.table import VersionNotAvailable
    t = make_table(tmp_path)
    for i in range(3):
        t.write([{"id": i}])
    vacuum(t, retain_versions=1)
    with pytest.raises(VersionNotAvailable, match="removed by VACUUM"):
        as_of(t, 0)
    with pytest.raises(VersionNotAvailable):
        as_of(t, 99)

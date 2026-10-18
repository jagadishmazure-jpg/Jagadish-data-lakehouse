"""Versioned writes, quality gates, a concurrent-writer conflict, schema
evolution, time travel, optimize and vacuum on a throwaway table."""
import tempfile

from lakehouse.checks import CheckViolation, expect_no_nulls, expect_unique
from lakehouse.maintenance import optimize, vacuum
from lakehouse.schema import Schema, SchemaViolation
from lakehouse.table import DeltaTable, VersionNotAvailable
from lakehouse.time_travel import as_of, diff
from lakehouse.transactions import ConcurrentWriteError

with tempfile.TemporaryDirectory() as tmp:
    schema = Schema({"id": "int", "item": "str", "amount": "float"})
    orders = DeltaTable(f"{tmp}/orders", schema=schema)
    orders.register_checks(expect_no_nulls("item"), expect_unique("id"))
    orders.write([{"id": 1, "item": "book", "amount": 9.5}])
    orders.write([{"id": 2, "item": "pen", "amount": 1.5}])
    print("v0 (time travel):", as_of(orders, 0))
    with orders.transaction() as txn:
        txn.write([{"id": 3, "item": "desk", "amount": 120.0}])
        txn.write([{"id": 4, "item": "lamp", "amount": 35.0}])
    print("after transaction:", len(orders.read()), "rows")

    for bad, label in [([{"id": 1, "item": "chair", "amount": 50.0}], "duplicate id"),
                       ([{"id": 9, "item": None, "amount": 5.0}], "null item"),
                       ([{"id": "x", "item": "mug", "amount": 4.0}], "wrong type")]:
        try:
            orders.write(bad)
        except (CheckViolation, SchemaViolation) as e:
            print(f"rejected ({label}):", e)

    txn = orders.transaction()
    txn.__enter__()
    orders.write([{"id": 5, "item": "cup", "amount": 3.0}])   # another writer commits first
    txn.write([{"id": 6, "item": "tray", "amount": 7.0}])
    try:
        txn.__exit__(None, None, None)
    except ConcurrentWriteError as e:
        print("conflict:", e)

    evolved = DeltaTable(f"{tmp}/orders", schema=schema.evolve({"region": "str"}))
    evolved.write([{"id": 7, "item": "rug", "amount": 60.0, "region": "west"}])
    print("diff v0 -> latest:", diff(evolved, 0, len(evolved.history()) - 1))
    print("history:", [(h["version"], h["operation"]) for h in evolved.history()])
    print("optimize:", optimize(evolved))
    print("vacuum (keep 2 versions):", vacuum(evolved, retain_versions=2))
    try:
        as_of(evolved, 0)
    except VersionNotAvailable as e:
        print("time travel past retention:", e)

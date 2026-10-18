# Jagadish-data-lakehouse

A Delta Lake-style lakehouse in pure Python: ACID transactions on a data lake, schema enforcement and evolution, time travel, and maintenance (optimize/vacuum) — the three ideas that turned data lakes from swamps into reliable platforms.

## The problem

Data lakes are cheap and hold everything — but they're just files. No transactions (a failed job leaves half-written data), no schema enforcement (a renamed column silently corrupts downstream dashboards), no history (you can't ask "what did this table look like last Tuesday?"). Over time, lakes become **data swamps** nobody trusts.

Delta Lake fixed this by adding a **transaction log** alongside the files: every change is an atomic, versioned commit. This repo implements those semantics from scratch so you can see exactly how they work.

## What's inside

| Module | Implements |
|---|---|
| `lakehouse/table.py` | `DeltaTable`: versioned writes (append/overwrite), immutable versions, `_delta_log/` |
| `lakehouse/transactions.py` | ACID transactions (atomic commit, rollback), optimistic concurrency |
| `lakehouse/schema.py` | Schema enforcement (no silent coercion), explicit evolution |
| `lakehouse/time_travel.py` | `as_of(version)` / `as_of(timestamp)`, `diff` between versions |
| `lakehouse/maintenance.py` | `optimize()` (compaction), `vacuum()` (retention), Z-order hints |
| `lakehouse/checks.py` | Data-quality gates on write (`expect_no_nulls`, `expect_unique`, `expect_range`) |

See [docs/delta-concepts.md](docs/delta-concepts.md) for each Delta Lake concept mapped to its module, in plain terms.

## Quickstart

```python
from lakehouse.table import DeltaTable
from lakehouse.schema import Schema
from lakehouse.checks import expect_no_nulls, expect_unique
from lakehouse.time_travel import as_of

schema = Schema({"id": "int", "item": "str", "amount": "float"})
orders = DeltaTable("./orders", schema=schema)
orders.register_checks(expect_no_nulls("item"), expect_unique("id"))

orders.write([{"id": 1, "item": "book", "amount": 9.5}])
orders.write([{"id": 2, "item": "pen", "amount": 1.5}])

# Time travel: the table as it was at version 0
print(as_of(orders, 0))   # [{'id': 1, 'item': 'book', 'amount': 9.5}]

# ACID transaction: all-or-nothing
with orders.transaction() as txn:
    txn.write([{"id": 3, "item": "desk", "amount": 120.0}])
    txn.write([{"id": 4, "item": "lamp", "amount": 35.0}])

# This write is rejected: duplicate id violates CHK-UNIQ-id
# orders.write([{"id": 1, "item": "chair", "amount": 50.0}])
```

## Design notes

- **Versions are immutable.** A write never mutates old files; it adds a new version plus a log entry. That's what makes time travel and rollback trivial.
- **Optimistic concurrency.** Writers don't take locks; each transaction checks the version at commit time and fails fast with `ConcurrentWriteError` on conflict. Retry logic belongs to the caller.
- **Schema is explicit.** Wrong types are rejected, not coerced. New columns require `evolve()` — schema changes are events in the log, not accidents.
- **Checks cite their IDs.** Every quality violation names its check (`CHK-NULL-item`), so failures are actionable in pipelines and alerts.

## Production path

This is a teaching-grade implementation (JSON files, single process). Production Delta Lake runs on Parquet + cloud object storage with a real engine (Spark/Databricks). The semantics transfer directly: transaction log, optimistic concurrency, schema enforcement, time travel, OPTIMIZE/VACUUM, Z-ordering.

## Data

All data in tests is synthetic and fictional. No real customer data.

## Tests

```bash
python3 -m pytest tests/ -q   # 34 tests, no dependencies beyond pytest
```

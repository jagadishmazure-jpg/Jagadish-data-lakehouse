"""Delta Lake-style lakehouse: ACID transactions, schema enforcement, time travel.

Modules: table (DeltaTable), transactions (ACID + optimistic concurrency),
schema (enforcement + evolution), time_travel (as_of/diff), maintenance
(optimize/vacuum), checks (data-quality gates on write),
decontaminate (eval-set dedup: exact + MinHash near-duplicate removal).
"""

__all__ = [
    "table",
    "transactions",
    "schema",
    "time_travel",
    "maintenance",
    "checks",
    "decontaminate",
]

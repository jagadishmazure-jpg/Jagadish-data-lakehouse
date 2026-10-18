# Delta Lake concepts → this repo

Plain-terms mapping of the ideas Delta Lake added on top of a raw data lake,
and where each one lives in this codebase.

## Transaction log (`_delta_log/`)

**The idea:** Next to the data files sits a log. Every change — append,
overwrite, delete — is recorded as an ordered, immutable log entry
(version number, timestamp, operation, files touched). The log *is* the
table's source of truth; readers reconstruct any version by replaying it.

**Here:** `lakehouse/table.py` — `DeltaTable` writes one JSON log entry per
version under `<table>/_delta_log/`. `history()` replays the log.

**Why it matters:** Without a log, a crashed job leaves half-written files
and nobody knows what's valid. With a log, a version either committed
(log entry exists) or it didn't.

## ACID transactions

**The idea:** Multiple writes either all land or none do (atomicity), and
concurrent writers don't corrupt each other (isolation). Delta Lake uses
*optimistic* concurrency: writers proceed without locks, and at commit time
the log is checked — if someone else committed first, you retry.

**Here:** `lakehouse/transactions.py` — `table.transaction()` context
manager. Staged writes collapse into a single version on clean exit; an
exception discards the staging area (rollback, no partial version). A
version check at commit raises `ConcurrentWriteError` on conflict.

## Schema enforcement and evolution

**The idea:** Lakes accept anything, which is how a renamed column silently
breaks ten dashboards. Delta Lake validates every write against the table
schema and rejects mismatches. When the schema legitimately changes, you
declare it (`ALTER TABLE … ADD COLUMN`) — evolution is an explicit,
logged event, never an accident.

**Here:** `lakehouse/schema.py` — `Schema.validate_rows()` rejects wrong
types (no silent coercion — `bool` is not an `int`), missing columns, and
unknown columns. `evolve()` adds columns explicitly; `merge_schema()`
allows compatible widening only.

## Time travel

**The idea:** Because versions are immutable and the log is complete, you
can query the table *as of* any past version or timestamp. Audits,
reproducible ML training, and "what changed?" debugging all become queries.

**Here:** `lakehouse/time_travel.py` — `as_of(version)`,
`as_of_timestamp(ts)`, `diff()` (content-level added/removed) and
`diff_by_key()` (added/removed/changed per key).

## OPTIMIZE (compaction)

**The idea:** Streaming writes produce many tiny files, which makes reads
slow (each file is overhead). OPTIMIZE rewrites small files into fewer,
larger ones. Data doesn't change — only the physical layout.

**Here:** `lakehouse/maintenance.py::optimize()` — consolidates each
version's part-files into one; records the action in the maintenance log.

## VACUUM (retention)

**The idea:** Old versions accumulate forever. VACUUM deletes files for
versions older than a retention horizon, trading time-travel depth for
storage cost. It's the one destructive maintenance operation, so it
requires an explicit retention setting.

**Here:** `lakehouse/maintenance.py::vacuum(retain_versions)` — removes
data files *and* log entries for versions past the horizon; time travel
beyond it raises.

## Z-ordering (data skipping)

**The idea:** Physically colocating rows with similar values (e.g. same
`date` or `region`) lets the engine skip whole files when a query filters
on those columns — massive read speedups on large tables.

**Here:** `lakehouse/maintenance.py::z_order_hint()` — recorded as table
metadata and marked **hint-only**: this implementation stores the intent
so future writers can honor it, but does not physically reorder data.

## Constraints / expectations

**The idea:** Beyond schema types, tables need *data* rules: no nulls in
this column, IDs unique, amounts in range. Delta Lake supports CHECK
constraints; violations reject the write before bad data lands.

**Here:** `lakehouse/checks.py` — `expect_no_nulls`, `expect_unique`,
`expect_range`, composable via `QualityGate`, registered on the table with
`register_checks()`. Every violation cites its check ID
(e.g. `CHK-UNIQ-id`) so pipeline alerts are actionable.

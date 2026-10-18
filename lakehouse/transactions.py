"""ACID transactions with optimistic concurrency.

Usage::

    with table.transaction() as txn:
        txn.write(rows_a, mode="append")
        txn.write(rows_b, mode="append")
    # committed atomically on clean exit; rolled back on exception

Optimistic concurrency: the transaction records the table version at entry.
If another writer commits in between, commit raises ConcurrentWriteError and
nothing partial is left behind — the caller can retry.
"""


class ConcurrentWriteError(Exception):
    pass


class Transaction:
    def __init__(self, table):
        self._table = table
        self._start_version: int = -1
        self._staged: list = []  # list of (rows, mode, operation)
        self._committed_version: int | None = None

    def __enter__(self) -> "Transaction":
        self._start_version = self._table.current_version()
        return self

    def write(self, rows: list, mode: str = "append", operation: str = "WRITE"):
        self._staged.append((list(rows), mode, operation))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            # Rollback: staged writes are simply discarded. No partial
            # version was ever committed, so the table is untouched.
            self._staged.clear()
            return False  # re-raise the original exception
        # Commit: all staged writes collapse into ONE new version (atomic).
        table = self._table
        current = table.current_version()
        if current != self._start_version:
            self._staged.clear()
            raise ConcurrentWriteError(
                f"concurrent write detected: table moved from version "
                f"{self._start_version} to {current} during transaction"
            )
        if not self._staged:
            return False
        # Validate everything before committing anything.
        if table.schema is not None:
            for rows, _mode, _op in self._staged:
                table.schema.validate_rows(rows)
        base_rows = table.read() if self._start_version >= 0 else []
        merged: list = list(base_rows)
        total_added = 0
        for rows, mode, _op in self._staged:
            if mode == "overwrite":
                merged = [dict(r) for r in rows]
            elif mode == "append":
                merged = merged + [dict(r) for r in rows]
            else:
                raise ValueError(f"unknown write mode: {mode!r}")
            total_added += len(rows)
        table._run_quality_gates(merged)
        new_version = self._start_version + 1
        files = table._write_data_file(new_version, merged)
        table._write_log_entry(
            {
                "version": new_version,
                "timestamp": __import__("time").time(),
                "operation": "TRANSACTION",
                "mode": "atomic",
                "num_rows": len(merged),
                "num_added": total_added,
                "num_statements": len(self._staged),
                "files": files,
                "schema": table.schema.to_dict() if table.schema else None,
            }
        )
        self._committed_version = new_version
        self._staged.clear()
        return False

    @property
    def committed_version(self):
        return self._committed_version


def transaction(table) -> Transaction:
    return Transaction(table)

"""Maintenance: optimize (compaction), vacuum (retention), z-order hints.

In real Delta Lake these rewrite Parquet files on cloud storage. Here they
operate on the JSON version files, but the semantics are identical: optimize
reduces file count without changing data; vacuum drops files for versions
older than the retention horizon.
"""

from __future__ import annotations

import json
import os
import time


def optimize(table) -> dict:
    """Compact all live version files into one file per version.

    Returns a report of files before/after. Data is unchanged; the action is
    recorded in the delta log as an OPTIMIZE entry (metadata-only version —
    it does not create a new data version).
    """
    before_files: list[str] = []
    for entry in table.history():
        before_files.extend(entry["files"])
    # Re-read each version and rewrite it as a single consolidated file.
    for entry in table.history():
        version = entry["version"]
        rows = table.read(version=version)
        compacted = [f"v{version:020d}.p000.json"]
        with open(os.path.join(table.path, compacted[0]), "w") as f:
            json.dump(rows, f)
        # Remove any extra part files beyond p000.
        for fname in entry["files"]:
            if fname not in compacted:
                p = os.path.join(table.path, fname)
                if os.path.exists(p):
                    os.remove(p)
        entry["files"] = compacted
        # Rewrite the log entry in place (same version number, same data).
        table._write_log_entry(entry)
    # Record the maintenance action itself.
    _record_maintenance(
        table, "OPTIMIZE", {"files_before": len(before_files)}
    )
    after_files = sum(len(e["files"]) for e in table.history())
    return {
        "operation": "OPTIMIZE",
        "files_before": len(before_files),
        "files_after": after_files,
    }


def vacuum(table, retain_versions: int) -> dict:
    """Delete data files for versions older than the retention horizon.

    Keeps the newest `retain_versions` versions (their files AND log entries);
    older versions become unreadable — time travel past the horizon raises.
    Returns the list of removed versions.
    """
    if retain_versions < 1:
        raise ValueError("retain_versions must be >= 1")
    current = table.current_version()
    cutoff = current - retain_versions  # versions <= cutoff are removed
    removed: list[int] = []
    for version in range(0, cutoff + 1):
        log_path = table._log_path(version)
        if not os.path.exists(log_path):
            continue
        entry = table._read_log_entry(version)
        for fname in entry["files"]:
            p = os.path.join(table.path, fname)
            if os.path.exists(p):
                os.remove(p)
        os.remove(log_path)
        removed.append(version)
    _record_maintenance(
        table, "VACUUM", {"retain_versions": retain_versions, "removed": removed}
    )
    return {"operation": "VACUUM", "removed_versions": removed}


def z_order_hint(table, columns: list) -> dict:
    """Record a Z-ordering hint for the given columns.

    HINT-ONLY in this implementation: it is stored as table metadata (so
    future writers can honor it) but does not physically reorder data.
    Real Delta Lake uses it to colocate related rows for data skipping.
    """
    meta_path = os.path.join(table.path, "_zorder.json")
    hint = {"columns": list(columns), "recorded_at": time.time(), "hint_only": True}
    with open(meta_path, "w") as f:
        json.dump(hint, f, indent=1)
    _record_maintenance(table, "ZORDER_HINT", {"columns": list(columns)})
    return hint


def _record_maintenance(table, operation: str, details: dict) -> None:
    path = os.path.join(table.path, "_maintenance_log.json")
    log: list = []
    if os.path.exists(path):
        with open(path) as f:
            log = json.load(f)
    log.append(
        {
            "operation": operation,
            "timestamp": time.time(),
            "table_version_at_time": table.current_version(),
            "details": details,
        }
    )
    with open(path, "w") as f:
        json.dump(log, f, indent=1)

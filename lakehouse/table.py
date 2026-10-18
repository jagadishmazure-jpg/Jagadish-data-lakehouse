"""DeltaTable: versioned table with a JSON transaction log.

Storage layout per table directory::

    <table>/
        _delta_log/
            00000000000000000000.json   # version 0 entry
            00000000000000000001.json   # version 1 entry
            ...
        v00000000000000000000.json      # data file(s) for version 0
        v00000000000000000001.json

Every write creates a new immutable version; old versions are never mutated,
which is what makes time travel possible.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .schema import Schema

LOG_DIR = "_delta_log"


class DeltaTable:
    def __init__(self, path: str, schema: Optional[Schema] = None):
        self.path = path
        self.log_dir = os.path.join(path, LOG_DIR)
        os.makedirs(self.log_dir, exist_ok=True)
        self._schema = schema
        self._quality_gate = None

    def register_checks(self, *checks) -> "DeltaTable":
        """Register data-quality checks; they run on every write."""
        from .checks import QualityGate

        if self._quality_gate is None:
            self._quality_gate = QualityGate()
        for check in checks:
            self._quality_gate.add(check)
        return self

    def _run_quality_gates(self, rows: List[Dict[str, Any]]) -> None:
        if self._quality_gate is not None:
            self._quality_gate.validate(rows)

    # ------------------------------------------------------------------ log
    def _log_path(self, version: int) -> str:
        return os.path.join(self.log_dir, f"{version:020d}.json")

    def _data_path(self, version: int, part: int = 0) -> str:
        return os.path.join(self.path, f"v{version:020d}.p{part:03d}.json")

    def _write_log_entry(self, entry: Dict[str, Any]) -> None:
        with open(self._log_path(entry["version"]), "w") as f:
            json.dump(entry, f, indent=1)

    def _read_log_entry(self, version: int) -> Dict[str, Any]:
        with open(self._log_path(version)) as f:
            return json.load(f)

    def current_version(self) -> int:
        """Highest committed version, or -1 when the table is empty."""
        versions = [
            int(name.split(".")[0])
            for name in os.listdir(self.log_dir)
            if name.endswith(".json")
        ]
        return max(versions) if versions else -1

    def history(self) -> List[Dict[str, Any]]:
        """All committed versions, oldest first."""
        return [
            self._read_log_entry(v)
            for v in range(self.current_version() + 1)
            if os.path.exists(self._log_path(v))
        ]

    # ----------------------------------------------------------------- data
    def _write_data_file(self, version: int, rows: List[Dict[str, Any]]) -> List[str]:
        fname = f"v{version:020d}.p000.json"
        with open(os.path.join(self.path, fname), "w") as f:
            json.dump(rows, f)
        return [fname]

    def _read_data_files(self, files: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for fname in files:
            with open(os.path.join(self.path, fname)) as f:
                rows.extend(json.load(f))
        return rows

    # ------------------------------------------------------------------ api
    @property
    def schema(self) -> Optional[Schema]:
        return self._schema

    def write(
        self,
        rows: List[Dict[str, Any]],
        mode: str = "append",
        operation: str = "WRITE",
        expected_version: Optional[int] = None,
    ) -> int:
        """Write rows, creating a new immutable version.

        mode: "append" adds to current rows; "overwrite" replaces them.
        expected_version: optimistic-concurrency guard — when given, the write
        only commits if the table is still at that version.
        Returns the new version number.
        """
        from .transactions import ConcurrentWriteError  # local import: no cycle

        if mode not in ("append", "overwrite"):
            raise ValueError(f"unknown write mode: {mode!r}")
        current = self.current_version()
        if expected_version is not None and expected_version != current:
            raise ConcurrentWriteError(
                f"version conflict: expected {expected_version}, table at {current}"
            )
        if self._schema is not None:
            self._schema.validate_rows(rows)
        base_rows = self.read() if mode == "append" and current >= 0 else []
        new_rows = base_rows + [dict(r) for r in rows]
        self._run_quality_gates(new_rows)
        new_version = current + 1
        files = self._write_data_file(new_version, new_rows)
        entry = {
            "version": new_version,
            "timestamp": time.time(),
            "operation": operation,
            "mode": mode,
            "num_rows": len(new_rows),
            "num_added": len(rows),
            "files": files,
            "schema": self._schema.to_dict() if self._schema else None,
        }
        self._write_log_entry(entry)
        return new_version

    def read(self, version: Optional[int] = None) -> List[Dict[str, Any]]:
        """Read the latest version, or time-travel to a specific version."""
        v = self.current_version() if version is None else version
        if v < 0:
            return []
        entry = self._read_log_entry(v)
        return self._read_data_files(entry["files"])

    def version_timestamp(self, version: int) -> float:
        return float(self._read_log_entry(version)["timestamp"])

    def transaction(self):
        """Start an ACID transaction (context manager)."""
        from .transactions import Transaction

        return Transaction(self)

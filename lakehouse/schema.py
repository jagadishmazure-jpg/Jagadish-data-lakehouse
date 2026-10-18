"""Schema: column names + types with enforcement and explicit evolution.

No silent coercion: a write carrying a wrong-typed value is rejected with
SchemaViolation, because silent coercion is how data lakes became swamps.
"""


class SchemaViolation(Exception):
    pass


_TYPE_NAMES = {"int": int, "float": float, "str": str, "bool": bool}


class Schema:
    def __init__(self, columns: dict):
        """columns: {name: type-name} where type-name in int|float|str|bool."""
        for name, tname in columns.items():
            if tname not in _TYPE_NAMES:
                raise ValueError(f"unknown type {tname!r} for column {name!r}")
        self.columns = dict(columns)

    def to_dict(self) -> dict:
        return dict(self.columns)

    @classmethod
    def from_dict(cls, d: dict) -> "Schema":
        return cls(d)

    def _check_value(self, col: str, tname: str, value) -> None:
        pytype = _TYPE_NAMES[tname]
        # bool is a subclass of int: exclude it explicitly for int columns.
        if tname == "int" and isinstance(value, bool):
            raise SchemaViolation(
                f"column {col!r}: expected int, got bool"
            )
        if not isinstance(value, pytype):
            raise SchemaViolation(
                f"column {col!r}: expected {tname}, "
                f"got {type(value).__name__} ({value!r})"
            )

    def validate_rows(self, rows: list) -> None:
        for i, row in enumerate(rows):
            missing = set(self.columns) - set(row)
            if missing:
                raise SchemaViolation(
                    f"row {i}: missing columns {sorted(missing)}"
                )
            extra = set(row) - set(self.columns)
            if extra:
                raise SchemaViolation(
                    f"row {i}: unknown columns {sorted(extra)} "
                    "(use evolve() to add columns explicitly)"
                )
            for col, tname in self.columns.items():
                self._check_value(col, tname, row[col])

    def evolve(self, add_columns: dict) -> "Schema":
        """Return a new Schema with added columns; the old one is unchanged."""
        merged = dict(self.columns)
        for name, tname in add_columns.items():
            if name in merged:
                raise SchemaViolation(f"column {name!r} already exists")
            if tname not in _TYPE_NAMES:
                raise ValueError(f"unknown type {tname!r}")
            merged[name] = tname
        return Schema(merged)

    def merge_schema(self, rows: list) -> "Schema":
        """Widen the schema for columns present in rows but missing here.

        Only *adds* columns (never changes types): compatible widening.
        Raises SchemaViolation if a row carries a conflicting type for an
        existing column.
        """
        new_cols = {}
        for row in rows:
            for col, value in row.items():
                if col in self.columns:
                    continue
                inferred = _infer_type(value)
                if col in new_cols and new_cols[col] != inferred:
                    raise SchemaViolation(
                        f"merge_schema: conflicting inferred types for {col!r}"
                    )
                new_cols[col] = inferred
        # validate existing columns against the rows (no silent conflicts)
        for row in rows:
            for col, tname in self.columns.items():
                if col in row:
                    self._check_value(col, tname, row[col])
        return self.evolve(new_cols) if new_cols else Schema(self.columns)


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    raise SchemaViolation(f"cannot infer schema type for {value!r}")

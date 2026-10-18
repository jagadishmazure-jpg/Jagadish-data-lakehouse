"""Data-quality gates: checks registered on a table run on every write.

A failing check rejects the whole write with a CheckViolation that cites the
check ID — bad data never lands in the lake. This is the programmatic version
of Delta Lake's CHECK constraints / expectations.
"""


class CheckViolation(Exception):
    def __init__(self, check_id: str, message: str):
        self.check_id = check_id
        super().__init__(f"[{check_id}] {message}")


class Check:
    def __init__(self, check_id: str, description: str):
        self.check_id = check_id
        self.description = description

    def run(self, rows: list) -> None:
        raise NotImplementedError


class NoNulls(Check):
    def __init__(self, column: str):
        super().__init__(
            f"CHK-NULL-{column}", f"column {column!r} must not contain nulls"
        )
        self.column = column

    def run(self, rows: list) -> None:
        for i, row in enumerate(rows):
            if row.get(self.column) is None:
                raise CheckViolation(
                    self.check_id, f"row {i}: null value in {self.column!r}"
                )


class Unique(Check):
    def __init__(self, column: str):
        super().__init__(
            f"CHK-UNIQ-{column}", f"column {column!r} must be unique"
        )
        self.column = column

    def run(self, rows: list) -> None:
        seen = {}
        for i, row in enumerate(rows):
            value = row.get(self.column)
            if value in seen:
                raise CheckViolation(
                    self.check_id,
                    f"duplicate value {value!r} in {self.column!r} "
                    f"(rows {seen[value]} and {i})",
                )
            seen[value] = i


class InRange(Check):
    def __init__(self, column: str, lo, hi):
        super().__init__(
            f"CHK-RANGE-{column}",
            f"column {column!r} must be within [{lo}, {hi}]",
        )
        self.column = column
        self.lo = lo
        self.hi = hi

    def run(self, rows: list) -> None:
        for i, row in enumerate(rows):
            value = row.get(self.column)
            if value is None:
                continue
            if not (self.lo <= value <= self.hi):
                raise CheckViolation(
                    self.check_id,
                    f"row {i}: {self.column!r}={value!r} outside "
                    f"[{self.lo}, {self.hi}]",
                )


def expect_no_nulls(column: str) -> NoNulls:
    return NoNulls(column)


def expect_unique(column: str) -> Unique:
    return Unique(column)


def expect_range(column: str, lo, hi) -> InRange:
    return InRange(column, lo, hi)


class QualityGate:
    """Collection of checks enforced together on a write."""

    def __init__(self, checks: list | None = None):
        self.checks: list = list(checks or [])

    def add(self, check: Check) -> "QualityGate":
        self.checks.append(check)
        return self

    def validate(self, rows: list) -> None:
        for check in self.checks:
            check.run(rows)

    def check_ids(self) -> list:
        return [c.check_id for c in self.checks]

"""Time travel: query the table as of a version or timestamp, and diff versions."""


def as_of(table, version: int):
    """Read the table exactly as it was at `version`."""
    return table.read(version=version)


def as_of_timestamp(table, timestamp: float):
    """Read the latest version committed at or before `timestamp`.

    Raises ValueError if no version existed yet at that time.
    """
    chosen = None
    for entry in table.history():
        if entry["timestamp"] <= timestamp:
            chosen = entry["version"]
        else:
            break
    if chosen is None:
        raise ValueError("no version committed at or before the given timestamp")
    return table.read(version=chosen)


def _row_key(row: dict):
    # Stable identity for diffing: full row content as sorted items.
    return tuple(sorted((k, _freeze(v)) for k, v in row.items()))


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def diff(table, v1: int, v2: int) -> dict:
    """Compare version v1 → v2. Returns added / removed / unchanged row lists.

    Rows are compared by full content. 'changed' is reported as a
    (before, after) pair only when a caller supplies key-based matching;
    by default this implementation reports content-level added/removed,
    which is exact for append-mostly tables.
    """
    rows1 = {_row_key(r) for r in table.read(version=v1)}
    rows2 = {_row_key(r) for r in table.read(version=v2)}
    added_keys = rows2 - rows1
    removed_keys = rows1 - rows2
    return {
        "added": [dict(k) for k in sorted(added_keys)],
        "removed": [dict(k) for k in sorted(removed_keys)],
        "added_count": len(added_keys),
        "removed_count": len(removed_keys),
        "from_version": v1,
        "to_version": v2,
    }


def diff_by_key(table, v1: int, v2: int, key: str) -> dict:
    """Key-based diff: reports added / removed / changed (before, after)."""
    map1 = {r[key]: r for r in table.read(version=v1)}
    map2 = {r[key]: r for r in table.read(version=v2)}
    added = [map2[k] for k in map2 if k not in map1]
    removed = [map1[k] for k in map1 if k not in map2]
    changed = [
        {"key": k, "before": map1[k], "after": map2[k]}
        for k in map1
        if k in map2 and map1[k] != map2[k]
    ]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "from_version": v1,
        "to_version": v2,
    }

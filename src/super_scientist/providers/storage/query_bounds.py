from __future__ import annotations

from collections.abc import Iterable

# Kept below SQLite's historical 999-variable default as well as current builds.
SQLITE_IN_PARAMETER_CHUNK = 900


def sqlite_in_chunks[ValueT: (str, int)](
    values: Iterable[ValueT],
) -> tuple[tuple[ValueT, ...], ...]:
    exact_values = tuple(sorted(set(values)))
    return tuple(
        exact_values[offset : offset + SQLITE_IN_PARAMETER_CHUNK]
        for offset in range(0, len(exact_values), SQLITE_IN_PARAMETER_CHUNK)
    )

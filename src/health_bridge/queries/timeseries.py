import sqlite3
from pathlib import Path
from typing import Final, TypeAlias

from pydantic import TypeAdapter, ValidationError

from health_bridge.queries._common import (
    DEFAULT_LIMIT,
    connect_readonly,
    notes_for_count,
    source_from_row,
    unique_sources,
)
from health_bridge.queries.models import Period, TimeseriesPoint, TimeseriesResult
from health_bridge.timeseries_catalog import (
    LEGACY_SAMPLE_TYPE_CODE_ALIASES,
    TIMESERIES_BY_TYPE_CODE,
    canonical_sample_type_code,
)

TimeseriesRow: TypeAlias = tuple[str, str, str, float, str, str, str, str, str]
TIMESERIES_ROWS_ADAPTER: Final[TypeAdapter[list[TimeseriesRow]]] = TypeAdapter(
    list[TimeseriesRow],
)
METADATA_ADAPTER: Final[TypeAdapter[dict[str, object]]] = TypeAdapter(
    dict[str, object],
)
LEGACY_QUERY_TYPE_CODES: Final[dict[str, tuple[str, ...]]] = {
    "energy": ("active_energy",),
    "weight": ("body_mass",),
}
STORED_TYPE_CODES_SQL: Final = (
    "select distinct type_code from samples where type_code in ({placeholders})"
)
STORED_TYPE_CODES_ADAPTER: Final[TypeAdapter[list[tuple[str]]]] = TypeAdapter(
    list[tuple[str]],
)

TIMESERIES_SQL: Final = (
    "select samples.type_code, samples.start_time, samples.end_time, samples.value, "
    "samples.unit, sources.source_key, sources.name, sources.kind, "
    "samples.metadata_json "
    "from samples join sources on sources.source_id = samples.source_id "
    "where samples.type_code in ({placeholders}) "
    "and samples.start_time >= ? and samples.start_time < ? "
    "order by samples.start_time, samples.type_code "
    "limit ?"
)


def get_timeseries(
    db_path: Path,
    type_codes: tuple[str, ...],
    start_time: str,
    end_time: str,
    limit: int = DEFAULT_LIMIT,
) -> TimeseriesResult:
    if not type_codes:
        return TimeseriesResult(
            period=Period(start=start_time, end=end_time),
            requested_types=type_codes,
            points=(),
            sources_used=(),
            missing_data_notes=notes_for_count(0),
            truncated=False,
        )
    query_type_codes = _compatible_query_type_codes(type_codes)
    placeholders = ",".join("?" for _type_code in query_type_codes)
    parameters = (*query_type_codes, start_time, end_time, limit + 1)
    with connect_readonly(db_path) as connection:
        rows = TIMESERIES_ROWS_ADAPTER.validate_python(
            connection.execute(
                TIMESERIES_SQL.format(placeholders=placeholders),
                parameters,
            ).fetchall(),
        )
        unknown_notes = _unknown_type_code_notes(connection, type_codes)
    truncated = len(rows) > limit
    limited_rows = tuple(rows[:limit])
    points = tuple(_point_from_row(row) for row in limited_rows)
    return TimeseriesResult(
        period=Period(start=start_time, end=end_time),
        requested_types=type_codes,
        points=points,
        sources_used=unique_sources(point.source for point in points),
        missing_data_notes=(
            *unknown_notes,
            *notes_for_count(len(points)),
        ),
        truncated=truncated,
    )


def _unknown_type_code_notes(
    connection: sqlite3.Connection,
    type_codes: tuple[str, ...],
) -> tuple[str, ...]:
    known = {
        *TIMESERIES_BY_TYPE_CODE,
        *LEGACY_SAMPLE_TYPE_CODE_ALIASES,
        *LEGACY_SAMPLE_TYPE_CODE_ALIASES.values(),
        *LEGACY_QUERY_TYPE_CODES,
    }
    candidates = tuple(
        dict.fromkeys(code for code in type_codes if code not in known),
    )
    if not candidates:
        return ()
    placeholders = ",".join("?" for _code in candidates)
    stored = {
        row[0]
        for row in STORED_TYPE_CODES_ADAPTER.validate_python(
            connection.execute(
                STORED_TYPE_CODES_SQL.format(placeholders=placeholders),
                candidates,
            ).fetchall(),
        )
    }
    unknown = tuple(code for code in candidates if code not in stored)
    if not unknown:
        return ()
    names = ", ".join(unknown)
    note = (
        f"Unknown type code(s): {names}. Not a metric type this bridge knows; "
        "call list_synced_metrics for valid codes."
    )
    return (note,)


def _compatible_query_type_codes(type_codes: tuple[str, ...]) -> tuple[str, ...]:
    query_type_codes: list[str] = []
    for type_code in type_codes:
        canonical_type_code = canonical_sample_type_code(type_code)
        query_type_codes.append(canonical_type_code)
        query_type_codes.extend(LEGACY_QUERY_TYPE_CODES.get(canonical_type_code, ()))
    return tuple(dict.fromkeys(query_type_codes))


def _point_from_row(row: TimeseriesRow) -> TimeseriesPoint:
    (
        type_code,
        start_time,
        end_time,
        value,
        unit,
        source_key,
        name,
        kind,
        metadata_json,
    ) = row
    return TimeseriesPoint(
        type_code=type_code,
        start_time=start_time,
        end_time=end_time,
        value=float(value),
        unit=unit,
        source=source_from_row((source_key, name, kind)),
        metadata=_metadata(metadata_json),
    )


def _metadata(metadata_json: str) -> dict[str, object]:
    try:
        return METADATA_ADAPTER.validate_json(metadata_json)
    except ValidationError:
        return {}

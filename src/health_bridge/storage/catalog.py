import json
import sqlite3
from collections.abc import Iterable
from typing import cast

from pydantic import ValidationError

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.storage.sqlite_rows import fetch_one_int
from health_bridge.timeseries_catalog import canonical_sample_type_code

UPSERT_SOURCE_SQL = (
    "insert into sources (source_key, name, kind, bundle_id, device_model) "
    "values (?, ?, ?, ?, ?) on conflict(source_key) do update set "
    "name = excluded.name, kind = excluded.kind, bundle_id = excluded.bundle_id, "
    "device_model = excluded.device_model, "
    "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
)
# Each source owns its own type rows, keyed (source_id, type_code), so a batch can
# only ever declare types for the sources it is bound to and never touches another
# source's metadata (RELAY-S15). A stored row is never rewritten; aliases are
# capped per owning type so repeated declarations cannot grow the table.
MAX_STORED_ALIASES_PER_TYPE = 64
INSERT_HEALTH_TYPE_SQL = (
    "insert into health_types (source_id, type_code, display_name, category, "
    "default_unit, sensitivity) values (?, ?, ?, ?, ?, ?) "
    "on conflict(source_id, type_code) do nothing"
)
INSERT_ALIAS_SQL = (
    "insert into health_type_aliases (source_id, type_code, alias) "
    "select ?1, ?2, ?3 where (select count(*) from health_type_aliases "
    "where source_id = ?1 and type_code = ?2) < ?4 "
    "on conflict(source_id, type_code, alias) do nothing"
)
MISSING_SOURCE_ERROR = "source was not available after catalog upsert"
MAX_REPORTED_REFERENCE_KEYS = 5
KNOWN_SOURCE_KEYS_SQL = (
    "select source_key from sources "
    "where source_key in (select value from json_each(?))"
)
KNOWN_TYPE_CODES_SQL = (
    "select type_code from health_types "
    "where type_code in (select value from json_each(?))"
)


class UndeclaredReferenceError(Exception):
    """A schema-valid batch names a type or source it never declared (RELAY-S13)."""


def refuse_undeclared_references(kind: str, keys: Iterable[str]) -> None:
    """Refuse the batch, naming the kind and at most the first few offending keys."""
    shown = sorted(keys)[:MAX_REPORTED_REFERENCE_KEYS]
    message = f"batch names an undeclared {kind}: {', '.join(shown)}"
    raise UndeclaredReferenceError(message)


def known_keys(
    connection: sqlite3.Connection,
    sql: str,
    keys: set[str],
) -> set[str]:
    """Return which of `keys` exist, in one set-based query."""
    if not keys:
        return set()
    rows = cast(
        "list[tuple[str]]",
        connection.execute(sql, (json.dumps(sorted(keys)),)).fetchall(),
    )
    return {row[0] for row in rows}


def upsert_catalog(connection: sqlite3.Connection, batch: HealthBridgeBatchV1) -> None:
    for source in batch.sources:
        _ = connection.execute(
            UPSERT_SOURCE_SQL,
            (
                source.source_key,
                source.name,
                source.kind,
                source.bundle_id,
                source.device_model,
            ),
        )
    source_ids = [
        source_id(connection, key)
        for key in sorted({source.source_key for source in batch.sources})
    ]
    for health_type in batch.health_types:
        canonical_type_code = canonical_sample_type_code(health_type.type_code)
        for owner_id in source_ids:
            _ = connection.execute(
                INSERT_HEALTH_TYPE_SQL,
                (
                    owner_id,
                    canonical_type_code,
                    health_type.display_name,
                    health_type.category,
                    health_type.default_unit,
                    health_type.sensitivity,
                ),
            )
        aliases = set(health_type.aliases)
        if canonical_type_code != health_type.type_code:
            aliases.add(health_type.type_code)
        for owner_id in source_ids:
            for alias in sorted(aliases):
                _ = connection.execute(
                    INSERT_ALIAS_SQL,
                    (owner_id, canonical_type_code, alias, MAX_STORED_ALIASES_PER_TYPE),
                )


def source_id(connection: sqlite3.Connection, source_key: str) -> int:
    try:
        source_id_value = fetch_one_int(
            connection,
            "select source_id from sources where source_key = ?",
            (source_key,),
        )
    except ValidationError as exc:
        raise sqlite3.IntegrityError(MISSING_SOURCE_ERROR) from exc
    if source_id_value <= 0:
        raise sqlite3.IntegrityError(MISSING_SOURCE_ERROR)
    return source_id_value

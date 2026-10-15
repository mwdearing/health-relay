"""Prune stored type catalog rows no sample uses (RELAY-S14, #134).

`health_types` and `health_type_aliases` only grow: every declaration adds rows
and nothing removes them. A row is prunable when all of these hold:

* no stored sample carries its `(source_id, type_code)`;
* it was declared more than N days ago, because a later batch may still reuse a
  type stored by an earlier one;
* no deletion tombstone names the type, because intake evidence rebuilds the
  alternate record ids of a deleted sample from the stored type codes.

Dropping a row loses no health data; the declaring source's next batch recreates
it. Aliases of a dropped row go with it.
"""

import sqlite3
from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

from health_bridge.storage.database import (
    connect_database,
    connect_readonly_database,
)
from health_bridge.storage.sqlite_rows import fetch_one_int

DEFAULT_MIN_AGE_DAYS: Final = 30

# One candidate set per statement, evaluated once: unreferenced, old enough, and
# not named by a sample tombstone of the same source whose id is exactly
# `hk-quantity-<type with - for _>-<36-character uuid>`. The CTE is repeated
# verbatim in each statement so every statement is one literal.
COUNT_TYPES_SQL: Final = """
with prunable as materialized (
    select t.source_id, t.type_code
    from health_types as t
    where not exists (
            select 1 from samples as s
            where s.source_id = t.source_id and s.type_code = t.type_code
        )
      and t.created_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
      and not exists (
            select 1 from deleted_records as d
            where d.source_id = t.source_id
              and d.record_family = 'sample'
              and d.client_record_id like
                  'hk-quantity-' || replace(t.type_code, '_', '-') || '-%'
              and length(d.client_record_id) =
                  length('hk-quantity-' || replace(t.type_code, '_', '-') || '-') + 36
        )
)
select count(*) from prunable
"""
COUNT_ALIASES_SQL: Final = """
with prunable as materialized (
    select t.source_id, t.type_code
    from health_types as t
    where not exists (
            select 1 from samples as s
            where s.source_id = t.source_id and s.type_code = t.type_code
        )
      and t.created_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
      and not exists (
            select 1 from deleted_records as d
            where d.source_id = t.source_id
              and d.record_family = 'sample'
              and d.client_record_id like
                  'hk-quantity-' || replace(t.type_code, '_', '-') || '-%'
              and length(d.client_record_id) =
                  length('hk-quantity-' || replace(t.type_code, '_', '-') || '-') + 36
        )
)
select count(*) from health_type_aliases as a
join prunable as p
  on p.source_id = a.source_id and p.type_code = a.type_code
"""
DELETE_TYPES_SQL: Final = """
with prunable as materialized (
    select t.source_id, t.type_code
    from health_types as t
    where not exists (
            select 1 from samples as s
            where s.source_id = t.source_id and s.type_code = t.type_code
        )
      and t.created_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
      and not exists (
            select 1 from deleted_records as d
            where d.source_id = t.source_id
              and d.record_family = 'sample'
              and d.client_record_id like
                  'hk-quantity-' || replace(t.type_code, '_', '-') || '-%'
              and length(d.client_record_id) =
                  length('hk-quantity-' || replace(t.type_code, '_', '-') || '-') + 36
        )
)
delete from health_types
where (source_id, type_code) in (select source_id, type_code from prunable)
"""


class PruneResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    applied: bool
    type_rows: int
    alias_rows: int


def prune_unreferenced_types(
    db_path: Path,
    *,
    apply: bool,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
) -> PruneResult:
    """Count (and with `apply`, delete) prunable catalog rows.

    A dry run opens the database read-only and writes nothing. `apply` counts and
    deletes inside one write transaction, so the reported rows are the removed rows.
    """
    if not db_path.is_file():
        message = f"database not found: {db_path}"
        raise FileNotFoundError(message)
    cutoff = f"-{max(min_age_days, 0)} days"
    if not apply:
        with connect_readonly_database(db_path) as connection:
            return _count(connection, cutoff, applied=False)
    with connect_database(db_path) as connection:
        _ = connection.execute("begin immediate")
        try:
            result = _count(connection, cutoff, applied=True)
            if result.type_rows:
                _ = connection.execute(DELETE_TYPES_SQL, (cutoff,))
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        return result


def _count(
    connection: sqlite3.Connection, cutoff: str, *, applied: bool
) -> PruneResult:
    return PruneResult(
        applied=applied,
        type_rows=fetch_one_int(connection, COUNT_TYPES_SQL, (cutoff,)),
        alias_rows=fetch_one_int(connection, COUNT_ALIASES_SQL, (cutoff,)),
    )

"""Prune stored type catalog rows no sample uses (RELAY-S14, #134).

`health_types` and `health_type_aliases` only grow: every declaration adds rows
and nothing removes them. A row is *unreferenced* when no stored sample carries
its `(source_id, type_code)`. Dropping it loses no health data; the declaring
source's next batch recreates it. Aliases of a dropped row go with it.
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

COUNT_TYPES_SQL: Final = """
select count(*) from health_types as t
where not exists (
    select 1 from samples as s
    where s.source_id = t.source_id and s.type_code = t.type_code
)
"""
COUNT_ALIASES_SQL: Final = """
select count(*) from health_type_aliases as a
where not exists (
    select 1 from samples as s
    where s.source_id = a.source_id and s.type_code = a.type_code
)
"""
DELETE_TYPES_SQL: Final = """
delete from health_types
where not exists (
    select 1 from samples as s
    where s.source_id = health_types.source_id
      and s.type_code = health_types.type_code
)
"""


class PruneResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    applied: bool
    type_rows: int
    alias_rows: int


def prune_unreferenced_types(db_path: Path, *, apply: bool) -> PruneResult:
    """Count (and with `apply`, delete) catalog rows no sample references.

    A dry run opens the database read-only and writes nothing. `apply` counts and
    deletes inside one write transaction, so the reported rows are the removed rows.
    """
    if not apply:
        with connect_readonly_database(db_path) as connection:
            return _count(connection, applied=False)
    with connect_database(db_path) as connection:
        _ = connection.execute("begin immediate")
        try:
            result = _count(connection, applied=True)
            if result.type_rows:
                _ = connection.execute(DELETE_TYPES_SQL)
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        return result


def _count(connection: sqlite3.Connection, *, applied: bool) -> PruneResult:
    return PruneResult(
        applied=applied,
        type_rows=fetch_one_int(connection, COUNT_TYPES_SQL),
        alias_rows=fetch_one_int(connection, COUNT_ALIASES_SQL),
    )

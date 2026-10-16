"""RELAY-S14: unreferenced type catalog rows can be pruned; referenced ones stay."""

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import ingest_batch
from health_bridge.storage import initialize_database
from health_bridge.storage.catalog_prune import prune_unreferenced_types

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.synthetic.json")
COUNT_SQL = "select count(*) from health_types"
USED_SQL = (
    "select count(*) from health_types t where exists (select 1 from samples s "
    "where s.source_id = t.source_id and s.type_code = t.type_code)"
)


def _ingest_fixture(db_path: Path) -> None:
    payload = cast(
        "dict[str, list[dict[str, object]]]",
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    )
    payload["health_types"].append(
        {
            "type_code": "synthetic_unused_type",
            "display_name": "Unused",
            "category": "activity",
            "default_unit": "count",
            "sensitivity": "low",
            "aliases": ["synthetic_unused_alias"],
        }
    )
    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))
    assert ingest_batch(db_path, batch, "test").status == "succeeded"


def _count(db_path: Path, sql: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return cast("int", connection.execute(sql).fetchone()[0])


def test_dry_run_counts_without_deleting(tmp_path: Path) -> None:
    db_path = tmp_path / "prune.sqlite"
    initialize_database(db_path)
    _ingest_fixture(db_path)
    before = _count(db_path, COUNT_SQL)

    result = prune_unreferenced_types(db_path, apply=False, min_age_days=0)

    assert result.type_rows >= 1
    assert not result.applied
    assert _count(db_path, COUNT_SQL) == before


def test_apply_removes_only_unreferenced_rows_and_their_aliases(tmp_path: Path) -> None:
    db_path = tmp_path / "prune.sqlite"
    initialize_database(db_path)
    _ingest_fixture(db_path)
    used = _count(db_path, USED_SQL)
    samples = _count(db_path, "select count(*) from samples")
    unused_sql = (
        "select count(*) from health_types where type_code = 'synthetic_unused_type'"
    )
    assert _count(db_path, unused_sql) >= 1

    result = prune_unreferenced_types(db_path, apply=True, min_age_days=0)

    assert result.applied
    assert _count(db_path, unused_sql) == 0
    assert _count(db_path, USED_SQL) == used
    assert _count(db_path, "select count(*) from samples") == samples
    alias_sql = """
        select count(*) from health_type_aliases
        where type_code = 'synthetic_unused_type'
        """
    assert _count(db_path, alias_sql) == 0
    assert prune_unreferenced_types(db_path, apply=True, min_age_days=0).type_rows == 0


def test_dry_run_on_a_missing_database_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nested" / "missing.sqlite"
    with pytest.raises((OSError, sqlite3.Error)):
        _ = prune_unreferenced_types(missing, apply=False)
    assert not missing.parent.exists()


def test_recently_declared_types_are_kept_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "prune.sqlite"
    initialize_database(db_path)
    _ingest_fixture(db_path)
    before = _count(db_path, COUNT_SQL)

    result = prune_unreferenced_types(db_path, apply=True)

    assert result.type_rows == 0
    assert _count(db_path, COUNT_SQL) == before


def test_types_named_by_a_deletion_tombstone_are_kept(tmp_path: Path) -> None:
    db_path = tmp_path / "prune.sqlite"
    initialize_database(db_path)
    _ingest_fixture(db_path)
    with sqlite3.connect(db_path) as connection:
        source_id = cast(
            "int",
            connection.execute("select min(source_id) from sources").fetchone()[0],
        )
        _ = connection.execute(
            """
            insert into deleted_records
                (source_id, record_family, client_record_id, deleted_at)
            values (?, 'sample',
                'hk-quantity-synthetic-unused-type-'
                || '12345678-1234-1234-1234-123456789012', ?)
            """,
            (source_id, "2026-01-01T00:00:00Z"),
        )

    result = prune_unreferenced_types(db_path, apply=True, min_age_days=0)

    with sqlite3.connect(db_path) as connection:
        kept = cast(
            "int",
            connection.execute(
                """
                select count(*) from health_types
                where type_code = 'synthetic_unused_type'
                """
            ).fetchone()[0],
        )
    assert kept >= 1
    assert result.applied


def test_apply_on_a_missing_database_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nested" / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        _ = prune_unreferenced_types(missing, apply=True)
    assert not missing.parent.exists()


def test_tombstone_from_another_source_protects_the_type(tmp_path: Path) -> None:
    db_path = tmp_path / "prune.sqlite"
    initialize_database(db_path)
    _ingest_fixture(db_path)
    with sqlite3.connect(db_path) as connection:
        other = cast(
            "int",
            connection.execute("select max(source_id) from sources").fetchone()[0],
        )
        owners = cast(
            "list[tuple[int]]",
            connection.execute(
                """
                select source_id from health_types
                where type_code = 'synthetic_unused_type'
                """
            ).fetchall(),
        )
        assert other not in {row[0] for row in owners}
        _ = connection.execute(
            """
            insert into deleted_records
                (source_id, record_family, client_record_id, deleted_at)
            values (?, 'sample',
                'hk-quantity-synthetic-unused-type-'
                || '12345678-1234-1234-1234-123456789012', ?)
            """,
            (other, "2026-01-01T00:00:00Z"),
        )

    _ = prune_unreferenced_types(db_path, apply=True, min_age_days=0)

    with sqlite3.connect(db_path) as connection:
        kept = cast(
            "int",
            connection.execute(
                """
                select count(*) from health_types
                where type_code = 'synthetic_unused_type'
                """
            ).fetchone()[0],
        )
    assert kept >= 1

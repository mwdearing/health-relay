"""Storage tests for lab result rows (migration 012)."""

import sqlite3
from pathlib import Path
from typing import TypeAlias, cast

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import ingest_batch, ingest_fixture
from health_bridge.storage import initialize_database
from health_bridge.storage.sqlite_rows import fetch_one_int

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.lab_result.synthetic.json")
LabRow: TypeAlias = tuple[str, str, str, float, str, float, float]
LATEST_LAB_COUNT_SQL = (
    "select lab_result_count from sync_runs order by sync_run_id desc limit 1"
)
MIGRATION_ROW_SQL = (
    "select count(*) from schema_migrations where migration_id = '012_lab_results'"
)
LAB_ROW_SQL = (
    "select loinc, name, effective_date, value_num, unit, ref_low, ref_high "
    "from lab_results"
)


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bridge.sqlite"
    initialize_database(db_path)
    return db_path


def _lab_row(connection: sqlite3.Connection) -> LabRow:
    row = cast("LabRow | None", connection.execute(LAB_ROW_SQL).fetchone())
    assert row is not None
    return row


def _fixture_batch() -> HealthBridgeBatchV1:
    return HealthBridgeBatchV1.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )


def test_ingest_stores_lab_result_row_and_counts_it(tmp_path: Path) -> None:
    db_path = _db(tmp_path)

    result = ingest_fixture(db_path, FIXTURE_PATH)

    assert result.lab_result_count == 1
    with sqlite3.connect(db_path) as connection:
        row = _lab_row(connection)
        assert row == ("2951-2", "Sodium", "2026-06-04", 140.0, "mmol/L", 136.0, 145.0)
        assert fetch_one_int(connection, LATEST_LAB_COUNT_SQL) == 1


def test_ingest_is_idempotent_and_updates_on_replay(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    corrected = _fixture_batch().model_copy(
        update={
            "lab_results": (
                _fixture_batch().lab_results[0].model_copy(update={"value_num": 141.0}),
            ),
        },
    )
    _ = ingest_batch(db_path, corrected, "replay")

    with sqlite3.connect(db_path) as connection:
        assert fetch_one_int(connection, "select count(*) from lab_results") == 1
        row = _lab_row(connection)
        assert row[3] == 141.0


def test_tombstoned_lab_result_is_deleted_and_not_resurrected(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    batch = _fixture_batch()
    tombstone = batch.deleted_records[0].model_copy(
        update={
            "record_family": "lab_result",
            "source_key": batch.lab_results[0].source_key,
            "client_record_id": batch.lab_results[0].client_record_id,
        },
    )
    deleting = batch.model_copy(update={"deleted_records": (tombstone,)})
    _ = ingest_batch(db_path, deleting, "delete")
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    with sqlite3.connect(db_path) as connection:
        assert fetch_one_int(connection, "select count(*) from lab_results") == 0


def test_migration_adds_table_and_sync_run_column_to_existing_database(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        pragma_rows = cast(
            "list[tuple[int, str, str, int, object, int]]",
            connection.execute("pragma table_info(sync_runs)").fetchall(),
        )
        assert "lab_result_count" in {row[1] for row in pragma_rows}
        assert fetch_one_int(connection, MIGRATION_ROW_SQL) == 1

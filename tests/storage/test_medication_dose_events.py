"""Storage tests for medication dose event rows (migration 011)."""

import sqlite3
from pathlib import Path
from typing import TypeAlias, cast

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import ingest_batch, ingest_fixture
from health_bridge.storage import initialize_database
from health_bridge.storage.sqlite_rows import fetch_one_int

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.medication.synthetic.json")
MedRow: TypeAlias = tuple[
    str, str | None, str, int, str, str | None, float | None, str | None
]
LATEST_MED_COUNT_SQL = (
    "select medication_dose_event_count from sync_runs "
    "order by sync_run_id desc limit 1"
)
MIGRATION_ROW_SQL = (
    "select count(*) from schema_migrations "
    "where migration_id = '011_medication_dose_events'"
)
MED_ROW_SQL = (
    "select medication_name, medication_concept_key, status, "
    "status_raw, start_time, scheduled_time, dose, unit "
    "from medication_dose_events"
)


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bridge.sqlite"
    initialize_database(db_path)
    return db_path


def _med_row(connection: sqlite3.Connection) -> MedRow:
    row = cast("MedRow | None", connection.execute(MED_ROW_SQL).fetchone())
    assert row is not None
    return row


def _fixture_batch() -> HealthBridgeBatchV1:
    return HealthBridgeBatchV1.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )


def test_ingest_stores_medication_dose_event_row_and_counts_it(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)

    result = ingest_fixture(db_path, FIXTURE_PATH)

    assert result.medication_dose_event_count == 1
    with sqlite3.connect(db_path) as connection:
        row = _med_row(connection)
        assert row == (
            "Synthetic Vitamin",
            "synthetic-concept-0001",
            "taken",
            1,
            "2026-06-04T13:05:00Z",
            "2026-06-04T13:00:00Z",
            1.0,
            "count",
        )
        assert fetch_one_int(connection, LATEST_MED_COUNT_SQL) == 1


def test_ingest_is_idempotent_and_keeps_med_on_replay(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    batch = _fixture_batch()
    replay = batch.model_copy(
        update={
            "medication_dose_events": (
                batch.medication_dose_events[0].model_copy(
                    update={"status": "taken_late"},
                ),
            ),
        },
    )
    _ = ingest_batch(db_path, replay, "replay")

    with sqlite3.connect(db_path) as connection:
        assert (
            fetch_one_int(connection, "select count(*) from medication_dose_events")
            == 1
        )
        row = _med_row(connection)
        assert row[2] == "taken_late"


def test_tombstoned_medication_dose_event_is_deleted_and_not_resurrected(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    batch = _fixture_batch()
    tombstone = batch.deleted_records[0].model_copy(
        update={
            "record_family": "medication_dose_event",
            "source_key": batch.medication_dose_events[0].source_key,
            "client_record_id": batch.medication_dose_events[0].client_record_id,
        },
    )
    deleting = batch.model_copy(update={"deleted_records": (tombstone,)})
    _ = ingest_batch(db_path, deleting, "delete")
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    with sqlite3.connect(db_path) as connection:
        assert (
            fetch_one_int(connection, "select count(*) from medication_dose_events")
            == 0
        )


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
        assert "medication_dose_event_count" in {row[1] for row in pragma_rows}
        assert fetch_one_int(connection, MIGRATION_ROW_SQL) == 1

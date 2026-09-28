"""Storage tests for electrocardiogram rows (migration 010)."""

import json
import sqlite3
from pathlib import Path
from typing import TypeAlias, cast

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import ingest_batch, ingest_fixture
from health_bridge.storage import initialize_database
from health_bridge.storage.sqlite_rows import fetch_one_int

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.electrocardiogram.synthetic.json")
EcgRow: TypeAlias = tuple[str, str, float, float, int, str]
VOLTAGES = [-12.5, 3.0, 41.75, 120.0, -30.25, -8.0, 2.5, 0.0]
LATEST_ECG_COUNT_SQL = (
    "select electrocardiogram_count from sync_runs order by sync_run_id desc limit 1"
)
MIGRATION_ROW_SQL = (
    "select count(*) from schema_migrations "
    "where migration_id = '010_electrocardiograms'"
)
ECG_ROW_SQL = (
    "select classification, symptoms_status, average_heart_rate_bpm, "
    "sampling_frequency_hz, voltage_count, voltages_json from electrocardiograms"
)


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bridge.sqlite"
    initialize_database(db_path)
    return db_path


def _ecg_row(connection: sqlite3.Connection) -> EcgRow:
    row = cast("EcgRow | None", connection.execute(ECG_ROW_SQL).fetchone())
    assert row is not None
    return row


def _fixture_batch() -> HealthBridgeBatchV1:
    return HealthBridgeBatchV1.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )


def test_ingest_stores_electrocardiogram_row_and_counts_it(tmp_path: Path) -> None:
    db_path = _db(tmp_path)

    result = ingest_fixture(db_path, FIXTURE_PATH)

    assert result.electrocardiogram_count == 1
    with sqlite3.connect(db_path) as connection:
        row = _ecg_row(connection)
        assert row[:5] == ("sinus_rhythm", "none", 62, 512, 8)
        assert json.loads(row[5]) == VOLTAGES
        assert fetch_one_int(connection, LATEST_ECG_COUNT_SQL) == 1


def test_ingest_is_idempotent_and_keeps_voltages_on_summary_replay(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    summary_only = _fixture_batch().model_copy(
        update={
            "electrocardiograms": (
                _fixture_batch()
                .electrocardiograms[0]
                .model_copy(
                    update={
                        "voltages_microvolts": (),
                        "classification": "inconclusive_other",
                    },
                ),
            ),
        },
    )
    _ = ingest_batch(db_path, summary_only, "replay")

    with sqlite3.connect(db_path) as connection:
        assert fetch_one_int(connection, "select count(*) from electrocardiograms") == 1
        row = _ecg_row(connection)
        assert row[0] == "inconclusive_other"
        assert json.loads(row[5]) == VOLTAGES


def test_tombstoned_electrocardiogram_is_deleted_and_not_resurrected(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    batch = _fixture_batch()
    tombstone = batch.deleted_records[0].model_copy(
        update={
            "record_family": "electrocardiogram",
            "source_key": batch.electrocardiograms[0].source_key,
            "client_record_id": batch.electrocardiograms[0].client_record_id,
        },
    )
    deleting = batch.model_copy(update={"deleted_records": (tombstone,)})
    _ = ingest_batch(db_path, deleting, "delete")
    _ = ingest_fixture(db_path, FIXTURE_PATH)

    with sqlite3.connect(db_path) as connection:
        assert fetch_one_int(connection, "select count(*) from electrocardiograms") == 0


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
        assert "electrocardiogram_count" in {row[1] for row in pragma_rows}
        assert fetch_one_int(connection, MIGRATION_ROW_SQL) == 1

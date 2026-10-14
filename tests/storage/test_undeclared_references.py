"""Undeclared type and source references are refused without a sync run (RELAY-S13)."""

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Final, TypeAlias, cast

import pytest

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import ingest_batch, ingest_batch_in_connection
from health_bridge.storage import initialize_database
from health_bridge.storage.catalog import UndeclaredReferenceError
from health_bridge.storage.models import IngestResult

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.synthetic.json")
JsonObject: TypeAlias = dict[str, object]
SYNC_RUN_COUNT_SQL = "select count(*) from sync_runs"


def _batch(**sample_overrides: object) -> HealthBridgeBatchV1:
    payload = cast("JsonObject", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    samples = cast("list[JsonObject]", payload["samples"])
    samples[0].update(sample_overrides)
    return HealthBridgeBatchV1.model_validate_json(json.dumps(payload))


def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "undeclared.sqlite"
    initialize_database(db_path)
    return db_path


def _sync_run_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = cast("tuple[int]", connection.execute(SYNC_RUN_COUNT_SQL).fetchone())
    return row[0]


def test_undeclared_type_code_is_refused_without_a_sync_run(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    batch = _batch(type_code="synthetic_never_declared")

    with pytest.raises(UndeclaredReferenceError):
        _ = ingest_batch(db_path, batch, "test")

    assert _sync_run_count(db_path) == 0


def test_undeclared_source_key_is_refused_without_a_sync_run(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    batch = _batch(source_key="synthetic.never.declared")

    with pytest.raises(UndeclaredReferenceError):
        _ = ingest_batch(db_path, batch, "test")

    assert _sync_run_count(db_path) == 0


def test_declared_references_still_ingest(tmp_path: Path) -> None:
    db_path = _db(tmp_path)

    result = ingest_batch(db_path, _batch(), "test")

    assert result.status == "succeeded"
    assert _sync_run_count(db_path) == 1


UNKNOWN_SOURCE = "synthetic.never.declared"
UNKNOWN_TYPE = "synthetic_never_declared"
ECG_FIXTURE_PATH = Path(
    "fixtures/health_bridge_batch_v1.electrocardiogram.synthetic.json"
)
MEDICATION_FIXTURE_PATH = Path(
    "fixtures/health_bridge_batch_v1.medication.synthetic.json"
)
EXPORT_SOURCE_KEY = "apple_health.export"
LAB_ROW: JsonObject = {
    "client_record_id": "synthetic-lab-1",
    "source_key": "synthetic.phone.alpha",
    "name": "Synthetic Glucose",
    "effective_date": "2026-06-04",
    "value_num": 5.1,
    "unit": "mmol/L",
}
WRITE_STATEMENT = re.compile(r"\s*(insert|update|delete)\b", re.IGNORECASE)


def _payload(path: Path = FIXTURE_PATH) -> JsonObject:
    return cast("JsonObject", json.loads(path.read_text(encoding="utf-8")))


def _rows(payload: JsonObject, field: str) -> list[JsonObject]:
    return cast("list[JsonObject]", payload[field])


def _validated(payload: JsonObject) -> HealthBridgeBatchV1:
    return HealthBridgeBatchV1.model_validate_json(json.dumps(payload))


def _ingest_payload(db_path: Path, payload: JsonObject) -> IngestResult:
    return ingest_batch(db_path, _validated(payload), "test")


def _set_source_on_cursor(payload: JsonObject) -> None:
    cursors = cast("list[JsonObject]", cast("JsonObject", payload["sync"])["cursors"])
    cursors[0]["source_key"] = UNKNOWN_SOURCE


def _set_source_on_lab(payload: JsonObject) -> None:
    payload["lab_results"] = [{**LAB_ROW, "source_key": UNKNOWN_SOURCE}]


def _set_source(field: str) -> Callable[[JsonObject], None]:
    def apply(payload: JsonObject) -> None:
        _rows(payload, field)[0]["source_key"] = UNKNOWN_SOURCE

    return apply


UNKNOWN_SOURCE_CASES: Final[list[tuple[str, Path, Callable[[JsonObject], None]]]] = [
    ("samples", FIXTURE_PATH, _set_source("samples")),
    ("workouts", FIXTURE_PATH, _set_source("workouts")),
    ("sleep_sessions", FIXTURE_PATH, _set_source("sleep_sessions")),
    ("deleted_records", FIXTURE_PATH, _set_source("deleted_records")),
    ("sync.cursors", FIXTURE_PATH, _set_source_on_cursor),
    ("lab_results", FIXTURE_PATH, _set_source_on_lab),
    ("electrocardiograms", ECG_FIXTURE_PATH, _set_source("electrocardiograms")),
    (
        "medication_dose_events",
        MEDICATION_FIXTURE_PATH,
        _set_source("medication_dose_events"),
    ),
]


@pytest.mark.parametrize(
    ("base_path", "apply_unknown_source"),
    [(path, apply) for _, path, apply in UNKNOWN_SOURCE_CASES],
    ids=[kind for kind, _, _ in UNKNOWN_SOURCE_CASES],
)
def test_unknown_source_on_each_row_kind_is_named_and_refused(
    tmp_path: Path,
    base_path: Path,
    apply_unknown_source: Callable[[JsonObject], None],
) -> None:
    db_path = _db(tmp_path)
    payload = _payload(base_path)
    apply_unknown_source(payload)

    with pytest.raises(UndeclaredReferenceError) as refused:
        _ = _ingest_payload(db_path, payload)

    assert "undeclared source" in str(refused.value)
    assert UNKNOWN_SOURCE in str(refused.value)
    assert refused.value.__cause__ is None
    assert _sync_run_count(db_path) == 0


def test_undeclared_type_code_is_named_and_refused(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    payload = _payload()
    _rows(payload, "samples")[0]["type_code"] = UNKNOWN_TYPE

    with pytest.raises(UndeclaredReferenceError) as refused:
        _ = _ingest_payload(db_path, payload)

    assert "undeclared type" in str(refused.value)
    assert UNKNOWN_TYPE in str(refused.value)
    assert refused.value.__cause__ is None
    assert _sync_run_count(db_path) == 0


def test_refused_batch_writes_no_row_before_the_undeclared_reference(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    payload = _payload()
    _rows(payload, "samples")[2]["type_code"] = UNKNOWN_TYPE
    statements: list[str] = []
    connection = sqlite3.connect(db_path)
    try:
        connection.set_trace_callback(statements.append)
        _ = connection.execute("pragma foreign_keys = on")
        with pytest.raises(UndeclaredReferenceError):
            _ = ingest_batch_in_connection(connection, _validated(payload), "test")
        connection.rollback()
    finally:
        connection.close()

    row_writes = [
        statement
        for statement in statements
        if WRITE_STATEMENT.match(statement)
        and "sync_runs" not in statement
        and not any(
            table in statement
            for table in ("sources", "health_types", "health_type_aliases")
        )
    ]
    assert row_writes == []
    assert _sync_run_count(db_path) == 0


def test_type_stored_by_an_earlier_accepted_batch_is_still_accepted(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    earlier = _payload()
    _rows(earlier, "health_types").append(
        {
            "type_code": "synthetic_earlier_type",
            "display_name": "Synthetic Earlier",
            "category": "activity",
            "default_unit": "count",
            "sensitivity": "low",
            "aliases": [],
        }
    )
    earlier_sample = {
        **_rows(earlier, "samples")[0],
        "client_record_id": "synthetic-earlier-type-sample",
        "type_code": "synthetic_earlier_type",
    }
    _rows(earlier, "samples").append(earlier_sample)
    _ = _ingest_payload(db_path, earlier)

    later = _payload()
    _rows(later, "samples")[0]["type_code"] = "synthetic_earlier_type"

    result = _ingest_payload(db_path, later)

    assert result.status == "succeeded"
    assert _sync_run_count(db_path) == 2


def test_alias_coded_sample_is_checked_against_its_canonical_type(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    payload = _payload()
    _rows(payload, "health_types").append(
        {
            "type_code": "energy",
            "display_name": "Energy",
            "category": "activity",
            "default_unit": "kcal",
            "sensitivity": "low",
            "aliases": [],
        }
    )
    _rows(payload, "samples")[0]["type_code"] = "active_energy"
    _rows(payload, "samples")[0]["unit"] = "kcal"

    result = _ingest_payload(db_path, payload)

    assert result.status == "succeeded"


def test_tombstoned_sample_with_undeclared_type_is_still_accepted(
    tmp_path: Path,
) -> None:
    db_path = _db(tmp_path)
    tombstone = _payload()
    tombstone["samples"] = []
    tombstone["deleted_records"] = [
        {
            "record_family": "sample",
            "source_key": "synthetic.phone.alpha",
            "client_record_id": "synthetic-sample-tombstoned-20260601",
            "deleted_at": "2026-06-05T09:15:00Z",
        }
    ]
    _ = _ingest_payload(db_path, tombstone)

    later = _payload()
    _rows(later, "samples").append(
        {
            **_rows(later, "samples")[0],
            "client_record_id": "synthetic-sample-tombstoned-20260601",
            "type_code": UNKNOWN_TYPE,
        }
    )

    result = _ingest_payload(db_path, later)

    assert result.status == "succeeded"


def test_declared_export_source_keeps_lab_results_accepted(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    payload = _payload()
    _rows(payload, "sources").append(
        {"source_key": EXPORT_SOURCE_KEY, "name": "Health export", "kind": "manual"}
    )
    payload["lab_results"] = [{**LAB_ROW, "source_key": EXPORT_SOURCE_KEY}]

    result = _ingest_payload(db_path, payload)

    assert result.status == "succeeded"


def test_undeclared_export_source_is_refused_for_lab_results(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    payload = _payload()
    payload["lab_results"] = [{**LAB_ROW, "source_key": EXPORT_SOURCE_KEY}]

    with pytest.raises(UndeclaredReferenceError) as refused:
        _ = _ingest_payload(db_path, payload)

    assert EXPORT_SOURCE_KEY in str(refused.value)
    assert _sync_run_count(db_path) == 0

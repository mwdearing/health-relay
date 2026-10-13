"""Undeclared type and source references are refused without a sync run (RELAY-S13)."""

import json
import sqlite3
from pathlib import Path
from typing import TypeAlias, cast

import pytest

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import UndeclaredReferenceError, ingest_batch
from health_bridge.storage import initialize_database

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

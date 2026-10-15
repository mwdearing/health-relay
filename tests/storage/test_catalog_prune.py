"""RELAY-S14: unreferenced type catalog rows can be pruned; referenced ones stay."""

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import cast

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

    result = prune_unreferenced_types(db_path, apply=False)

    assert result.type_rows >= 1
    assert not result.applied
    assert _count(db_path, COUNT_SQL) == before


def test_apply_removes_only_unreferenced_rows_and_their_aliases(tmp_path: Path) -> None:
    db_path = tmp_path / "prune.sqlite"
    initialize_database(db_path)
    _ingest_fixture(db_path)
    used = _count(db_path, USED_SQL)
    samples = _count(db_path, "select count(*) from samples")

    result = prune_unreferenced_types(db_path, apply=True)

    assert result.applied
    assert _count(db_path, COUNT_SQL) == used
    assert _count(db_path, "select count(*) from samples") == samples
    assert (
        _count(
            db_path,
            """
            select count(*) from health_type_aliases
            where type_code = 'synthetic_unused_type'
            """,
        )
        == 0
    )
    assert prune_unreferenced_types(db_path, apply=True).type_rows == 0


def test_dry_run_on_a_missing_database_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nested" / "missing.sqlite"
    with contextlib.suppress(OSError, sqlite3.Error):
        _ = prune_unreferenced_types(missing, apply=False)
    assert not missing.parent.exists()

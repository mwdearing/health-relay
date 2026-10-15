"""RELAY-S15: a batch may add health types but never rewrite a stored one."""

import json
import sqlite3
from pathlib import Path
from typing import TypeAlias, cast

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.ingest import ingest_batch
from health_bridge.storage import initialize_database

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.synthetic.json")
JsonObject: TypeAlias = dict[str, object]
TYPE_ROW_SQL = (
    "select display_name, category, default_unit, sensitivity "
    "from health_types where type_code = ?"
)
ALIAS_SQL = "select alias from health_type_aliases where type_code = ? order by alias"


def _payload() -> JsonObject:
    return cast("JsonObject", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _types(payload: JsonObject) -> list[JsonObject]:
    return cast("list[JsonObject]", payload["health_types"])


def _ingest(db_path: Path, payload: JsonObject) -> str:
    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))
    return ingest_batch(db_path, batch, "test").status


def _type_row(db_path: Path, type_code: str) -> tuple[str, str, str, str]:
    with sqlite3.connect(db_path) as connection:
        return cast(
            "tuple[str, str, str, str]",
            connection.execute(TYPE_ROW_SQL, (type_code,)).fetchone(),
        )


def _aliases(db_path: Path, type_code: str) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = cast(
            "list[tuple[str]]", connection.execute(ALIAS_SQL, (type_code,)).fetchall()
        )
    return [row[0] for row in rows]


def test_later_batch_cannot_rewrite_stored_type_metadata(tmp_path: Path) -> None:
    # Given a type stored by an earlier batch
    db_path = tmp_path / "catalog.sqlite"
    initialize_database(db_path)
    first = _payload()
    _ = _ingest(db_path, first)
    declared = _types(first)[0]
    type_code = cast("str", declared["type_code"])
    before = _type_row(db_path, type_code)

    # When a later batch declares the same type with different metadata
    later = _payload()
    _types(later)[0].update(
        {"display_name": "Synthetic Relabel", "default_unit": "synthetic_unit"}
    )
    status = _ingest(db_path, later)

    # Then the batch is accepted but the stored metadata is unchanged
    assert status == "succeeded"
    assert _type_row(db_path, type_code) == before
    assert before[0] != "Synthetic Relabel"


def test_new_types_and_aliases_are_still_added(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite"
    initialize_database(db_path)
    _ = _ingest(db_path, _payload())

    later = _payload()
    existing_code = cast("str", _types(later)[0]["type_code"])
    _types(later)[0]["aliases"] = ["synthetic_added_alias"]
    _types(later).append(
        {
            "type_code": "synthetic_new_type",
            "display_name": "Synthetic New",
            "category": "activity",
            "default_unit": "count",
            "sensitivity": "low",
            "aliases": [],
        }
    )
    assert _ingest(db_path, later) == "succeeded"

    assert _type_row(db_path, "synthetic_new_type") == (
        "Synthetic New",
        "activity",
        "count",
        "low",
    )
    assert "synthetic_added_alias" in _aliases(db_path, existing_code)


def _source_scoped_row(
    db_path: Path, source_key: str, type_code: str
) -> tuple[str, str] | None:
    with sqlite3.connect(db_path) as connection:
        return cast(
            "tuple[str, str] | None",
            connection.execute(
                """
                select t.display_name, t.default_unit from health_types as t
                join sources as s on s.source_id = t.source_id
                where s.source_key = ? and t.type_code = ?
                """,
                (source_key, type_code),
            ).fetchone(),
        )


def test_each_source_owns_its_own_type_metadata(tmp_path: Path) -> None:
    # Given a type declared by the first batch's sources
    db_path = tmp_path / "catalog.sqlite"
    initialize_database(db_path)
    first = _payload()
    _ = _ingest(db_path, first)
    type_code = cast("str", _types(first)[0]["type_code"])
    owner = cast("str", cast("list[JsonObject]", first["sources"])[0]["source_key"])
    before = _source_scoped_row(db_path, owner, type_code)

    # When another source declares the same type code with different metadata
    other = _payload()
    other["sources"] = [
        {"source_key": "synthetic.other", "name": "Other", "kind": "app"}
    ]
    other["samples"] = []
    other["workouts"] = []
    other["sleep_sessions"] = []
    other["deleted_records"] = []
    other["electrocardiograms"] = []
    other["medication_dose_events"] = []
    other["lab_results"] = []
    _types(other)[0].update(
        {"type_code": type_code, "display_name": "Other Label", "default_unit": "zz"}
    )
    sync = cast("JsonObject", other["sync"])
    sync["cursors"] = []
    assert _ingest(db_path, other) == "succeeded"

    # Then each source keeps its own row and the first source is untouched
    assert _source_scoped_row(db_path, owner, type_code) == before
    assert _source_scoped_row(db_path, "synthetic.other", type_code) == (
        "Other Label",
        "zz",
    )


def test_aliases_are_bounded_per_owned_type(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite"
    initialize_database(db_path)
    first = _payload()
    _ = _ingest(db_path, first)
    type_code = cast("str", _types(first)[0]["type_code"])
    for round_index in range(8):
        later = _payload()
        _types(later)[0]["aliases"] = [
            f"synthetic_alias_{round_index}_{n}" for n in range(16)
        ]
        assert _ingest(db_path, later) == "succeeded"

    with sqlite3.connect(db_path) as connection:
        counts = cast(
            "list[tuple[int]]",
            connection.execute(
                """
                select count(*) from health_type_aliases
                where type_code = ? group by source_id
                """,
                (type_code,),
            ).fetchall(),
        )
    assert counts
    assert all(count <= 64 for (count,) in counts)

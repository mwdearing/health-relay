"""Upstream migrations 010 to 014 ported as 017 to 021, after this fork's 016.

Upstream's `010_daily_projection_state`, `011_daily_projection_fence_order`,
`012_catalogue_coverage_index`, `013_mailbox_enrollment` and
`014_uv_raw_aggregation` reuse numbers this fork already applied to real
databases (`010_electrocardiograms` to `014_intake_context_tokens`), so they are
carried forward under the next free numbers with their SQL unchanged. An applied
migration is never renumbered or edited.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter

import health_bridge.storage.database as database_module
from health_bridge.storage import initialize_database
from tests.fixture_helpers import initialized_fixture_db

MIGRATION_DIR = Path("src/health_bridge/storage/migrations")
FORK_LAST_BEFORE_PORT = "016_source_scoped_types"
PORTED = {
    "017_daily_projection_state": "010_daily_projection_state",
    "018_daily_projection_fence_order": "011_daily_projection_fence_order",
    "019_catalogue_coverage_index": "012_catalogue_coverage_index",
    "020_mailbox_enrollment": "013_mailbox_enrollment",
    "021_uv_raw_aggregation": "014_uv_raw_aggregation",
}
NEW_TABLES = {
    "daily_projection_owners",
    "daily_projection_records",
    "daily_projection_fences",
    "daily_projection_replays",
}
NEW_INDEXES = {
    "daily_projection_record_identity",
    "daily_projection_record_ranges",
    "daily_projection_fence_ranges",
    "daily_projection_fence_order",
    "samples_type_coverage",
}
NEW_COLUMNS = {
    ("pairing_invitation_redemptions", "mailbox_enrollment_sha256"),
    ("pairing_invitation_redemptions", "native_request_sha256"),
    ("pairing_invitations", "native_cancel_sha256"),
}
ALTERED_TABLES = {"pairing_invitations", "pairing_invitation_redemptions"}
METADATA_ADAPTER = TypeAdapter(dict[str, object])
NAMES_SQL = """
select name from sqlite_master
where type = ? and name not like 'sqlite_%'
"""
INSERT_INVITATION_SQL = """
insert into pairing_invitations (
    pairing_invitation_id, invitation_label, receiver_url,
    invitation_secret_hash, invitation_code_selector, invitation_code_hash,
    invitation_code_salt, created_at, expires_at, failed_attempt_count,
    max_failed_attempts, transport, native_cancel_sha256
) values (
    'synthetic-invitation', 'synthetic', 'http://127.0.0.1:1', 'h', 's', 'h',
    's', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', 0, 5, 'direct', ?
)
"""
INSERT_HEALTH_TYPE_SQL = """
insert or ignore into health_types (
    source_id, type_code, display_name, category, default_unit, sensitivity
) values (?, ?, ?, 'environmental', 'count', 'moderate')
"""
INSERT_SAMPLE_SQL = """
insert into samples (
    source_id, type_code, client_record_id, start_time, end_time, value, unit,
    metadata_json
) values (
    ?, ?, ?, '2026-01-02T08:00:00Z', '2026-01-02T08:00:00Z', 1.0, 'count', ?
)
"""
SELECT_UV_ROWS_SQL = """
select client_record_id, metadata_json from samples
where client_record_id like 'uv-%' or client_record_id = 'other-type'
"""
UV_METADATA = {
    "sample_kind": "raw_quantity",
    "healthkit_identifier": "HKQuantityTypeIdentifierUVExposure",
    "keep": {"synthetic": True},
}


def _names(connection: sqlite3.Connection, kind: str) -> set[str]:
    rows = cast(
        "list[tuple[str]]",
        connection.execute(NAMES_SQL, (kind,)).fetchall(),
    )
    return {row[0] for row in rows}


def _columns(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for table in ALTERED_TABLES:
        rows = cast(
            "list[tuple[int, str]]",
            connection.execute(f"pragma table_info({table})").fetchall(),
        )
        result |= {(table, row[1]) for row in rows}
    return result


def _schema_sql(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    rows = cast(
        "list[tuple[str, str, str | None]]",
        connection.execute(
            "select type, name, sql from sqlite_master where name not like 'sqlite_%'"
        ).fetchall(),
    )
    return {(kind, name): sql or "" for kind, name, sql in rows}


def _counts(connection: sqlite3.Connection, tables: set[str]) -> dict[str, int]:
    return {
        table: cast(
            "tuple[int]",
            connection.execute(
                f'select count(*) from "{table}"'  # noqa: S608
            ).fetchone(),
        )[0]
        for table in tables
    }


def _database_at_016(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ids = database_module.MIGRATION_IDS
    assert ids[: len(ids) - len(PORTED)][-1] == FORK_LAST_BEFORE_PORT
    with monkeypatch.context() as hooks:
        hooks.setattr(database_module, "MIGRATION_IDS", ids[: len(ids) - len(PORTED)])
        return initialized_fixture_db(tmp_path)


def test_ported_migrations_follow_016_in_upstream_order() -> None:
    ids = database_module.MIGRATION_IDS
    start = ids.index(FORK_LAST_BEFORE_PORT) + 1
    assert ids[start:] == tuple(PORTED)
    assert list(ids) == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_ported_sql_files_exist_and_name_their_upstream_origin() -> None:
    for ours, upstream in PORTED.items():
        text = (MIGRATION_DIR / f"{ours}.sql").read_text(encoding="utf-8")
        assert f"Ported from upstream {upstream}" in text
        assert f"renumbered to {ours}" in text


def test_fresh_database_has_every_ported_object(tmp_path: Path) -> None:
    path = tmp_path / "fresh.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as connection:
        assert _names(connection, "table") >= NEW_TABLES
        assert _names(connection, "index") >= NEW_INDEXES
        assert _columns(connection) >= NEW_COLUMNS
        applied = {
            row[0]
            for row in cast(
                "list[tuple[str]]",
                connection.execute(
                    "select migration_id from schema_migrations"
                ).fetchall(),
            )
        }
        assert set(PORTED) <= applied
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        assert connection.execute("pragma integrity_check").fetchall() == [("ok",)]


def test_database_at_016_upgrades_in_place_with_its_data_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _database_at_016(tmp_path, monkeypatch)
    with closing(sqlite3.connect(path)) as connection:
        before_sql = _schema_sql(connection)
        tables = {
            name
            for kind, name in before_sql
            if kind == "table" and name != "schema_migrations"
        }
        before_counts = _counts(connection, tables)
    assert not NEW_TABLES & tables
    assert before_counts["samples"] > 0

    initialize_database(path)

    with closing(sqlite3.connect(path)) as connection:
        after_sql = _schema_sql(connection)
        untouched = {
            key: sql
            for key, sql in before_sql.items()
            if not (key[0] == "table" and key[1] in ALTERED_TABLES)
        }
        assert {k: v for k, v in after_sql.items() if k in untouched} == untouched
        added = {key for key in after_sql if key not in before_sql}
        assert {name for kind, name in added if kind == "table"} == NEW_TABLES
        assert {name for kind, name in added if kind == "index"} >= NEW_INDEXES
        assert _counts(connection, tables) == before_counts
        assert _counts(connection, NEW_TABLES) == dict.fromkeys(NEW_TABLES, 0)
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        assert connection.execute("pragma integrity_check").fetchall() == [("ok",)]
        ids = [
            row[0]
            for row in cast(
                "list[tuple[str]]",
                connection.execute(
                    "select migration_id from schema_migrations order by 1"
                ).fetchall(),
            )
        ]
        assert ids == sorted(database_module.MIGRATION_IDS)


def test_initialize_database_is_idempotent_for_the_ported_objects(
    tmp_path: Path,
) -> None:
    path = tmp_path / "idempotent.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as connection:
        first = _schema_sql(connection)
    initialize_database(path)
    initialize_database(path)
    with closing(sqlite3.connect(path)) as connection:
        assert _schema_sql(connection) == first
        count = cast(
            "tuple[int]",
            connection.execute("select count(*) from schema_migrations").fetchone(),
        )[0]
    assert count == len(database_module.MIGRATION_IDS)


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (None, True),
        ("a" * 64, True),
        ("A" * 64, False),
        ("a" * 63, False),
        ("g" * 64, False),
    ],
)
def test_native_cancel_hash_column_is_null_or_lowercase_sha256_hex(
    tmp_path: Path, value: str | None, accepted: bool
) -> None:
    path = tmp_path / "pairing.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as connection:
        if accepted:
            _ = connection.execute(INSERT_INVITATION_SQL, (value,))
        else:
            with pytest.raises(sqlite3.IntegrityError):
                _ = connection.execute(INSERT_INVITATION_SQL, (value,))


def _add_uv_sample(
    connection: sqlite3.Connection, client_id: str, type_code: str, metadata: str
) -> None:
    source_id = cast(
        "tuple[int]",
        connection.execute("select min(source_id) from sources").fetchone(),
    )[0]
    _ = connection.execute(INSERT_HEALTH_TYPE_SQL, (source_id, type_code, type_code))
    _ = connection.execute(
        INSERT_SAMPLE_SQL, (source_id, type_code, client_id, metadata)
    )


def test_uv_raw_aggregation_backfill_relabels_only_raw_sum_uv_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database_at_016(tmp_path, monkeypatch)
    cases = {
        "uv-raw-sum": ("uv_exposure", {**UV_METADATA, "aggregation": "sum"}),
        "uv-raw-minmax": (
            "uv_exposure",
            {**UV_METADATA, "aggregation": "min_max_average"},
        ),
        "uv-daily": (
            "uv_exposure",
            {"sample_kind": "daily_total", "aggregation": "sum"},
        ),
        "uv-no-kind": ("uv_exposure", {"aggregation": "sum"}),
        "other-type": ("steps", {**UV_METADATA, "aggregation": "sum"}),
    }
    with closing(sqlite3.connect(path)) as connection:
        for client_id, (type_code, metadata) in cases.items():
            _add_uv_sample(connection, client_id, type_code, json.dumps(metadata))
        _add_uv_sample(connection, "uv-not-json", "uv_exposure", "not json")
        connection.commit()

    initialize_database(path)

    with closing(sqlite3.connect(path)) as connection:
        stored = {
            row[0]: row[1]
            for row in cast(
                "list[tuple[str, str]]",
                connection.execute(SELECT_UV_ROWS_SQL).fetchall(),
            )
        }
    relabelled = METADATA_ADAPTER.validate_json(stored["uv-raw-sum"])
    assert relabelled["aggregation"] == "min_max_average"
    without_label = {
        key: value for key, value in relabelled.items() if key != "aggregation"
    }
    assert without_label == UV_METADATA
    for untouched in ("uv-raw-minmax", "uv-daily", "uv-no-kind", "other-type"):
        assert METADATA_ADAPTER.validate_json(stored[untouched]) == cases[untouched][1]
    assert stored["uv-not-json"] == "not json"

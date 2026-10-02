"""Tests for migration 013 and the intake-context storage module."""

import json
import re
import sqlite3
from collections.abc import Callable, Generator, Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Final, cast, final

import pytest

import health_bridge.storage.database as database_module
from health_bridge.contract.intake_context_v1 import (
    Fact,
    HealthKitLink,
    UpsertOperation,
    validate_batch,
)
from health_bridge.storage import initialize_database, intake_context
from health_bridge.storage.intake_context import (
    BlendMemberRecord,
    FactRecord,
    IntakeNonTerminalOutcomeError,
    IntakeOperationConflictError,
    IntakeProducerConflictError,
    IntakeProjectionConflictError,
    IntakeRevisionConflictError,
    IntakeStateRecord,
    IntakeTombstoneConflictError,
    IntakeTransactionRequiredError,
    OperationReceiptRecord,
    ProducerRecord,
    ProjectionRecord,
    RevisionRecord,
    SampleLink,
    TombstoneRecord,
    append_operation_receipt,
    insert_projection_snapshot,
    insert_revision,
    list_links_by_component_id,
    list_links_by_sample_uuid,
    read_intake_state,
    read_operation_receipt,
    read_producer,
    read_revision,
    read_tombstone,
    register_producer,
    write_intake_state,
    write_tombstone,
)

FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "intake_context"
MIGRATION_FILE: Final = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "health_bridge"
    / "storage"
    / "migrations"
    / "013_intake_context.sql"
)
OWNER: Final = "owner-1"
NOW: Final = "2026-10-01T00:00:00Z"
HASH_A: Final = "sha256:" + "a" * 64
HASH_B: Final = "sha256:" + "b" * 64
HASH_C: Final = "sha256:" + "c" * 64
INTAKE_TABLES: Final = (
    "intake_producers",
    "intake_state",
    "intake_revisions",
    "intake_compound_facts",
    "intake_blend_members",
    "intake_projection_snapshots",
    "intake_sample_links",
    "intake_operation_receipts",
    "intake_tombstones",
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "receiver.sqlite"
    initialize_database(path)
    return path


@pytest.fixture
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    _ = conn.execute("pragma foreign_keys = on")
    yield conn
    conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[None]:
    _ = conn.execute("begin immediate")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _fact_record(fact: Fact) -> FactRecord:
    members = tuple(
        BlendMemberRecord(label_name=m.label_name, amount=m.amount, unit=m.unit)
        for m in fact.members or []
    )
    return FactRecord(
        component_id=fact.component_id,
        kind=fact.kind,
        code=fact.code,
        label_name=fact.label_name,
        value_state=fact.value_state,
        amount=fact.amount,
        unit=fact.unit,
        quantity_basis=fact.quantity_basis,
        aggregation_role=fact.aggregation_role,
        provenance=fact.provenance,
        members=members,
    )


def _link_record(link: HealthKitLink) -> SampleLink:
    return SampleLink(
        component_id=link.component_id,
        healthkit_type=link.healthkit_type,
        sample_uuid=link.healthkit_sample_uuid,
        sync_identifier=link.sync_identifier,
        sync_version=link.sync_version,
        disposition=link.disposition,
    )


def revision_from_fixture(name: str, *, owner_id: str = OWNER) -> RevisionRecord:
    batch = validate_batch((FIXTURE_DIR / name).read_text())
    operation = batch.operations[0]
    assert isinstance(operation, UpsertOperation)
    return RevisionRecord(
        owner_id=owner_id,
        producer_id=batch.producer_id,
        intake_id=operation.intake_id,
        revision=operation.revision,
        domain_facts_hash=operation.domain_facts_hash,
        projection_hash=operation.projection_hash,
        client_payload_hash=operation.client_payload_hash,
        installation_id=batch.installation_id,
        operation_id=operation.operation_id,
        occurred_at=operation.occurred_at,
        time_zone=operation.time_zone,
        recorded_at=operation.recorded_at,
        category=operation.category,
        display_name=operation.display_name,
        serving_amount=operation.serving.amount,
        serving_unit=operation.serving.unit,
        nutrition_completeness=operation.nutrition_completeness,
        received_at=NOW,
        facts=tuple(_fact_record(f) for f in operation.facts),
        links=tuple(_link_record(link) for link in operation.healthkit_links),
    )


def _store(conn: sqlite3.Connection, record: RevisionRecord) -> int:
    with transaction(conn):
        return insert_revision(conn, record).revision_row_id


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    cursor = conn.execute(f"select count(*) from {table}")  # noqa: S608
    row = cast("tuple[int]", cursor.fetchone())
    return row[0]


def _rows(conn: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return cast("list[tuple[object, ...]]", conn.execute(sql).fetchall())


def _schema_sql(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    rows = cast(
        "list[tuple[str, str, str | None]]",
        conn.execute(
            "select type, name, sql from sqlite_master where name not like 'sqlite_%'"
        ).fetchall(),
    )
    return {(kind, name): sql or "" for kind, name, sql in rows}


def test_database_at_012_upgrades_in_place_with_its_data_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "old.sqlite"
    full_ids = database_module.MIGRATION_IDS
    assert full_ids[-1] == "013_intake_context"
    monkeypatch.setattr(database_module, "MIGRATION_IDS", full_ids[:-1])
    initialize_database(path)
    with closing(sqlite3.connect(path)) as conn:
        _ = conn.execute(
            "insert into sources (source_key, name, kind) values (?, ?, ?)",
            ("synthetic-source", "Synthetic", "test"),
        )
        conn.commit()
        before_sql = _schema_sql(conn)
        tables = [
            name
            for kind, name in before_sql
            if kind == "table" and name != "schema_migrations"
        ]
        before_counts = {table: _count_rows(conn, table) for table in tables}
    assert not any("intake" in name for _, name in before_sql)

    monkeypatch.setattr(database_module, "MIGRATION_IDS", full_ids)
    initialize_database(path)

    with closing(sqlite3.connect(path)) as conn:
        after_sql = _schema_sql(conn)
        assert {k: v for k, v in after_sql.items() if k in before_sql} == before_sql
        assert {table: _count_rows(conn, table) for table in tables} == before_counts
        added = {name for kind, name in after_sql if (kind, name) not in before_sql}
        assert added
        assert all("intake" in name for name in added)
        ids = conn.execute(
            "select migration_id from schema_migrations where migration_id = ?",
            ("013_intake_context",),
        ).fetchall()
        assert len(ids) == 1
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def test_initialize_database_is_idempotent_for_the_intake_tables(
    db_path: Path,
) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        first = _schema_sql(conn)
    initialize_database(db_path)
    initialize_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        assert _schema_sql(conn) == first
        applied = _rows(
            conn,
            """
            select migration_id from schema_migrations
            where migration_id = '013_intake_context'
            """,
        )
    assert applied == [("013_intake_context",)]


def test_migration_is_additive_ddl_only_with_text_amounts() -> None:
    sql = re.sub(r"--[^\n]*", "", MIGRATION_FILE.read_text()).lower()
    assert not re.search(r"\b(drop|rename|alter)\b", sql)
    assert not re.search(r"\b(insert\s+into|delete\s+from|update\s+\w+\s+set)\b", sql)
    assert not re.search(r"\b(real|float|double)\b", sql)


def test_every_intake_table_exists_and_every_new_object_carries_intake(
    connection: sqlite3.Connection,
) -> None:
    tables = {name for kind, name in _schema_sql(connection) if kind == "table"}
    assert set(INTAKE_TABLES) <= tables
    new_objects = [
        name
        for (kind, name) in _schema_sql(connection)
        if kind in {"index", "trigger"} and not name.startswith("sqlite_")
    ]
    for table in INTAKE_TABLES:
        columns = _rows(connection, f"pragma table_info({table})")
        declared = " ".join(str(column[2]).lower() for column in columns)
        assert "real" not in declared
        assert "float" not in declared
    assert any(name.startswith("idx_intake_") for name in new_objects)


def test_expected_indexes_exist(connection: sqlite3.Connection) -> None:
    def index_columns(table: str) -> list[tuple[bool, list[str]]]:
        result: list[tuple[bool, list[str]]] = []
        for row in _rows(connection, f"pragma index_list({table})"):
            info = _rows(connection, f"pragma index_info({row[1]})")
            result.append((bool(row[2]), [str(part[2]) for part in info]))
        return result

    revisions = index_columns("intake_revisions")
    assert (True, ["owner_id", "producer_id", "intake_id", "revision"]) in revisions
    assert (False, ["producer_id", "intake_id"]) in revisions
    assert (False, ["revision"]) in revisions
    assert (False, ["occurred_at"]) in revisions
    assert (False, ["sample_uuid"]) in index_columns("intake_sample_links")
    assert (False, ["component_id"]) in index_columns("intake_sample_links")
    assert (False, ["component_id"]) in index_columns("intake_compound_facts")
    assert (
        True,
        ["owner_id", "producer_id", "operation_id"],
    ) in index_columns("intake_operation_receipts")
    assert (True, ["owner_id", "producer_id", "intake_id"]) in index_columns(
        "intake_tombstones"
    )
    assert (True, ["owner_id", "producer_id"]) in index_columns("intake_producers")


def _seed(conn: sqlite3.Connection) -> dict[str, int]:
    record = revision_from_fixture("valid_worked_example.json")
    with transaction(conn):
        stored = insert_revision(conn, record)
    fact_row = cast(
        "tuple[int]",
        conn.execute(
            """
            select intake_fact_row_id from intake_compound_facts
            where intake_revision_row_id = ? and component_id = ?
            """,
            (stored.revision_row_id, "water"),
        ).fetchone(),
    )
    return {"revision": stored.revision_row_id, "fact": fact_row[0]}


def _revision_values(_: dict[str, int]) -> dict[str, object]:
    return {
        "owner_id": OWNER,
        "producer_id": "other-app",
        "intake_id": "intake-x",
        "revision": 1,
        "domain_facts_hash": HASH_A,
        "projection_hash": HASH_B,
        "client_payload_hash": HASH_C,
        "installation_id": "install",
        "operation_id": "op-x",
        "occurred_at": NOW,
        "time_zone": "UTC",
        "recorded_at": NOW,
        "category": "food",
        "display_name": "Synthetic food",
        "serving_amount": "1",
        "serving_unit": "g",
        "nutrition_completeness": "complete",
        "received_at": NOW,
    }


def _fact_values(ids: dict[str, int]) -> dict[str, object]:
    return {
        "intake_revision_row_id": ids["revision"],
        "component_id": "extra",
        "position": 9,
        "kind": "nutrient",
        "code": "dietary_caffeine",
        "label_name": None,
        "value_state": "known",
        "amount": "1",
        "unit": "mg",
        "quantity_basis": None,
        "aggregation_role": "context_only",
        "provenance": "user_confirmed",
    }


def _link_values(ids: dict[str, int]) -> dict[str, object]:
    return {
        "intake_revision_row_id": ids["revision"],
        "projection_sequence": 1,
        "component_id": "water",
        "healthkit_type": "HKQuantityTypeIdentifierDietaryWater",
        "sample_uuid": "00000000-0000-4000-8000-000000000001",
        "sync_identifier": "sync-1",
        "sync_version": 1,
        "disposition": "active",
    }


def _receipt_values(_: dict[str, int]) -> dict[str, object]:
    return {
        "owner_id": OWNER,
        "producer_id": "nutrition-app",
        "operation_id": "op-raw",
        "client_payload_hash": HASH_A,
        "outcome": "accepted",
        "accepted_revision": 1,
        "received_at": NOW,
        "result_json": None,
    }


def _tombstone_values(_: dict[str, int]) -> dict[str, object]:
    return {
        "owner_id": OWNER,
        "producer_id": "nutrition-app",
        "intake_id": "intake-raw",
        "deleted_at": NOW,
        "revision": 1,
        "operation_id": "op-raw",
        "domain_facts_hash": HASH_A,
    }


def _state_values(_: dict[str, int]) -> dict[str, object]:
    return {
        "owner_id": OWNER,
        "producer_id": "nutrition-app",
        "intake_id": "intake-raw",
        "current_revision": 1,
        "current_projection_sequence": 1,
        "deleted": 0,
        "updated_at": NOW,
    }


def _raw_insert(
    conn: sqlite3.Connection, table: str, values: dict[str, object]
) -> None:
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    _ = conn.execute(
        f"insert into {table} ({columns}) values ({marks})",  # noqa: S608
        tuple(values.values()),
    )


CASES: Final = (
    ("intake_revisions", _revision_values, "nutrition_completeness", "bogus"),
    ("intake_revisions", _revision_values, "revision", 0),
    ("intake_revisions", _revision_values, "domain_facts_hash", "sha256:short"),
    ("intake_revisions", _revision_values, "domain_facts_hash", "sha256:" + "A" * 64),
    ("intake_revisions", _revision_values, "domain_facts_hash", "sha256:" + "g" * 64),
    ("intake_revisions", _revision_values, "projection_hash", "sha256:" + "A" * 64),
    ("intake_revisions", _revision_values, "projection_hash", "sha256:" + "z" * 64),
    ("intake_revisions", _revision_values, "client_payload_hash", "sha256:" + "F" * 64),
    ("intake_revisions", _revision_values, "client_payload_hash", "sha256:" + "-" * 64),
    (
        "intake_operation_receipts",
        _receipt_values,
        "client_payload_hash",
        "sha256:" + "A" * 64,
    ),
    (
        "intake_operation_receipts",
        _receipt_values,
        "client_payload_hash",
        "sha256:" + "x" * 64,
    ),
    ("intake_tombstones", _tombstone_values, "domain_facts_hash", "sha256:" + "A" * 64),
    ("intake_tombstones", _tombstone_values, "domain_facts_hash", "sha256:" + "x" * 64),
    ("intake_revisions", _revision_values, "serving_amount", "5e0"),
    ("intake_revisions", _revision_values, "serving_amount", "05"),
    ("intake_compound_facts", _fact_values, "kind", "bogus"),
    ("intake_compound_facts", _fact_values, "value_state", "bogus"),
    ("intake_compound_facts", _fact_values, "aggregation_role", "blend_total_only"),
    ("intake_compound_facts", _fact_values, "provenance", "bogus"),
    ("intake_compound_facts", _fact_values, "quantity_basis", "bogus"),
    ("intake_compound_facts", _fact_values, "amount", None),
    ("intake_compound_facts", _fact_values, "amount", "1.5.2"),
    ("intake_sample_links", _link_values, "disposition", "bogus"),
    ("intake_sample_links", _link_values, "sync_version", 0),
    ("intake_sample_links", _link_values, "projection_sequence", 0),
    ("intake_operation_receipts", _receipt_values, "outcome", "bogus"),
    ("intake_operation_receipts", _receipt_values, "outcome", "stale"),
    ("intake_operation_receipts", _receipt_values, "outcome", "retryable"),
    ("intake_operation_receipts", _receipt_values, "outcome", "retryable_failure"),
    ("intake_operation_receipts", _receipt_values, "outcome", "permanent"),
    ("intake_tombstones", _tombstone_values, "domain_facts_hash", "sha256:short"),
    ("intake_operation_receipts", _receipt_values, "result_json", "{not json"),
    ("intake_tombstones", _tombstone_values, "revision", 0),
    ("intake_state", _state_values, "deleted", 2),
)


@pytest.mark.parametrize(("table", "base", "column", "bad"), CASES)
def test_check_constraints_reject_invalid_values(
    connection: sqlite3.Connection,
    table: str,
    base: Callable[[dict[str, int]], dict[str, object]],
    column: str,
    bad: object,
) -> None:
    ids = _seed(connection)
    values = base(ids)
    with transaction(connection):
        _raw_insert(connection, table, values)
    values = dict(values)
    values[column] = bad
    if table == "intake_revisions":
        values["intake_id"] = "intake-y"
    elif table == "intake_compound_facts":
        values["component_id"] = "extra-two"
        values["position"] = 10
    elif table == "intake_sample_links":
        values["sample_uuid"] = "00000000-0000-4000-8000-000000000002"
    elif table == "intake_operation_receipts":
        values["operation_id"] = "op-raw-two"
    else:
        values["intake_id"] = "intake-raw-two"
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _raw_insert(connection, table, values)


def test_known_value_needs_amount_and_unit_and_a_compound_needs_its_basis(
    connection: sqlite3.Connection,
) -> None:
    ids = _seed(connection)
    unknown_with_amount = {
        **_fact_values(ids),
        "value_state": "unknown",
        "unit": None,
    }
    compound_without_basis = {
        **_fact_values(ids),
        "component_id": "compound-two",
        "position": 11,
        "kind": "compound",
        "aggregation_role": "compound_measurement",
        "quantity_basis": None,
    }
    for values in (unknown_with_amount, compound_without_basis):
        with pytest.raises(sqlite3.IntegrityError), transaction(connection):
            _raw_insert(connection, "intake_compound_facts", values)


def test_worked_example_revision_reads_back_equal(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")

    with transaction(connection):
        written = insert_revision(connection, record)
    stored = read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )

    assert stored == written
    assert stored is not None
    assert stored.record == record
    assert [p.projection_sequence for p in stored.projections] == [1]
    assert stored.projections[0].projection_hash == record.projection_hash
    assert stored.record.domain_facts_hash.startswith("sha256:93bb96b9")


def test_blend_members_keep_their_order_and_unknown_values_stay_null(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_proprietary_blend.json")

    _ = _store(connection, record)
    stored = read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )

    assert stored is not None
    assert stored.record == record
    blend = next(f for f in stored.record.facts if f.kind == "blend")
    assert [m.label_name for m in blend.members] == [
        "Taurine",
        "Guarana extract",
        "Ginseng root extract",
    ]
    assert all(m.amount is None and m.unit is None for m in blend.members)
    energy = next(f for f in stored.record.facts if f.component_id == "energy-kcal")
    assert (energy.value_state, energy.amount, energy.unit) == ("unknown", None, None)
    assert [f.component_id for f in stored.record.facts] == [
        "caffeine",
        "energy-blend",
        "energy-kcal",
    ]


def test_decimal_spelling_is_stored_exactly(connection: sqlite3.Connection) -> None:
    base = revision_from_fixture("valid_worked_example.json")
    water, creatine = base.facts
    five = replace(base, facts=(water, replace(creatine, amount="5")))
    five_point_zero = replace(
        base,
        intake_id="b1f4c9d2-0000-4000-8000-000000000001",
        facts=(water, replace(creatine, amount="5.0")),
        serving_amount="500.0",
    )

    _ = _store(connection, five)
    _ = _store(connection, five_point_zero)

    amounts: list[str] = []
    for record in (five, five_point_zero):
        stored = read_revision(
            connection,
            owner_id=record.owner_id,
            producer_id=record.producer_id,
            intake_id=record.intake_id,
            revision=record.revision,
        )
        assert stored is not None
        amounts.append(stored.record.facts[1].amount or "")
        assert stored.record.serving_amount == record.serving_amount
    assert amounts == ["5", "5.0"]
    typeof = _rows(
        connection,
        """
        select distinct typeof(amount) from intake_compound_facts
        where amount is not null
        """,
    )
    assert typeof == [("text",)]


def test_same_revision_with_different_facts_is_a_conflict(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    _ = _store(connection, record)
    other = replace(
        record,
        domain_facts_hash=HASH_A,
        operation_id="00000000-0000-4000-8000-0000000000aa",
        display_name="Different",
    )

    with pytest.raises(IntakeRevisionConflictError) as error, transaction(connection):
        _ = insert_revision(connection, other)

    assert error.value.revision == record.revision
    assert _count_rows(connection, "intake_revisions") == 1
    assert _count_rows(connection, "intake_compound_facts") == 2


def test_same_revision_with_same_facts_returns_the_existing_row(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    first_id = _store(connection, record)
    replay = replace(
        record,
        operation_id="00000000-0000-4000-8000-0000000000bb",
        installation_id="9a1d3f4e-0000-4000-8000-000000000002",
        client_payload_hash=HASH_C,
        received_at="2026-10-02T00:00:00Z",
    )

    with transaction(connection):
        again = insert_revision(connection, replay)

    assert again.revision_row_id == first_id
    assert again.record == record
    assert _count_rows(connection, "intake_revisions") == 1
    assert _count_rows(connection, "intake_sample_links") == 1


def test_owner_and_producer_scope_the_revision_identity(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    _ = _store(connection, record)

    _ = _store(
        connection, replace(record, owner_id="owner-2", domain_facts_hash=HASH_A)
    )
    _ = _store(
        connection, replace(record, producer_id="other-app", domain_facts_hash=HASH_B)
    )

    assert _count_rows(connection, "intake_revisions") == 3


def test_failed_insert_leaves_nothing_behind_in_the_callers_transaction(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    water, creatine = record.facts
    broken = replace(record, facts=(water, replace(creatine, component_id="water")))

    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = insert_revision(connection, broken)

    assert _count_rows(connection, "intake_revisions") == 0
    assert _count_rows(connection, "intake_compound_facts") == 0

    with transaction(connection):
        outer = insert_revision(connection, record)
        with pytest.raises(sqlite3.IntegrityError):
            _ = insert_revision(connection, replace(broken, intake_id="other-intake"))
    assert read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    ) == replace(outer)
    assert _count_rows(connection, "intake_revisions") == 1


def _tombstone(
    intake_id: str,
    *,
    revision: int,
    operation_id: str = "op-delete",
    domain_facts_hash: str = HASH_A,
) -> TombstoneRecord:
    return TombstoneRecord(
        owner_id=OWNER,
        producer_id="nutrition-app",
        intake_id=intake_id,
        deleted_at="2026-10-01T01:00:00Z",
        revision=revision,
        operation_id=operation_id,
        domain_facts_hash=domain_facts_hash,
    )


def test_writes_need_a_transaction_opened_by_the_caller(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")

    with pytest.raises(IntakeTransactionRequiredError):
        _ = insert_revision(connection, record)
    with pytest.raises(IntakeTransactionRequiredError):
        _ = write_tombstone(
            connection,
            _tombstone(record.intake_id, revision=3),
        )


@pytest.mark.parametrize(
    ("table", "statement"),
    [
        ("intake_revisions", "update intake_revisions set display_name = 'x'"),
        ("intake_revisions", "delete from intake_revisions"),
        ("intake_compound_facts", "update intake_compound_facts set amount = '9'"),
        ("intake_compound_facts", "delete from intake_compound_facts"),
        (
            "intake_projection_snapshots",
            "update intake_projection_snapshots set projection_hash = 'x'",
        ),
        ("intake_projection_snapshots", "delete from intake_projection_snapshots"),
        (
            "intake_sample_links",
            "update intake_sample_links set disposition = 'deleted'",
        ),
        ("intake_sample_links", "update intake_sample_links set sample_uuid = 'x'"),
        ("intake_sample_links", "delete from intake_sample_links"),
    ],
)
def test_stored_revisions_have_no_update_or_delete_path(
    connection: sqlite3.Connection,
    table: str,
    statement: str,
) -> None:
    _ = _seed(connection)
    before = _count_rows(connection, table)

    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = connection.execute(statement)

    assert _count_rows(connection, table) == before


def test_blend_members_are_immutable(connection: sqlite3.Connection) -> None:
    record = revision_from_fixture("valid_proprietary_blend.json")
    _ = _store(connection, record)

    for statement in (
        "update intake_blend_members set label_name = 'x'",
        "delete from intake_blend_members",
    ):
        with pytest.raises(sqlite3.IntegrityError), transaction(connection):
            _ = connection.execute(statement)
    assert _count_rows(connection, "intake_blend_members") == 3


def test_tombstone_is_permanent(connection: sqlite3.Connection) -> None:
    tombstone = _tombstone("intake-gone", revision=3)

    with transaction(connection):
        written = write_tombstone(connection, tombstone)
    with transaction(connection):
        again = write_tombstone(connection, tombstone)
    stored = read_tombstone(
        connection,
        owner_id=OWNER,
        producer_id="nutrition-app",
        intake_id="intake-gone",
    )

    assert written == again == stored == tombstone
    with pytest.raises(IntakeTombstoneConflictError), transaction(connection):
        _ = write_tombstone(connection, replace(tombstone, domain_facts_hash=HASH_B))
    for statement in (
        "update intake_tombstones set revision = 9",
        "delete from intake_tombstones",
    ):
        with pytest.raises(sqlite3.IntegrityError), transaction(connection):
            _ = connection.execute(statement)
    assert (
        read_tombstone(
            connection,
            owner_id=OWNER,
            producer_id="nutrition-app",
            intake_id="intake-gone",
        )
        == tombstone
    )
    assert (
        read_tombstone(
            connection,
            owner_id="owner-2",
            producer_id="nutrition-app",
            intake_id="intake-gone",
        )
        is None
    )


def _receipt(
    operation_id: str = "op-1", payload_hash: str = HASH_A
) -> OperationReceiptRecord:
    return OperationReceiptRecord(
        owner_id=OWNER,
        producer_id="nutrition-app",
        operation_id=operation_id,
        client_payload_hash=payload_hash,
        outcome="accepted",
        accepted_revision=2,
        received_at=NOW,
        result_json=json.dumps({"result": "accepted", "revision": 2}),
    )


def test_operation_receipt_is_idempotent_and_rejects_a_different_payload(
    connection: sqlite3.Connection,
) -> None:
    with transaction(connection):
        first = append_operation_receipt(connection, _receipt())
    with transaction(connection):
        again = append_operation_receipt(
            connection,
            replace(
                _receipt(), outcome="duplicate", received_at="2026-10-02T00:00:00Z"
            ),
        )

    assert again == first
    assert first.server_cursor == 1
    assert _count_rows(connection, "intake_operation_receipts") == 1
    with pytest.raises(IntakeOperationConflictError) as error, transaction(connection):
        _ = append_operation_receipt(connection, _receipt(payload_hash=HASH_B))
    assert error.value.operation_id == "op-1"
    assert (
        read_operation_receipt(
            connection,
            owner_id=OWNER,
            producer_id="nutrition-app",
            operation_id="op-1",
        )
        == first
    )
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _raw_insert(
            connection,
            "intake_operation_receipts",
            {**_receipt_values({}), "operation_id": "op-1"},
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = connection.execute(
            "update intake_operation_receipts set outcome = 'stale_revision'"
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = connection.execute("delete from intake_operation_receipts")


def test_server_cursor_only_moves_forward(connection: sqlite3.Connection) -> None:
    cursors: list[int | None] = []
    for number in range(3):
        with transaction(connection):
            cursors.append(
                append_operation_receipt(
                    connection, _receipt(f"op-{number}")
                ).server_cursor
            )
    with transaction(connection):
        _ = append_operation_receipt(connection, replace(_receipt("op-1")))
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _raw_insert(
            connection,
            "intake_operation_receipts",
            {**_receipt_values({}), "operation_id": "op-9", "outcome": "bogus"},
        )
    with transaction(connection):
        cursors.append(
            append_operation_receipt(connection, _receipt("op-4")).server_cursor
        )

    assert cursors == [1, 2, 3, 4]


def test_operation_ids_are_scoped_by_owner_and_producer(
    connection: sqlite3.Connection,
) -> None:
    with transaction(connection):
        _ = append_operation_receipt(connection, _receipt())
        _ = append_operation_receipt(
            connection,
            replace(_receipt(), producer_id="other-app", client_payload_hash=HASH_B),
        )
        _ = append_operation_receipt(
            connection,
            replace(_receipt(), owner_id="owner-2", client_payload_hash=HASH_C),
        )
    assert _count_rows(connection, "intake_operation_receipts") == 3


def _later_snapshot(sequence: int, links: tuple[SampleLink, ...]) -> ProjectionRecord:
    return ProjectionRecord(
        projection_sequence=sequence,
        projection_hash=HASH_B,
        received_at="2026-10-01T02:00:00Z",
        links=links,
    )


def test_superseded_links_are_retained_in_later_snapshots(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    row_id = _store(connection, record)
    original = record.links[0]
    replacement = replace(
        original,
        sample_uuid="11111111-1111-4111-8111-111111111111",
        sync_version=3,
        source_bundle_id="com.example.healthrelay.nutrition",
        source_checked_at="2026-10-01T02:00:00Z",
    )
    snapshot = _later_snapshot(
        2,
        (replace(original, disposition="superseded"), replacement),
    )

    with transaction(connection):
        written = insert_projection_snapshot(
            connection, revision_row_id=row_id, snapshot=snapshot
        )
    stored = read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )

    assert written == snapshot
    assert stored is not None
    assert stored.record.links == (original,)
    assert [p.projection_sequence for p in stored.projections] == [1, 2]
    assert stored.projections[1].links == snapshot.links
    old = list_links_by_sample_uuid(
        connection, owner_id=OWNER, sample_uuid=original.sample_uuid
    )
    assert [(s.projection_sequence, s.link.disposition) for s in old] == [
        (1, "active"),
        (2, "superseded"),
    ]


def test_projection_snapshot_replay_and_conflict(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    row_id = _store(connection, record)
    snapshot = _later_snapshot(2, ())

    with transaction(connection):
        _ = insert_projection_snapshot(
            connection, revision_row_id=row_id, snapshot=snapshot
        )
    with transaction(connection):
        again = insert_projection_snapshot(
            connection,
            revision_row_id=row_id,
            snapshot=replace(snapshot, received_at="2026-10-05T00:00:00Z"),
        )

    assert again == snapshot
    with pytest.raises(IntakeProjectionConflictError), transaction(connection):
        _ = insert_projection_snapshot(
            connection,
            revision_row_id=row_id,
            snapshot=replace(snapshot, projection_hash=HASH_C),
        )
    assert _count_rows(connection, "intake_projection_snapshots") == 2


def test_foreign_keys_hold_and_a_revision_is_never_cascaded_away(
    connection: sqlite3.Connection,
) -> None:
    ids = _seed(connection)

    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _raw_insert(
            connection,
            "intake_compound_facts",
            {**_fact_values(ids), "intake_revision_row_id": 9999},
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _raw_insert(
            connection,
            "intake_sample_links",
            {**_link_values(ids), "projection_sequence": 7},
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _raw_insert(
            connection,
            "intake_blend_members",
            {"intake_fact_row_id": 9999, "position": 0, "label_name": "x"},
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = connection.execute(
            "delete from intake_revisions where intake_revision_row_id = ?",
            (ids["revision"],),
        )
    foreign_keys = _rows(connection, "pragma foreign_key_list(intake_compound_facts)")
    assert [(row[2], row[6]) for row in foreign_keys] == [
        ("intake_revisions", "NO ACTION")
    ]
    assert connection.execute("pragma foreign_key_check").fetchall() == []
    assert _count_rows(connection, "intake_revisions") == 1


def test_links_are_listed_by_sample_uuid_and_by_component_id(
    connection: sqlite3.Connection,
) -> None:
    worked = revision_from_fixture("valid_worked_example.json")
    # A second intake that also has a "water" fact (links name facts of their revision).
    other = replace(worked, intake_id="0b9f4c2e-7d1a-4e8b-9c3f-5a6d7e8f9a0b")
    _ = _store(connection, worked)
    other_link = SampleLink(
        component_id="water",
        healthkit_type="HKQuantityTypeIdentifierDietaryWater",
        sample_uuid="22222222-2222-4222-8222-222222222222",
        sync_identifier="intake:other:water",
        sync_version=1,
        disposition="active",
    )
    _ = _store(
        connection,
        replace(other, links=(other_link,), domain_facts_hash=HASH_A),
    )

    by_sample = list_links_by_sample_uuid(
        connection, owner_id=OWNER, sample_uuid=worked.links[0].sample_uuid
    )
    by_component = list_links_by_component_id(
        connection, owner_id=OWNER, component_id="water"
    )
    narrowed = list_links_by_component_id(
        connection, owner_id=OWNER, component_id="water", intake_id=other.intake_id
    )

    assert [s.intake_id for s in by_sample] == [worked.intake_id]
    assert by_sample[0].revision == worked.revision
    assert by_sample[0].link == worked.links[0]
    assert {s.link.sample_uuid for s in by_component} == {
        worked.links[0].sample_uuid,
        other_link.sample_uuid,
    }
    assert [s.link for s in narrowed] == [other_link]
    assert (
        list_links_by_sample_uuid(
            connection, owner_id="owner-2", sample_uuid=worked.links[0].sample_uuid
        )
        == ()
    )


def test_producer_registration_is_idempotent_and_never_silently_changed(
    connection: sqlite3.Connection,
) -> None:
    producer = ProducerRecord(
        owner_id=OWNER,
        producer_id="nutrition-app",
        writer_bundle_id="com.example.healthrelay.nutrition",
        display_label="Synthetic nutrition app",
        registered_at=NOW,
    )

    with transaction(connection):
        assert register_producer(connection, producer) == producer
    with transaction(connection):
        assert register_producer(connection, producer) == producer
    with pytest.raises(IntakeProducerConflictError), transaction(connection):
        _ = register_producer(
            connection, replace(producer, writer_bundle_id="com.example.other")
        )

    assert (
        read_producer(connection, owner_id=OWNER, producer_id="nutrition-app")
        == producer
    )
    assert read_producer(connection, owner_id=OWNER, producer_id="missing") is None
    assert _count_rows(connection, "intake_producers") == 1


def test_intake_state_is_one_row_per_intake_and_follows_the_caller(
    connection: sqlite3.Connection,
) -> None:
    state = IntakeStateRecord(
        owner_id=OWNER,
        producer_id="nutrition-app",
        intake_id="intake-1",
        current_revision=1,
        current_projection_sequence=1,
        deleted=False,
        updated_at=NOW,
    )
    assert (
        read_intake_state(
            connection,
            owner_id=OWNER,
            producer_id="nutrition-app",
            intake_id="intake-1",
        )
        is None
    )

    with transaction(connection):
        write_intake_state(connection, state)
    moved = replace(
        state,
        current_revision=2,
        current_projection_sequence=3,
        deleted=True,
        updated_at="2026-10-02T00:00:00Z",
    )
    with transaction(connection):
        write_intake_state(connection, moved)

    assert (
        read_intake_state(
            connection,
            owner_id=OWNER,
            producer_id="nutrition-app",
            intake_id="intake-1",
        )
        == moved
    )
    assert _count_rows(connection, "intake_state") == 1


def test_a_link_must_name_a_fact_of_its_revision(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    row_id = _store(connection, record)
    ghost = replace(record.links[0], component_id="ghost")

    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = insert_projection_snapshot(
            connection, revision_row_id=row_id, snapshot=_later_snapshot(2, (ghost,))
        )
    assert _count_rows(connection, "intake_projection_snapshots") == 1


def test_a_failed_write_keeps_its_own_error_when_the_transaction_is_gone(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    _ = connection.execute("begin")

    def lost_transaction(*_args: object, **_kwargs: object) -> int:
        # SQLite rolls the whole transaction back itself on SQLITE_FULL or IOERR.
        _ = connection.execute("rollback")
        message = "database or disk is full"
        raise sqlite3.OperationalError(message)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(intake_context, "_inserted_row_id", lost_transaction)
        with pytest.raises(sqlite3.OperationalError, match="disk is full"):
            _ = insert_revision(connection, record)
    assert not connection.in_transaction


def test_tombstone_replay_under_a_new_operation_id_is_safe(
    connection: sqlite3.Connection,
) -> None:
    tombstone = _tombstone("intake-gone", revision=3)
    with transaction(connection):
        _ = write_tombstone(connection, tombstone)
    with transaction(connection):
        again = write_tombstone(
            connection, replace(tombstone, operation_id="op-redelivered")
        )

    assert again == tombstone
    assert _count_rows(connection, "intake_tombstones") == 1


def test_receipt_outcomes_use_the_contract_names(
    connection: sqlite3.Connection,
) -> None:
    outcomes = (
        "accepted",
        "duplicate",
        "stale_revision",
        "domain_conflict",
        "projection_conflict",
        "permanent_failure",
    )
    for index, outcome in enumerate(outcomes):
        with transaction(connection):
            stored = append_operation_receipt(
                connection,
                replace(_receipt(operation_id=f"op-{index}"), outcome=outcome),
            )
        assert stored.outcome == outcome


def test_retryable_failure_is_never_stored_as_a_receipt(
    connection: sqlite3.Connection,
) -> None:
    retryable = replace(
        _receipt(),
        outcome="retryable_failure",
    )
    with pytest.raises(IntakeNonTerminalOutcomeError), transaction(connection):
        _ = append_operation_receipt(connection, retryable)
    with pytest.raises(IntakeNonTerminalOutcomeError):
        _ = append_operation_receipt(connection, retryable)
    assert _count_rows(connection, "intake_operation_receipts") == 0


def test_revision_replay_with_a_different_first_projection_conflicts(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    _ = _store(connection, record)
    changed = replace(
        record,
        projection_hash=HASH_B,
        links=tuple(
            replace(link, sync_version=link.sync_version + 1) for link in record.links
        ),
    )
    with pytest.raises(IntakeProjectionConflictError), transaction(connection):
        _ = insert_revision(connection, changed)
    with transaction(connection):
        same = insert_revision(connection, record)
    assert same.record.projection_hash == record.projection_hash
    assert _count_rows(connection, "intake_revisions") == 1


@pytest.mark.parametrize("bad", ["sha256:" + "A" * 64, "sha256:" + "g" * 64])
def test_snapshot_hash_must_be_lowercase_hex(
    connection: sqlite3.Connection, bad: str
) -> None:
    ids = _seed(connection)
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = connection.execute(
            """
            insert into intake_projection_snapshots
                (intake_revision_row_id, projection_sequence,
                 projection_hash, received_at)
            values (?, 9, ?, ?)
            """,
            (ids["revision"], bad, NOW),
        )


def test_link_created_at_is_immutable_but_source_columns_stay_updatable(
    connection: sqlite3.Connection,
) -> None:
    ids = _seed(connection)
    with transaction(connection):
        _raw_insert(connection, "intake_sample_links", _link_values(ids))

    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        _ = connection.execute(
            "update intake_sample_links set created_at = '2000-01-01T00:00:00Z'"
        )
    with transaction(connection):
        _ = connection.execute(
            """
            update intake_sample_links
            set source_bundle_id = 'com.example.app', source_checked_at = ?
            where sample_uuid = ?
            """,
            (NOW, "00000000-0000-4000-8000-000000000001"),
        )
    row = cast(
        "tuple[str, str]",
        connection.execute(
            """
            select source_bundle_id, source_checked_at from intake_sample_links
            where sample_uuid = ?
            """,
            ("00000000-0000-4000-8000-000000000001",),
        ).fetchone(),
    )
    assert row == ("com.example.app", NOW)


@final
class _SnapshotThenWrite:
    """Connection proxy: right after the first read of this revision's
    snapshots or links, another writer commits a new snapshot with a link."""

    def __init__(self, conn: sqlite3.Connection, row_id: int, link: SampleLink) -> None:
        self._conn = conn
        self._row_id = row_id
        self._link = link
        self.fired = False

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> "_Rows":
        rows = self._conn.execute(sql, params).fetchall()
        if not self.fired and (
            "from intake_projection_snapshots" in sql
            or "from intake_sample_links" in sql
        ):
            self.fired = True
            late = ProjectionRecord(
                projection_sequence=3,
                projection_hash=HASH_C,
                received_at="2026-10-01T03:00:00Z",
                links=(self._link,),
            )
            with transaction(self._conn):
                _ = insert_projection_snapshot(
                    self._conn, revision_row_id=self._row_id, snapshot=late
                )
        return _Rows(rows)


@final
class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


def test_links_are_only_read_for_snapshots_that_were_read(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    row_id = _store(connection, record)
    original = record.links[0]
    with transaction(connection):
        _ = insert_projection_snapshot(
            connection, revision_row_id=row_id, snapshot=_later_snapshot(2, ())
        )
    late_link = replace(
        original, sample_uuid="22222222-2222-4222-8222-222222222222", sync_version=9
    )
    proxy = _SnapshotThenWrite(connection, row_id, late_link)

    stored = read_revision(
        cast("sqlite3.Connection", cast("object", proxy)),
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )

    assert proxy.fired
    assert stored is not None
    # A snapshot is never returned without its links: snapshot 3 landed after
    # the snapshot read, so it must be absent and its link must not leak in.
    assert [p.projection_sequence for p in stored.projections] == [1, 2]
    assert stored.projections[0].links == (original,)
    assert stored.projections[1].links == ()
    again = read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )
    assert again is not None
    assert [p.projection_sequence for p in again.projections] == [1, 2, 3]
    assert again.projections[2].links == (late_link,)


def test_each_snapshot_gets_exactly_its_own_links_in_order(
    connection: sqlite3.Connection,
) -> None:
    record = revision_from_fixture("valid_worked_example.json")
    row_id = _store(connection, record)
    original = record.links[0]

    def variant(tag: int) -> SampleLink:
        return replace(
            original,
            sample_uuid=f"{tag:08d}-0000-4000-8000-000000000000",
            sync_version=tag,
        )

    expected: dict[int, tuple[SampleLink, ...]] = {
        2: (variant(9), variant(3), variant(5)),
        3: (),
        4: (variant(1),),
        5: (variant(7), variant(2)),
    }
    for sequence, links in expected.items():
        with transaction(connection):
            _ = insert_projection_snapshot(
                connection,
                revision_row_id=row_id,
                snapshot=_later_snapshot(sequence, links),
            )

    stored = read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )

    assert stored is not None
    assert {p.projection_sequence: p.links for p in stored.projections} == {
        1: (original,),
        **expected,
    }

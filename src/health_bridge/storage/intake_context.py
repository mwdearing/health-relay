"""Receiver storage for the intake-context contract.

Typed reads and writes for the tables created by migration 013. Every write
runs inside a transaction the caller opened and never commits. This module
makes no acceptance decisions: it stores what it is given, refuses to overwrite
anything immutable, and reports an identity that already holds different
content as a typed error.
"""

import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias, get_args

from pydantic import TypeAdapter

from health_bridge.contract.intake_context_v1 import (
    AggregationRole,
    Disposition,
    FactKind,
    NutritionCompleteness,
    Provenance,
    QuantityBasis,
    ValueState,
)

OperationOutcome: TypeAlias = Literal[
    "accepted",
    "duplicate",
    "stale_revision",
    "domain_conflict",
    "projection_conflict",
    "permanent_failure",
]
TERMINAL_OUTCOMES: Final = frozenset(get_args(OperationOutcome))
SAVEPOINT_NAME: Final = "intake_context_write"


class IntakeContextStorageError(Exception):
    """Base class for every error this module raises."""


class IntakeTransactionRequiredError(IntakeContextStorageError):
    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} needs a transaction opened by the caller")
        self.operation: str = operation


class IntakeProducerConflictError(IntakeContextStorageError):
    def __init__(self, producer_id: str) -> None:
        super().__init__("intake producer already registered with different details")
        self.producer_id: str = producer_id


class IntakeRevisionConflictError(IntakeContextStorageError):
    def __init__(
        self,
        *,
        owner_id: str,
        producer_id: str,
        intake_id: str,
        revision: int,
    ) -> None:
        super().__init__("intake revision already holds different domain facts")
        self.owner_id: str = owner_id
        self.producer_id: str = producer_id
        self.intake_id: str = intake_id
        self.revision: int = revision


class IntakeProjectionConflictError(IntakeContextStorageError):
    def __init__(self, *, revision_row_id: int, projection_sequence: int) -> None:
        super().__init__(
            "intake projection sequence already holds a different snapshot"
        )
        self.revision_row_id: int = revision_row_id
        self.projection_sequence: int = projection_sequence


class IntakeOperationConflictError(IntakeContextStorageError):
    def __init__(self, operation_id: str) -> None:
        super().__init__(
            "intake operation id already recorded with a different payload"
        )
        self.operation_id: str = operation_id


class IntakeStateRegressionError(IntakeContextStorageError):
    def __init__(self, intake_id: str) -> None:
        super().__init__("intake state never moves backwards or out of deleted")
        self.intake_id: str = intake_id


class IntakeNonTerminalOutcomeError(IntakeContextStorageError, ValueError):
    def __init__(self, outcome: str) -> None:
        super().__init__("only terminal outcomes are stored as operation receipts")
        self.outcome: str = outcome


class IntakeTombstoneConflictError(IntakeContextStorageError):
    def __init__(self, intake_id: str) -> None:
        super().__init__("intake already has a different tombstone")
        self.intake_id: str = intake_id


@dataclass(frozen=True, slots=True)
class ProducerRecord:
    owner_id: str
    producer_id: str
    writer_bundle_id: str
    display_label: str
    registered_at: str
    revoked_at: str | None = None


@dataclass(frozen=True, slots=True)
class BlendMemberRecord:
    label_name: str
    amount: str | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class FactRecord:
    component_id: str
    kind: FactKind
    code: str
    value_state: ValueState
    aggregation_role: AggregationRole
    provenance: Provenance
    label_name: str | None = None
    amount: str | None = None
    unit: str | None = None
    quantity_basis: QuantityBasis | None = None
    members: tuple[BlendMemberRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class SampleLink:
    component_id: str
    healthkit_type: str
    sample_uuid: str
    sync_identifier: str
    sync_version: int
    disposition: Disposition
    source_bundle_id: str | None = None
    source_checked_at: str | None = None


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    owner_id: str
    producer_id: str
    intake_id: str
    revision: int
    domain_facts_hash: str
    projection_hash: str
    client_payload_hash: str
    installation_id: str
    operation_id: str
    occurred_at: str
    time_zone: str
    recorded_at: str
    category: str
    display_name: str
    serving_amount: str
    serving_unit: str
    nutrition_completeness: NutritionCompleteness
    received_at: str
    facts: tuple[FactRecord, ...]
    links: tuple[SampleLink, ...]


@dataclass(frozen=True, slots=True)
class ProjectionRecord:
    projection_sequence: int
    projection_hash: str
    received_at: str
    links: tuple[SampleLink, ...]


@dataclass(frozen=True, slots=True)
class StoredRevision:
    revision_row_id: int
    record: RevisionRecord
    projections: tuple[ProjectionRecord, ...]


@dataclass(frozen=True, slots=True)
class StoredLink:
    owner_id: str
    producer_id: str
    intake_id: str
    revision: int
    projection_sequence: int
    link: SampleLink


@dataclass(frozen=True, slots=True)
class IntakeStateRecord:
    owner_id: str
    producer_id: str
    intake_id: str
    current_revision: int
    current_projection_sequence: int
    deleted: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class OperationReceiptRecord:
    owner_id: str
    producer_id: str
    operation_id: str
    client_payload_hash: str
    outcome: OperationOutcome
    received_at: str
    accepted_revision: int | None = None
    result_json: str | None = None
    server_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class TombstoneRecord:
    owner_id: str
    producer_id: str
    intake_id: str
    deleted_at: str
    revision: int
    operation_id: str
    domain_facts_hash: str


ProducerRow: TypeAlias = tuple[str, str, str, str, str, str | None]
RevisionRow: TypeAlias = tuple[
    int,
    str,
    str,
    str,
    int,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    NutritionCompleteness,
    str,
]
FactRow: TypeAlias = tuple[
    int,
    str,
    FactKind,
    str,
    str | None,
    ValueState,
    str | None,
    str | None,
    QuantityBasis | None,
    AggregationRole,
    Provenance,
]
MemberRow: TypeAlias = tuple[int, str, str | None, str | None]
LinkRow: TypeAlias = tuple[
    int, str, str, str, str, int, Disposition, str | None, str | None
]
SnapshotRow: TypeAlias = tuple[int, str, str]
LocatedLinkRow: TypeAlias = tuple[
    str,
    str,
    str,
    int,
    int,
    str,
    str,
    str,
    str,
    int,
    Disposition,
    str | None,
    str | None,
]
StateRow: TypeAlias = tuple[str, str, str, int, int, int, str]
ReceiptRow: TypeAlias = tuple[
    str, str, str, str, OperationOutcome, int | None, str, str | None, int
]
TombstoneRow: TypeAlias = tuple[str, str, str, str, int, str, str]

PRODUCER_ROW_ADAPTER: Final[TypeAdapter[ProducerRow | None]] = TypeAdapter(
    ProducerRow | None
)
REVISION_ROW_ADAPTER: Final[TypeAdapter[RevisionRow | None]] = TypeAdapter(
    RevisionRow | None
)
FACT_ROWS_ADAPTER: Final[TypeAdapter[list[FactRow]]] = TypeAdapter(list[FactRow])
MEMBER_ROWS_ADAPTER: Final[TypeAdapter[list[MemberRow]]] = TypeAdapter(list[MemberRow])
LINK_ROWS_ADAPTER: Final[TypeAdapter[list[LinkRow]]] = TypeAdapter(list[LinkRow])
SNAPSHOT_ROW_ADAPTER: Final[TypeAdapter[SnapshotRow | None]] = TypeAdapter(
    SnapshotRow | None
)
SNAPSHOT_ROWS_ADAPTER: Final[TypeAdapter[list[SnapshotRow]]] = TypeAdapter(
    list[SnapshotRow]
)
LOCATED_LINK_ROWS_ADAPTER: Final[TypeAdapter[list[LocatedLinkRow]]] = TypeAdapter(
    list[LocatedLinkRow]
)
STATE_ROW_ADAPTER: Final[TypeAdapter[StateRow | None]] = TypeAdapter(StateRow | None)
RECEIPT_ROW_ADAPTER: Final[TypeAdapter[ReceiptRow | None]] = TypeAdapter(
    ReceiptRow | None
)
TOMBSTONE_ROW_ADAPTER: Final[TypeAdapter[TombstoneRow | None]] = TypeAdapter(
    TombstoneRow | None
)

SELECT_PRODUCER_SQL: Final = """
select owner_id, producer_id, writer_bundle_id, display_label, registered_at,
       revoked_at
from intake_producers
where owner_id = ? and producer_id = ?
"""
INSERT_PRODUCER_SQL: Final = """
insert into intake_producers (
    owner_id, producer_id, writer_bundle_id, display_label, registered_at,
    revoked_at
) values (?, ?, ?, ?, ?, ?)
"""
SELECT_REVISION_SQL: Final = """
select intake_revision_row_id, owner_id, producer_id, intake_id, revision,
       domain_facts_hash, projection_hash, client_payload_hash,
       installation_id, operation_id, occurred_at, time_zone, recorded_at,
       category, display_name, serving_amount, serving_unit,
       nutrition_completeness, received_at
from intake_revisions
where owner_id = ? and producer_id = ? and intake_id = ? and revision = ?
"""
INSERT_REVISION_SQL: Final = """
insert into intake_revisions (
    owner_id, producer_id, intake_id, revision, domain_facts_hash,
    projection_hash, client_payload_hash, installation_id, operation_id,
    occurred_at, time_zone, recorded_at, category, display_name,
    serving_amount, serving_unit, nutrition_completeness, received_at
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
INSERT_FACT_SQL: Final = """
insert into intake_compound_facts (
    intake_revision_row_id, component_id, position, kind, code, label_name,
    value_state, amount, unit, quantity_basis, aggregation_role, provenance
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
INSERT_MEMBER_SQL: Final = """
insert into intake_blend_members (
    intake_fact_row_id, position, label_name, amount, unit
) values (?, ?, ?, ?, ?)
"""
SELECT_FACTS_SQL: Final = """
select intake_fact_row_id, component_id, kind, code, label_name, value_state,
       amount, unit, quantity_basis, aggregation_role, provenance
from intake_compound_facts
where intake_revision_row_id = ?
order by position
"""
SELECT_MEMBERS_SQL: Final = """
select member.intake_fact_row_id, member.label_name, member.amount,
       member.unit
from intake_blend_members as member
join intake_compound_facts as fact
  on fact.intake_fact_row_id = member.intake_fact_row_id
where fact.intake_revision_row_id = ?
order by member.intake_fact_row_id, member.position
"""
SELECT_SNAPSHOT_SQL: Final = """
select projection_sequence, projection_hash, received_at
from intake_projection_snapshots
where intake_revision_row_id = ? and projection_sequence = ?
"""
SELECT_SNAPSHOTS_SQL: Final = """
select projection_sequence, projection_hash, received_at
from intake_projection_snapshots
where intake_revision_row_id = ?
order by projection_sequence
"""
INSERT_SNAPSHOT_SQL: Final = """
insert into intake_projection_snapshots (
    intake_revision_row_id, projection_sequence, projection_hash, received_at
) values (?, ?, ?, ?)
"""
INSERT_LINK_SQL: Final = """
insert into intake_sample_links (
    intake_revision_row_id, projection_sequence, component_id, healthkit_type,
    sample_uuid, sync_identifier, sync_version, disposition,
    source_bundle_id, source_checked_at
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
SELECT_LINKS_SQL: Final = """
select projection_sequence, component_id, healthkit_type, sample_uuid,
       sync_identifier, sync_version, disposition, source_bundle_id,
       source_checked_at
from intake_sample_links
where intake_revision_row_id = ?
order by projection_sequence, intake_link_row_id
"""
SELECT_LINKS_UP_TO_SQL: Final = """
select projection_sequence, component_id, healthkit_type, sample_uuid,
       sync_identifier, sync_version, disposition, source_bundle_id,
       source_checked_at
from intake_sample_links
where intake_revision_row_id = ? and projection_sequence <= ?
order by projection_sequence, intake_link_row_id
"""
LOCATED_LINK_COLUMNS: Final = """
select revision.owner_id, revision.producer_id, revision.intake_id,
       revision.revision, link.projection_sequence, link.component_id,
       link.healthkit_type, link.sample_uuid, link.sync_identifier,
       link.sync_version, link.disposition, link.source_bundle_id,
       link.source_checked_at
from intake_sample_links as link
join intake_revisions as revision
  on revision.intake_revision_row_id = link.intake_revision_row_id
"""
LOCATED_LINK_ORDER: Final = """
order by revision.producer_id, revision.intake_id, revision.revision,
         link.projection_sequence, link.intake_link_row_id
"""
SELECT_LINKS_BY_SAMPLE_SQL: Final = (
    LOCATED_LINK_COLUMNS
    + "where revision.owner_id = ? and link.sample_uuid = ?\n"
    + LOCATED_LINK_ORDER
)
SELECT_LINKS_BY_COMPONENT_SQL: Final = (
    LOCATED_LINK_COLUMNS
    + """
where revision.owner_id = ?
  and link.component_id = ?
  and (? is null or revision.producer_id = ?)
  and (? is null or revision.intake_id = ?)
"""
    + LOCATED_LINK_ORDER
)
SELECT_STATE_SQL: Final = """
select owner_id, producer_id, intake_id, current_revision,
       current_projection_sequence, deleted, updated_at
from intake_state
where owner_id = ? and producer_id = ? and intake_id = ?
"""
WRITE_STATE_SQL: Final = """
insert into intake_state (
    owner_id, producer_id, intake_id, current_revision,
    current_projection_sequence, deleted, updated_at
) values (?, ?, ?, ?, ?, ?, ?)
on conflict(owner_id, producer_id, intake_id) do update set
    current_revision = excluded.current_revision,
    current_projection_sequence = excluded.current_projection_sequence,
    deleted = excluded.deleted,
    updated_at = excluded.updated_at
"""
SELECT_RECEIPT_SQL: Final = """
select owner_id, producer_id, operation_id, client_payload_hash, outcome,
       accepted_revision, received_at, result_json, server_cursor
from intake_operation_receipts
where owner_id = ? and producer_id = ? and operation_id = ?
"""
INSERT_RECEIPT_SQL: Final = """
insert into intake_operation_receipts (
    owner_id, producer_id, operation_id, client_payload_hash, outcome,
    accepted_revision, received_at, result_json
) values (?, ?, ?, ?, ?, ?, ?, ?)
"""
SELECT_TOMBSTONE_SQL: Final = """
select owner_id, producer_id, intake_id, deleted_at, revision, operation_id,
       domain_facts_hash
from intake_tombstones
where owner_id = ? and producer_id = ? and intake_id = ?
"""
INSERT_TOMBSTONE_SQL: Final = """
insert into intake_tombstones (
    owner_id, producer_id, intake_id, deleted_at, revision, operation_id,
    domain_facts_hash
) values (?, ?, ?, ?, ?, ?, ?)
"""


@contextmanager
def _write_scope(connection: sqlite3.Connection, operation: str) -> Generator[None]:
    if not connection.in_transaction:
        raise IntakeTransactionRequiredError(operation)
    _ = connection.execute(f"savepoint {SAVEPOINT_NAME}")
    try:
        yield
    except BaseException:
        # SQLite may already have rolled the whole transaction back (full disk,
        # I/O error, interrupt): the savepoint is then gone, and the caller must
        # see the original error, not "no such savepoint".
        if connection.in_transaction:
            _ = connection.execute(f"rollback to savepoint {SAVEPOINT_NAME}")
            _ = connection.execute(f"release savepoint {SAVEPOINT_NAME}")
        raise
    _ = connection.execute(f"release savepoint {SAVEPOINT_NAME}")


def _inserted_row_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        message = "insert did not return a row id"
        raise IntakeContextStorageError(message)
    return row_id


def _producer_from_row(row: ProducerRow) -> ProducerRecord:
    return ProducerRecord(
        owner_id=row[0],
        producer_id=row[1],
        writer_bundle_id=row[2],
        display_label=row[3],
        registered_at=row[4],
        revoked_at=row[5],
    )


def read_producer(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
) -> ProducerRecord | None:
    row = PRODUCER_ROW_ADAPTER.validate_python(
        connection.execute(SELECT_PRODUCER_SQL, (owner_id, producer_id)).fetchone()
    )
    return None if row is None else _producer_from_row(row)


def register_producer(
    connection: sqlite3.Connection,
    record: ProducerRecord,
) -> ProducerRecord:
    """Store a producer, or return the identical one already registered."""
    existing = read_producer(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
    )
    if existing is not None:
        if existing != record:
            raise IntakeProducerConflictError(record.producer_id)
        return existing
    with _write_scope(connection, "register_producer"):
        _ = connection.execute(
            INSERT_PRODUCER_SQL,
            (
                record.owner_id,
                record.producer_id,
                record.writer_bundle_id,
                record.display_label,
                record.registered_at,
                record.revoked_at,
            ),
        )
    return record


def _select_revision_row(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    intake_id: str,
    revision: int,
) -> RevisionRow | None:
    return REVISION_ROW_ADAPTER.validate_python(
        connection.execute(
            SELECT_REVISION_SQL,
            (owner_id, producer_id, intake_id, revision),
        ).fetchone()
    )


def _links_from_rows(rows: Sequence[LinkRow], sequence: int) -> tuple[SampleLink, ...]:
    return tuple(
        SampleLink(
            component_id=row[1],
            healthkit_type=row[2],
            sample_uuid=row[3],
            sync_identifier=row[4],
            sync_version=row[5],
            disposition=row[6],
            source_bundle_id=row[7],
            source_checked_at=row[8],
        )
        for row in rows
        if row[0] == sequence
    )


def _read_facts(
    connection: sqlite3.Connection,
    revision_row_id: int,
) -> tuple[FactRecord, ...]:
    members: dict[int, list[BlendMemberRecord]] = {}
    for member_row in MEMBER_ROWS_ADAPTER.validate_python(
        connection.execute(SELECT_MEMBERS_SQL, (revision_row_id,)).fetchall()
    ):
        members.setdefault(member_row[0], []).append(
            BlendMemberRecord(
                label_name=member_row[1],
                amount=member_row[2],
                unit=member_row[3],
            )
        )
    return tuple(
        FactRecord(
            component_id=row[1],
            kind=row[2],
            code=row[3],
            label_name=row[4],
            value_state=row[5],
            amount=row[6],
            unit=row[7],
            quantity_basis=row[8],
            aggregation_role=row[9],
            provenance=row[10],
            members=tuple(members.get(row[0], ())),
        )
        for row in FACT_ROWS_ADAPTER.validate_python(
            connection.execute(SELECT_FACTS_SQL, (revision_row_id,)).fetchall()
        )
    )


def _read_projections(
    connection: sqlite3.Connection,
    revision_row_id: int,
) -> tuple[ProjectionRecord, ...]:
    """Snapshots first, then only the links of the snapshots already read.

    Outside an explicit transaction a commit can land between the two reads;
    reading links second and bounding them by the last snapshot sequence read
    means a snapshot is never returned without its links, and links of a newer
    snapshot are never returned at all. Links are grouped in one pass.
    """
    snapshots = SNAPSHOT_ROWS_ADAPTER.validate_python(
        connection.execute(SELECT_SNAPSHOTS_SQL, (revision_row_id,)).fetchall()
    )
    if not snapshots:
        return ()
    last_sequence = max(snapshot[0] for snapshot in snapshots)
    grouped: dict[int, list[SampleLink]] = {}
    for row in LINK_ROWS_ADAPTER.validate_python(
        connection.execute(
            SELECT_LINKS_UP_TO_SQL, (revision_row_id, last_sequence)
        ).fetchall()
    ):
        grouped.setdefault(row[0], []).extend(_links_from_rows((row,), row[0]))
    return tuple(
        ProjectionRecord(
            projection_sequence=snapshot[0],
            projection_hash=snapshot[1],
            received_at=snapshot[2],
            links=tuple(grouped.get(snapshot[0], ())),
        )
        for snapshot in snapshots
    )


def _stored_revision(
    connection: sqlite3.Connection,
    row: RevisionRow,
) -> StoredRevision:
    revision_row_id = row[0]
    projections = _read_projections(connection, revision_row_id)
    first_links = projections[0].links if projections else ()
    record = RevisionRecord(
        owner_id=row[1],
        producer_id=row[2],
        intake_id=row[3],
        revision=row[4],
        domain_facts_hash=row[5],
        projection_hash=row[6],
        client_payload_hash=row[7],
        installation_id=row[8],
        operation_id=row[9],
        occurred_at=row[10],
        time_zone=row[11],
        recorded_at=row[12],
        category=row[13],
        display_name=row[14],
        serving_amount=row[15],
        serving_unit=row[16],
        nutrition_completeness=row[17],
        received_at=row[18],
        facts=_read_facts(connection, revision_row_id),
        links=first_links,
    )
    return StoredRevision(
        revision_row_id=revision_row_id,
        record=record,
        projections=projections,
    )


def read_revision(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    intake_id: str,
    revision: int,
) -> StoredRevision | None:
    """One stored revision with its facts, blend members and every link snapshot.

    Facts and members come back in their stored order. The links of the first
    snapshot are also on ``record.links``; later snapshots are on ``projections``.
    """
    row = _select_revision_row(
        connection,
        owner_id=owner_id,
        producer_id=producer_id,
        intake_id=intake_id,
        revision=revision,
    )
    return None if row is None else _stored_revision(connection, row)


def _insert_links(
    connection: sqlite3.Connection,
    revision_row_id: int,
    projection_sequence: int,
    links: Sequence[SampleLink],
) -> None:
    for link in links:
        _ = connection.execute(
            INSERT_LINK_SQL,
            (
                revision_row_id,
                projection_sequence,
                link.component_id,
                link.healthkit_type,
                link.sample_uuid,
                link.sync_identifier,
                link.sync_version,
                link.disposition,
                link.source_bundle_id,
                link.source_checked_at,
            ),
        )


def _insert_fact(
    connection: sqlite3.Connection,
    revision_row_id: int,
    position: int,
    fact: FactRecord,
) -> None:
    fact_row_id = _inserted_row_id(
        connection.execute(
            INSERT_FACT_SQL,
            (
                revision_row_id,
                fact.component_id,
                position,
                fact.kind,
                fact.code,
                fact.label_name,
                fact.value_state,
                fact.amount,
                fact.unit,
                fact.quantity_basis,
                fact.aggregation_role,
                fact.provenance,
            ),
        )
    )
    for member_position, member in enumerate(fact.members):
        _ = connection.execute(
            INSERT_MEMBER_SQL,
            (
                fact_row_id,
                member_position,
                member.label_name,
                member.amount,
                member.unit,
            ),
        )


def insert_revision(
    connection: sqlite3.Connection,
    record: RevisionRecord,
) -> StoredRevision:
    """Store one revision with its facts and first link snapshot atomically.

    The identity is (owner, producer, intake, revision). If it already holds
    the same domain facts hash and the same first projection, the stored
    revision is returned and nothing is written. A different first projection
    raises ``IntakeProjectionConflictError``. If it holds a different facts
    hash, ``IntakeRevisionConflictError`` is raised and nothing is written.
    """
    existing = _select_revision_row(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )
    if existing is not None:
        if existing[5] != record.domain_facts_hash:
            raise IntakeRevisionConflictError(
                owner_id=record.owner_id,
                producer_id=record.producer_id,
                intake_id=record.intake_id,
                revision=record.revision,
            )
        _ = insert_projection_snapshot(
            connection,
            revision_row_id=existing[0],
            snapshot=ProjectionRecord(
                projection_sequence=1,
                projection_hash=record.projection_hash,
                received_at=record.received_at,
                links=record.links,
            ),
        )
        return _stored_revision(connection, existing)
    with _write_scope(connection, "insert_revision"):
        revision_row_id = _inserted_row_id(
            connection.execute(
                INSERT_REVISION_SQL,
                (
                    record.owner_id,
                    record.producer_id,
                    record.intake_id,
                    record.revision,
                    record.domain_facts_hash,
                    record.projection_hash,
                    record.client_payload_hash,
                    record.installation_id,
                    record.operation_id,
                    record.occurred_at,
                    record.time_zone,
                    record.recorded_at,
                    record.category,
                    record.display_name,
                    record.serving_amount,
                    record.serving_unit,
                    record.nutrition_completeness,
                    record.received_at,
                ),
            )
        )
        for position, fact in enumerate(record.facts):
            _insert_fact(connection, revision_row_id, position, fact)
        _ = insert_projection_snapshot(
            connection,
            revision_row_id=revision_row_id,
            snapshot=ProjectionRecord(
                projection_sequence=1,
                projection_hash=record.projection_hash,
                received_at=record.received_at,
                links=record.links,
            ),
        )
    stored = read_revision(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
        revision=record.revision,
    )
    if stored is None:
        message = "stored revision could not be read back"
        raise IntakeContextStorageError(message)
    return stored


def insert_projection_snapshot(
    connection: sqlite3.Connection,
    *,
    revision_row_id: int,
    snapshot: ProjectionRecord,
) -> ProjectionRecord:
    """Store one complete link snapshot of a stored revision.

    The same sequence with the same projection hash returns the stored
    snapshot. The same sequence with a different hash raises
    ``IntakeProjectionConflictError``. Earlier snapshots are never touched, so
    superseded links stay available for audit.
    """
    existing = SNAPSHOT_ROW_ADAPTER.validate_python(
        connection.execute(
            SELECT_SNAPSHOT_SQL,
            (revision_row_id, snapshot.projection_sequence),
        ).fetchone()
    )
    if existing is not None:
        if existing[1] != snapshot.projection_hash:
            raise IntakeProjectionConflictError(
                revision_row_id=revision_row_id,
                projection_sequence=snapshot.projection_sequence,
            )
        link_rows = LINK_ROWS_ADAPTER.validate_python(
            connection.execute(SELECT_LINKS_SQL, (revision_row_id,)).fetchall()
        )
        return ProjectionRecord(
            projection_sequence=existing[0],
            projection_hash=existing[1],
            received_at=existing[2],
            links=_links_from_rows(link_rows, existing[0]),
        )
    with _write_scope(connection, "insert_projection_snapshot"):
        _ = connection.execute(
            INSERT_SNAPSHOT_SQL,
            (
                revision_row_id,
                snapshot.projection_sequence,
                snapshot.projection_hash,
                snapshot.received_at,
            ),
        )
        _insert_links(
            connection,
            revision_row_id,
            snapshot.projection_sequence,
            snapshot.links,
        )
    return snapshot


def read_intake_state(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    intake_id: str,
) -> IntakeStateRecord | None:
    row = STATE_ROW_ADAPTER.validate_python(
        connection.execute(
            SELECT_STATE_SQL,
            (owner_id, producer_id, intake_id),
        ).fetchone()
    )
    if row is None:
        return None
    return IntakeStateRecord(
        owner_id=row[0],
        producer_id=row[1],
        intake_id=row[2],
        current_revision=row[3],
        current_projection_sequence=row[4],
        deleted=bool(row[5]),
        updated_at=row[6],
    )


def write_intake_state(
    connection: sqlite3.Connection,
    state: IntakeStateRecord,
) -> None:
    """Set the current pointer of an intake; the caller decides what is current.

    The pointer never moves backwards and a deleted intake is never undeleted:
    either raises ``IntakeStateRegressionError`` and writes nothing.
    """
    existing = read_intake_state(
        connection,
        owner_id=state.owner_id,
        producer_id=state.producer_id,
        intake_id=state.intake_id,
    )
    if existing is not None and (
        (state.current_revision, state.current_projection_sequence)
        < (existing.current_revision, existing.current_projection_sequence)
        or (existing.deleted and not state.deleted)
    ):
        raise IntakeStateRegressionError(state.intake_id)
    with _write_scope(connection, "write_intake_state"):
        _ = connection.execute(
            WRITE_STATE_SQL,
            (
                state.owner_id,
                state.producer_id,
                state.intake_id,
                state.current_revision,
                state.current_projection_sequence,
                int(state.deleted),
                state.updated_at,
            ),
        )


def _receipt_from_row(row: ReceiptRow) -> OperationReceiptRecord:
    return OperationReceiptRecord(
        owner_id=row[0],
        producer_id=row[1],
        operation_id=row[2],
        client_payload_hash=row[3],
        outcome=row[4],
        accepted_revision=row[5],
        received_at=row[6],
        result_json=row[7],
        server_cursor=row[8],
    )


def read_operation_receipt(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    operation_id: str,
) -> OperationReceiptRecord | None:
    row = RECEIPT_ROW_ADAPTER.validate_python(
        connection.execute(
            SELECT_RECEIPT_SQL,
            (owner_id, producer_id, operation_id),
        ).fetchone()
    )
    return None if row is None else _receipt_from_row(row)


def append_operation_receipt(
    connection: sqlite3.Connection,
    record: OperationReceiptRecord,
) -> OperationReceiptRecord:
    """Append a receipt and return it with its server cursor.

    An operation id that was already recorded with the same client payload hash
    returns the stored receipt unchanged. With a different hash it raises
    ``IntakeOperationConflictError``. Receipts are never rewritten.
    """
    if record.outcome not in TERMINAL_OUTCOMES:
        raise IntakeNonTerminalOutcomeError(record.outcome)
    existing = read_operation_receipt(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        operation_id=record.operation_id,
    )
    if existing is not None:
        if existing.client_payload_hash != record.client_payload_hash:
            raise IntakeOperationConflictError(record.operation_id)
        return existing
    with _write_scope(connection, "append_operation_receipt"):
        cursor_value = _inserted_row_id(
            connection.execute(
                INSERT_RECEIPT_SQL,
                (
                    record.owner_id,
                    record.producer_id,
                    record.operation_id,
                    record.client_payload_hash,
                    record.outcome,
                    record.accepted_revision,
                    record.received_at,
                    record.result_json,
                ),
            )
        )
    return OperationReceiptRecord(
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        operation_id=record.operation_id,
        client_payload_hash=record.client_payload_hash,
        outcome=record.outcome,
        accepted_revision=record.accepted_revision,
        received_at=record.received_at,
        result_json=record.result_json,
        server_cursor=cursor_value,
    )


def read_tombstone(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    intake_id: str,
) -> TombstoneRecord | None:
    row = TOMBSTONE_ROW_ADAPTER.validate_python(
        connection.execute(
            SELECT_TOMBSTONE_SQL,
            (owner_id, producer_id, intake_id),
        ).fetchone()
    )
    if row is None:
        return None
    return TombstoneRecord(
        owner_id=row[0],
        producer_id=row[1],
        intake_id=row[2],
        deleted_at=row[3],
        revision=row[4],
        operation_id=row[5],
        domain_facts_hash=row[6],
    )


def write_tombstone(
    connection: sqlite3.Connection,
    record: TombstoneRecord,
) -> TombstoneRecord:
    """Write the permanent tombstone of an intake.

    A tombstone is never updated. The delete is identified by its domain facts
    hash: the same hash again, under any operation id, returns the stored
    tombstone; a different hash raises ``IntakeTombstoneConflictError``.
    """
    existing = read_tombstone(
        connection,
        owner_id=record.owner_id,
        producer_id=record.producer_id,
        intake_id=record.intake_id,
    )
    if existing is not None:
        if existing.domain_facts_hash != record.domain_facts_hash:
            raise IntakeTombstoneConflictError(record.intake_id)
        return existing
    with _write_scope(connection, "write_tombstone"):
        _ = connection.execute(
            INSERT_TOMBSTONE_SQL,
            (
                record.owner_id,
                record.producer_id,
                record.intake_id,
                record.deleted_at,
                record.revision,
                record.operation_id,
                record.domain_facts_hash,
            ),
        )
    return record


def _located_links(rows: Sequence[LocatedLinkRow]) -> tuple[StoredLink, ...]:
    return tuple(
        StoredLink(
            owner_id=row[0],
            producer_id=row[1],
            intake_id=row[2],
            revision=row[3],
            projection_sequence=row[4],
            link=SampleLink(
                component_id=row[5],
                healthkit_type=row[6],
                sample_uuid=row[7],
                sync_identifier=row[8],
                sync_version=row[9],
                disposition=row[10],
                source_bundle_id=row[11],
                source_checked_at=row[12],
            ),
        )
        for row in rows
    )


def list_links_by_sample_uuid(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    sample_uuid: str,
) -> tuple[StoredLink, ...]:
    """Every stored link, in every revision and snapshot, naming this sample."""
    return _located_links(
        LOCATED_LINK_ROWS_ADAPTER.validate_python(
            connection.execute(
                SELECT_LINKS_BY_SAMPLE_SQL,
                (owner_id, sample_uuid),
            ).fetchall()
        )
    )


def list_links_by_component_id(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    component_id: str,
    producer_id: str | None = None,
    intake_id: str | None = None,
) -> tuple[StoredLink, ...]:
    """Every stored link for a component, optionally for one producer or intake."""
    return _located_links(
        LOCATED_LINK_ROWS_ADAPTER.validate_python(
            connection.execute(
                SELECT_LINKS_BY_COMPONENT_SQL,
                (
                    owner_id,
                    component_id,
                    producer_id,
                    producer_id,
                    intake_id,
                    intake_id,
                ),
            ).fetchall()
        )
    )


__all__ = [
    "BlendMemberRecord",
    "FactRecord",
    "IntakeContextStorageError",
    "IntakeNonTerminalOutcomeError",
    "IntakeOperationConflictError",
    "IntakeProducerConflictError",
    "IntakeProjectionConflictError",
    "IntakeRevisionConflictError",
    "IntakeStateRecord",
    "IntakeTombstoneConflictError",
    "IntakeTransactionRequiredError",
    "OperationOutcome",
    "OperationReceiptRecord",
    "ProducerRecord",
    "ProjectionRecord",
    "RevisionRecord",
    "SampleLink",
    "StoredLink",
    "StoredRevision",
    "TombstoneRecord",
    "append_operation_receipt",
    "insert_projection_snapshot",
    "insert_revision",
    "list_links_by_component_id",
    "list_links_by_sample_uuid",
    "read_intake_state",
    "read_operation_receipt",
    "read_producer",
    "read_revision",
    "read_tombstone",
    "register_producer",
    "write_intake_state",
    "write_tombstone",
]

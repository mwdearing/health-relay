"""Acceptance of intake-context batches.

Each operation of a batch is applied in array order inside its own immediate
transaction, so a failure never undoes an earlier operation and every operation
sees the ones before it. Evaluation order per operation: schema and major
version, digest recomputation, the operation_id receipt, the tombstone, then the
revision and projection rules of ``docs/reference/intake-context-v1.md``.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias, TypedDict, cast

from health_bridge.contract.intake_context_v1 import (
    DeleteOperation,
    Fact,
    HealthKitLink,
    IntakeContextBatchV1,
    IntakeContextContractError,
    LinkProjectionOperation,
    UpsertOperation,
    expected_digests,
    healthkit_type_for,
    validate_batch,
)
from health_bridge.storage.intake_context import (
    TERMINAL_OUTCOMES,
    BlendMemberRecord,
    FactRecord,
    IntakeContextStorageError,
    IntakeStateRecord,
    OperationReceiptRecord,
    ProducerRecord,
    ProjectionRecord,
    RevisionRecord,
    SampleLink,
    StoredRevision,
    TombstoneRecord,
    append_operation_receipt,
    insert_projection_snapshot,
    insert_revision,
    read_intake_state,
    read_operation_receipt,
    read_producer,
    read_revision,
    read_tombstone,
    register_producer,
    write_intake_state,
    write_tombstone,
)

ResultName: TypeAlias = Literal[
    "accepted",
    "duplicate",
    "stale_revision",
    "domain_conflict",
    "projection_conflict",
    "retryable_failure",
    "permanent_failure",
]
Operation: TypeAlias = UpsertOperation | DeleteOperation | LinkProjectionOperation

_OPERATION_ID_TEXT: Final = re.compile(r'"operation_id"\s*:\s*"([^"\\]*)"')


class _Scope(TypedDict):
    owner_id: str
    producer_id: str


class _Key(TypedDict):
    owner_id: str
    producer_id: str
    intake_id: str


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation_id: str
    result: ResultName
    accepted_revision: int | None = None
    projection_sequence: int | None = None
    server_cursor: int | None = None
    current_revision: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class _Outcome:
    result: ResultName
    accepted_revision: int | None = None
    projection_sequence: int | None = None
    current_revision: int | None = None
    detail: str | None = None


def _operation_ids(payload: str | bytes | dict[str, object]) -> list[str]:
    """Every operation_id in a raw payload, even one that fails validation."""
    found: list[str] = []
    document: object = payload
    if not isinstance(payload, dict):
        text = (
            payload.decode("utf-8", "replace")
            if isinstance(payload, bytes)
            else payload
        )
        try:
            document = cast("object", json.loads(text))
        except ValueError:
            found = _OPERATION_ID_TEXT.findall(text)
            document = None
    if isinstance(document, dict):
        operations = cast("dict[str, object]", document).get("operations")
        if isinstance(operations, list):
            for operation in cast("list[object]", operations):
                if isinstance(operation, dict):
                    value = cast("dict[str, object]", operation).get("operation_id")
                    if isinstance(value, str):
                        found.append(value)
    return found


def _fact_record(fact: Fact) -> FactRecord:
    return FactRecord(
        component_id=fact.component_id,
        kind=fact.kind,
        code=fact.code,
        value_state=fact.value_state,
        aggregation_role=fact.aggregation_role,
        provenance=fact.provenance,
        label_name=fact.label_name,
        amount=fact.amount,
        unit=fact.unit,
        quantity_basis=fact.quantity_basis,
        members=tuple(
            BlendMemberRecord(label_name=m.label_name, amount=m.amount, unit=m.unit)
            for m in fact.members or ()
        ),
    )


def _link_records(links: list[HealthKitLink]) -> tuple[SampleLink, ...]:
    return tuple(
        SampleLink(
            component_id=link.component_id,
            healthkit_type=link.healthkit_type,
            sample_uuid=link.healthkit_sample_uuid,
            sync_identifier=link.sync_identifier,
            sync_version=link.sync_version,
            disposition=link.disposition,
        )
        for link in links
    )


def _receipt_json(outcome: _Outcome) -> str:
    return json.dumps(
        {
            "accepted_revision": outcome.accepted_revision,
            "projection_sequence": outcome.projection_sequence,
            "current_revision": outcome.current_revision,
        }
    )


def _replay(
    connection: sqlite3.Connection,
    receipt: OperationReceiptRecord,
    operation_id: str,
    key: _Key,
) -> OperationResult:
    stored = cast("dict[str, int | None]", json.loads(receipt.result_json or "{}"))
    name: ResultName = (
        "duplicate" if receipt.outcome in {"accepted", "duplicate"} else receipt.outcome
    )
    current_revision = stored.get("current_revision")
    if name == "stale_revision":
        current = read_intake_state(connection, **key)
        if current is not None:
            current_revision = current.current_revision
    return OperationResult(
        operation_id=operation_id,
        result=name,
        accepted_revision=receipt.accepted_revision,
        projection_sequence=stored.get("projection_sequence"),
        server_cursor=receipt.server_cursor,
        current_revision=current_revision,
    )


def _stale(current: int) -> _Outcome:
    return _Outcome("stale_revision", current_revision=current)


def _apply_upsert(
    connection: sqlite3.Connection,
    batch: IntakeContextBatchV1,
    operation: UpsertOperation,
    key: _Key,
    received_at: str,
) -> _Outcome:
    existing = read_revision(connection, revision=operation.revision, **key)
    if existing is not None:
        record = existing.record
        if record.domain_facts_hash != operation.domain_facts_hash:
            return _Outcome(
                "domain_conflict", detail="different facts at this revision"
            )
        if record.projection_hash != operation.projection_hash:
            return _Outcome(
                "projection_conflict", detail="different first link snapshot"
            )
        return _Outcome("duplicate", operation.revision, 1)
    current = read_intake_state(connection, **key)
    if current is not None and operation.revision < current.current_revision:
        return _stale(current.current_revision)
    _ = insert_revision(
        connection,
        RevisionRecord(
            **key,
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
            received_at=received_at,
            facts=tuple(_fact_record(f) for f in operation.facts),
            links=_link_records(operation.healthkit_links),
        ),
    )
    write_intake_state(
        connection,
        IntakeStateRecord(
            **key,
            current_revision=operation.revision,
            current_projection_sequence=1,
            deleted=False,
            updated_at=received_at,
        ),
    )
    return _Outcome("accepted", operation.revision, 1)


def _link_error(stored: StoredRevision, links: list[HealthKitLink]) -> str | None:
    facts = {fact.component_id: fact for fact in stored.record.facts}
    for link in links:
        fact = facts.get(link.component_id)
        if fact is None or fact.kind != "nutrient":
            return f"{link.component_id} is not a nutrient fact of the target revision"
        if link.healthkit_type != healthkit_type_for(fact.code):
            return f"{link.healthkit_type} is not the type of {fact.code}"
    return None


def _known_snapshot(
    stored: StoredRevision, operation: LinkProjectionOperation
) -> _Outcome | None:
    for snapshot in stored.projections:
        if snapshot.projection_sequence == operation.projection_sequence:
            if snapshot.projection_hash == operation.projection_hash:
                return _Outcome(
                    "duplicate", operation.revision, operation.projection_sequence
                )
            return _Outcome(
                "projection_conflict", detail="different snapshot at this sequence"
            )
    return None


def _link_projection_blocker(
    connection: sqlite3.Connection,
    operation: LinkProjectionOperation,
    key: _Key,
) -> tuple[_Outcome | None, StoredRevision | None]:
    """The outcome that ends this operation early, or the target revision."""
    current = read_intake_state(connection, **key)
    if current is not None and operation.revision < current.current_revision:
        return _stale(current.current_revision), None
    stored = (
        read_revision(connection, revision=operation.revision, **key)
        if current is not None and operation.revision == current.current_revision
        else None
    )
    if current is None or stored is None:
        return _Outcome(
            "retryable_failure", detail="target revision not accepted yet"
        ), None
    problem = _link_error(stored, operation.healthkit_links)
    if problem is not None:
        return _Outcome("permanent_failure", detail=problem), None
    known = _known_snapshot(stored, operation)
    if known is not None:
        return known, None
    if operation.projection_sequence < current.current_projection_sequence:
        return _stale(current.current_revision), None
    return None, stored


def _apply_link_projection(
    connection: sqlite3.Connection,
    operation: LinkProjectionOperation,
    key: _Key,
    received_at: str,
) -> _Outcome:
    early, stored = _link_projection_blocker(connection, operation, key)
    if early is not None or stored is None:
        return early or _Outcome("retryable_failure")
    sequence = operation.projection_sequence
    _ = insert_projection_snapshot(
        connection,
        revision_row_id=stored.revision_row_id,
        snapshot=ProjectionRecord(
            projection_sequence=sequence,
            projection_hash=operation.projection_hash,
            received_at=received_at,
            links=_link_records(operation.healthkit_links),
        ),
    )
    write_intake_state(
        connection,
        IntakeStateRecord(
            **key,
            current_revision=operation.revision,
            current_projection_sequence=sequence,
            deleted=False,
            updated_at=received_at,
        ),
    )
    return _Outcome("accepted", operation.revision, sequence)


def _apply_delete(
    connection: sqlite3.Connection,
    operation: DeleteOperation,
    key: _Key,
    received_at: str,
) -> _Outcome:
    current = read_intake_state(connection, **key)
    if current is not None and operation.revision <= current.current_revision:
        return _stale(current.current_revision)
    _ = write_tombstone(
        connection,
        TombstoneRecord(
            **key,
            deleted_at=operation.deleted_at,
            revision=operation.revision,
            operation_id=operation.operation_id,
            domain_facts_hash=operation.domain_facts_hash,
        ),
    )
    write_intake_state(
        connection,
        IntakeStateRecord(
            **key,
            current_revision=operation.revision,
            current_projection_sequence=(
                1 if current is None else current.current_projection_sequence
            ),
            deleted=True,
            updated_at=received_at,
        ),
    )
    return _Outcome("accepted", operation.revision)


def _apply(
    connection: sqlite3.Connection,
    batch: IntakeContextBatchV1,
    operation: Operation,
    owner_id: str,
    received_at: str,
) -> OperationResult:
    key = _Key(
        owner_id=owner_id,
        producer_id=batch.producer_id,
        intake_id=operation.intake_id,
    )
    scope = _Scope(owner_id=owner_id, producer_id=batch.producer_id)
    is_link = isinstance(operation, LinkProjectionOperation)
    receipt = read_operation_receipt(
        connection, operation_id=operation.operation_id, **scope
    )
    if receipt is not None:
        if receipt.client_payload_hash == operation.client_payload_hash:
            return _replay(connection, receipt, operation.operation_id, key)
        return OperationResult(
            operation.operation_id,
            "projection_conflict" if is_link else "domain_conflict",
            detail="operation_id reused with different content",
        )
    producer = read_producer(connection, **scope)
    newly_registered = producer is None
    if producer is None:
        _ = register_producer(
            connection,
            ProducerRecord(
                **scope,
                writer_bundle_id=batch.writer_bundle_id,
                display_label=batch.producer_id,
                registered_at=received_at,
            ),
        )
    elif producer.revoked_at is not None or (
        producer.writer_bundle_id != batch.writer_bundle_id
    ):
        return OperationResult(
            operation.operation_id,
            "permanent_failure",
            detail="producer is revoked or the writer bundle does not match",
        )
    tombstone = read_tombstone(connection, **key)
    if tombstone is not None:
        same_delete = (
            isinstance(operation, DeleteOperation)
            and operation.domain_facts_hash == tombstone.domain_facts_hash
        )
        outcome = (
            _Outcome("duplicate", tombstone.revision)
            if same_delete
            else _stale(tombstone.revision)
        )
    elif isinstance(operation, UpsertOperation):
        outcome = _apply_upsert(connection, batch, operation, key, received_at)
    elif isinstance(operation, LinkProjectionOperation):
        outcome = _apply_link_projection(connection, operation, key, received_at)
    else:
        outcome = _apply_delete(connection, operation, key, received_at)
    if newly_registered and outcome.result != "accepted":
        _ = connection.execute(
            "delete from intake_producers where owner_id = ? and producer_id = ?",
            (owner_id, batch.producer_id),
        )
    cursor: int | None = None
    if outcome.result in TERMINAL_OUTCOMES:
        stored = append_operation_receipt(
            connection,
            OperationReceiptRecord(
                **scope,
                operation_id=operation.operation_id,
                client_payload_hash=operation.client_payload_hash,
                outcome=cast("Literal['accepted']", outcome.result),
                received_at=received_at,
                accepted_revision=outcome.accepted_revision,
                result_json=_receipt_json(outcome),
            ),
        )
        cursor = stored.server_cursor
    return OperationResult(
        operation.operation_id,
        outcome.result,
        accepted_revision=outcome.accepted_revision,
        projection_sequence=outcome.projection_sequence,
        server_cursor=cursor,
        current_revision=outcome.current_revision,
        detail=outcome.detail,
    )


def _is_lock_error(error: sqlite3.OperationalError) -> bool:
    text = str(error).lower()
    return "locked" in text or "busy" in text


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.rollback()


def _in_own_transaction(
    connection: sqlite3.Connection,
    batch: IntakeContextBatchV1,
    operation: Operation,
    owner_id: str,
    received_at: str,
) -> OperationResult:
    try:
        _ = connection.execute("begin immediate")
        result = _apply(connection, batch, operation, owner_id, received_at)
        connection.commit()
    except sqlite3.OperationalError as error:
        _rollback(connection)
        if not _is_lock_error(error):
            raise
        return OperationResult(
            operation.operation_id, "retryable_failure", detail=str(error)
        )
    except (sqlite3.IntegrityError, IntakeContextStorageError) as error:
        _rollback(connection)
        return OperationResult(
            operation.operation_id,
            "permanent_failure",
            detail=type(error).__name__,
        )
    except BaseException:
        _rollback(connection)
        raise
    return result


def _mismatched_positions(batch: IntakeContextBatchV1) -> set[int]:
    """Array indexes of operations carrying a digest that recomputes differently."""
    return {
        position
        for position, expected in enumerate(expected_digests(batch))
        if any(
            getattr(batch.operations[position], field, value) != value
            for field, value in expected.items()
        )
    }


def accept_batch(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    payload: str | bytes | dict[str, object],
    received_at: str,
) -> list[OperationResult]:
    """Apply a batch operation by operation and return one result for each.

    The connection must have no open transaction. Validation failures and
    digest mismatches are not stored: nothing about them is trustworthy enough
    to key a receipt on. A ``retryable_failure`` is never stored either.
    """
    if connection.in_transaction:
        message = "accept_batch needs a connection without an open transaction"
        raise sqlite3.ProgrammingError(message)
    try:
        batch = validate_batch(payload)
    except IntakeContextContractError as error:
        return [
            OperationResult(op_id, "permanent_failure", detail=str(error))
            for op_id in _operation_ids(payload)
        ]
    bad = _mismatched_positions(batch)
    results: list[OperationResult] = []
    for position, operation in enumerate(batch.operations):
        if position in bad:
            results.append(
                OperationResult(
                    operation.operation_id,
                    "permanent_failure",
                    detail="a digest does not match its recomputed value",
                )
            )
            continue
        results.append(
            _in_own_transaction(connection, batch, operation, owner_id, received_at)
        )
    return results

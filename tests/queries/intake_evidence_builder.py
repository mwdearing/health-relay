"""Synthetic database builder for intake-evidence query tests.

Writes valid rows through the real intake-context storage API and plain
inserts for samples and sources. All identifiers are synthetic.
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

from health_bridge.contract.intake_context_v1 import AggregationRole, FactKind
from health_bridge.storage import initialize_database
from health_bridge.storage.intake_context import (
    FactRecord,
    IntakeStateRecord,
    ProducerRecord,
    ProjectionRecord,
    RevisionRecord,
    SampleLink,
    TombstoneRecord,
    insert_projection_snapshot,
    insert_revision,
    read_intake_state,
    read_revision,
    register_producer,
    write_intake_state,
    write_tombstone,
)

OWNER: Final = "owner-1"
PRODUCER: Final = "nutrition-app"
WRITER_BUNDLE: Final = "dev.example.nutrition"
EXPORTER_BUNDLE: Final = "dev.example.companion"
RECEIVED_AT: Final = "2026-10-01T12:00:05Z"
OCCURRED_AT: Final = "2026-10-01T07:00:00-05:00"
TIME_ZONE: Final = "America/Chicago"

_AGGREGATION_ROLES: Final[dict[str, AggregationRole]] = {
    "nutrient": "context_only",
    "compound": "compound_measurement",
    "blend": "blend_total_only",
}
INSERT_TYPE_SQL: Final = """
insert or ignore into health_types
    (type_code, display_name, category, default_unit, sensitivity)
values (?, ?, ?, ?, 'sensitive')
"""
INSERT_SOURCE_SQL: Final = """
insert or ignore into sources (source_key, name, kind, bundle_id)
values (?, 'Synthetic Phone', 'phone', ?)
"""
INSERT_SAMPLE_SQL: Final = """
insert into samples
    (source_id, type_code, client_record_id, start_time, end_time, value, unit,
     metadata_json)
values (?, ?, ?, ?, ?, ?, ?, ?)
"""
_TYPE_DETAILS: Final = {
    "hydration": ("Hydration", "nutrition", "mL"),
}


def make_connection(path: Path) -> sqlite3.Connection:
    initialize_database(path)
    connection = sqlite3.connect(path)
    _ = connection.execute("pragma foreign_keys = on")
    return connection


def hk_identifier(code: str) -> str:
    if code == "hydration":
        return "HKQuantityTypeIdentifierDietaryWater"
    parts = code.split("_")
    return "HKQuantityTypeIdentifier" + "".join(part.capitalize() for part in parts)


def exporter_client_record_id(code: str, sample_uuid: str) -> str:
    return f"hk-quantity-{code.replace('_', '-')}-{sample_uuid.lower()}"


def _digest(*parts: object) -> str:
    text = "|".join(str(part) for part in parts)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _begin(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        _ = connection.execute("begin immediate")


def fact(  # noqa: PLR0913
    component_id: str,
    code: str = "hydration",
    *,
    kind: FactKind = "nutrient",
    amount: str | None = "500",
    unit: str | None = "mL",
    value_state: str = "known",
) -> FactRecord:
    return FactRecord(
        component_id=component_id,
        kind=kind,
        code=code,
        value_state=value_state,  # pyright: ignore[reportArgumentType]
        aggregation_role=_AGGREGATION_ROLES[kind],
        provenance="user_confirmed",
        label_name=None,
        amount=amount,
        unit=unit,
        quantity_basis="compound_mass" if kind == "compound" else None,
    )


def link(
    component_id: str,
    sample_uuid: str,
    code: str = "hydration",
    *,
    disposition: str = "active",
    sync_version: int = 1,
) -> SampleLink:
    return SampleLink(
        component_id=component_id,
        healthkit_type=hk_identifier(code),
        sample_uuid=sample_uuid,
        sync_identifier=f"intake:{component_id}",
        sync_version=sync_version,
        disposition=disposition,  # pyright: ignore[reportArgumentType]
    )


def register_producer_row(
    connection: sqlite3.Connection,
    *,
    owner_id: str = OWNER,
    producer_id: str = PRODUCER,
    writer_bundle_id: str = WRITER_BUNDLE,
) -> None:
    _begin(connection)
    _ = register_producer(
        connection,
        ProducerRecord(
            owner_id=owner_id,
            producer_id=producer_id,
            writer_bundle_id=writer_bundle_id,
            display_label="Synthetic Nutrition App",
            registered_at="2026-09-01T00:00:00Z",
        ),
    )
    connection.commit()


def _projection_hash(  # noqa: PLR0913
    owner_id: str,
    producer_id: str,
    intake_id: str,
    revision: int,
    sequence: int,
    links: Sequence[SampleLink],
) -> str:
    return _digest(
        owner_id,
        producer_id,
        intake_id,
        revision,
        "projection",
        sequence,
        repr(tuple(links)),
    )


def put_revision(  # noqa: PLR0913
    connection: sqlite3.Connection,
    *,
    intake_id: str,
    revision: int,
    facts: Sequence[FactRecord],
    links: Sequence[SampleLink] = (),
    owner_id: str = OWNER,
    producer_id: str = PRODUCER,
) -> int:
    _begin(connection)
    key = (owner_id, producer_id, intake_id, revision)
    stored = insert_revision(
        connection,
        RevisionRecord(
            owner_id=owner_id,
            producer_id=producer_id,
            intake_id=intake_id,
            revision=revision,
            domain_facts_hash=_digest(*key, "facts"),
            projection_hash=_projection_hash(*key, 1, links),
            client_payload_hash=_digest(*key, "payload"),
            installation_id="22222222-2222-4222-8222-222222222222",
            operation_id=f"op-{intake_id}-{revision}",
            occurred_at=OCCURRED_AT,
            time_zone=TIME_ZONE,
            recorded_at=OCCURRED_AT,
            category="drink",
            display_name="Synthetic Drink",
            serving_amount="1",
            serving_unit="serving",
            nutrition_completeness="complete",
            received_at=RECEIVED_AT,
            facts=tuple(facts),
            links=tuple(links),
        ),
    )
    existing = read_intake_state(
        connection, owner_id=owner_id, producer_id=producer_id, intake_id=intake_id
    )
    if existing is None or existing.current_revision < revision:
        write_intake_state(
            connection,
            IntakeStateRecord(
                owner_id=owner_id,
                producer_id=producer_id,
                intake_id=intake_id,
                current_revision=revision,
                current_projection_sequence=1,
                deleted=False,
                updated_at=RECEIVED_AT,
            ),
        )
    connection.commit()
    return stored.revision_row_id


def put_projection(  # noqa: PLR0913
    connection: sqlite3.Connection,
    *,
    intake_id: str,
    revision: int,
    sequence: int,
    links: Sequence[SampleLink],
    owner_id: str = OWNER,
    producer_id: str = PRODUCER,
) -> None:
    _begin(connection)
    stored = read_revision(
        connection,
        owner_id=owner_id,
        producer_id=producer_id,
        intake_id=intake_id,
        revision=revision,
    )
    if stored is None:
        message = "projection needs a stored revision"
        raise LookupError(message)
    revision_row_id = stored.revision_row_id
    _ = insert_projection_snapshot(
        connection,
        revision_row_id=revision_row_id,
        snapshot=ProjectionRecord(
            projection_sequence=sequence,
            projection_hash=_projection_hash(
                owner_id, producer_id, intake_id, revision, sequence, links
            ),
            received_at=RECEIVED_AT,
            links=tuple(links),
        ),
    )
    state = read_intake_state(
        connection, owner_id=owner_id, producer_id=producer_id, intake_id=intake_id
    )
    if state is not None and state.current_revision == revision:
        write_intake_state(
            connection,
            IntakeStateRecord(
                owner_id=owner_id,
                producer_id=producer_id,
                intake_id=intake_id,
                current_revision=revision,
                current_projection_sequence=max(
                    sequence, state.current_projection_sequence
                ),
                deleted=state.deleted,
                updated_at=RECEIVED_AT,
            ),
        )
    connection.commit()


def put_tombstone(
    connection: sqlite3.Connection,
    *,
    intake_id: str,
    revision: int,
    owner_id: str = OWNER,
    producer_id: str = PRODUCER,
) -> None:
    _begin(connection)
    state = read_intake_state(
        connection, owner_id=owner_id, producer_id=producer_id, intake_id=intake_id
    )
    _ = write_tombstone(
        connection,
        TombstoneRecord(
            owner_id=owner_id,
            producer_id=producer_id,
            intake_id=intake_id,
            deleted_at=RECEIVED_AT,
            revision=revision,
            operation_id=f"op-{intake_id}-{revision}-delete",
            domain_facts_hash=_digest(
                owner_id, producer_id, intake_id, revision, "tombstone"
            ),
        ),
    )
    write_intake_state(
        connection,
        IntakeStateRecord(
            owner_id=owner_id,
            producer_id=producer_id,
            intake_id=intake_id,
            current_revision=revision,
            current_projection_sequence=(
                1 if state is None else state.current_projection_sequence
            ),
            deleted=True,
            updated_at=RECEIVED_AT,
        ),
    )
    connection.commit()


def put_sample(  # noqa: PLR0913
    connection: sqlite3.Connection,
    *,
    sample_uuid: str,
    type_code: str = "hydration",
    bundle_id: str | None = WRITER_BUNDLE,
    client_record_id: str | None = None,
    source_key: str = "apple_health.phone",
    start_time: str = "2026-10-01T12:00:00Z",
    value: float = 500.0,
    unit: str = "mL",
) -> int:
    display_name, category, default_unit = _TYPE_DETAILS.get(
        type_code,
        (type_code.replace("_", " ").title(), "nutrition", unit),
    )
    _ = connection.execute(
        INSERT_TYPE_SQL,
        (type_code, display_name, category, default_unit),
    )
    _ = connection.execute(
        INSERT_SOURCE_SQL,
        (source_key, EXPORTER_BUNDLE),
    )
    source_id = cast(
        "int",
        connection.execute(
            "select source_id from sources where source_key = ?", (source_key,)
        ).fetchone()[0],
    )
    metadata: dict[str, str] = {
        "healthkit_source_name": "Synthetic Nutrition App",
        "sample_kind": "raw_quantity",
    }
    if type_code == "hydration" or type_code.startswith("dietary_"):
        metadata["healthkit_identifier"] = hk_identifier(type_code)
    if bundle_id is not None:
        metadata["healthkit_source_bundle_id"] = bundle_id
    cursor = connection.execute(
        INSERT_SAMPLE_SQL,
        (
            source_id,
            type_code,
            client_record_id or exporter_client_record_id(type_code, sample_uuid),
            start_time,
            start_time,
            value,
            unit,
            json.dumps(metadata, sort_keys=True),
        ),
    )
    sample_id = cursor.lastrowid
    connection.commit()
    if sample_id is None:
        message = "sample insert returned no row id"
        raise RuntimeError(message)
    return sample_id

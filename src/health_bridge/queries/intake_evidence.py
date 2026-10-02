"""Effective measurement view of intake components.

An intake component carries claims about HealthKit samples. This query reports,
for the newest accepted revision of every live intake, what the receiver can
verify about each claim by exact joins only. It never joins on time, name or
amount.
"""

import base64
import binascii
import json
import sqlite3
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias, cast, final

from pydantic import TypeAdapter

LinkStatus: TypeAlias = Literal["verified", "pending", "unlinked", "mismatch"]

DEFAULT_LIMIT: Final = 100
MIN_LIMIT: Final = 1
MAX_LIMIT: Final = 500
EXPORTER_RECORD_PREFIX: Final = "hk-quantity-"
SOURCE_BUNDLE_METADATA_KEY: Final = "healthkit_source_bundle_id"
HEALTHKIT_IDENTIFIER_METADATA_KEY: Final = "healthkit_identifier"

ComponentRow: TypeAlias = tuple[
    str,  # intake_id
    str,  # producer_id
    int,  # revision
    int,  # intake_revision_row_id
    int,  # component position
    str,  # component_id
    str,  # kind
    str,  # code
    str | None,  # amount
    str | None,  # unit
    str,  # value_state
    str | None,  # registered writer bundle
]
ActiveLinkRow: TypeAlias = tuple[str, str]
SampleRow: TypeAlias = tuple[str, str, str | None]
COMPONENT_ROWS_ADAPTER: Final[TypeAdapter[list[ComponentRow]]] = TypeAdapter(
    list[ComponentRow],
)
ACTIVE_LINK_ROWS_ADAPTER: Final[TypeAdapter[list[ActiveLinkRow]]] = TypeAdapter(
    list[ActiveLinkRow],
)
SAMPLE_ROWS_ADAPTER: Final[TypeAdapter[list[SampleRow]]] = TypeAdapter(
    list[SampleRow],
)

# Effective revision: the newest accepted revision of each intake. A tombstone,
# or a state pointer marked deleted, removes the intake from the view.
COMPONENTS_SQL: Final = """
select revision.intake_id, revision.producer_id, revision.revision,
       revision.intake_revision_row_id, fact.position, fact.component_id,
       fact.kind, fact.code, fact.amount, fact.unit, fact.value_state,
       producer.writer_bundle_id
from intake_revisions as revision
join intake_compound_facts as fact
  on fact.intake_revision_row_id = revision.intake_revision_row_id
left join intake_producers as producer
  on producer.owner_id = revision.owner_id
 and producer.producer_id = revision.producer_id
where revision.owner_id = :owner_id
  and revision.revision = (
      select max(newer.revision) from intake_revisions as newer
      where newer.owner_id = revision.owner_id
        and newer.producer_id = revision.producer_id
        and newer.intake_id = revision.intake_id
  )
  and not exists (
      select 1 from intake_tombstones as tombstone
      where tombstone.owner_id = revision.owner_id
        and tombstone.producer_id = revision.producer_id
        and tombstone.intake_id = revision.intake_id
  )
  and not exists (
      select 1 from intake_state as state
      where state.owner_id = revision.owner_id
        and state.producer_id = revision.producer_id
        and state.intake_id = revision.intake_id
        and state.deleted = 1
  )
  and (:intake_id is null or revision.intake_id = :intake_id)
  and (
      :after_intake_id is null
      or (revision.intake_id, revision.producer_id, fact.position)
         >= (:after_intake_id, :after_producer_id, :after_position)
  )
order by revision.intake_id, revision.producer_id, fact.position
limit :row_limit
"""
# The newest projection snapshot of a revision is its current claim. Links are
# fetched per component and bounded by what the page still needs.
COMPONENT_LINKS_SQL: Final = """
select link.sample_uuid, link.healthkit_type
from intake_sample_links as link
where link.intake_revision_row_id = :revision_row_id
  and link.component_id = :component_id
  and link.disposition = 'active'
  and link.projection_sequence = (
      select max(snapshot.projection_sequence)
      from intake_projection_snapshots as snapshot
      where snapshot.intake_revision_row_id = link.intake_revision_row_id
  )
  and (:after_link is null or link.sample_uuid > :after_link)
order by link.sample_uuid
limit :link_limit
"""
# Candidate (type, id) pairs only, probed through the unique index on
# (source_id, type_code, client_record_id), so cost does not grow with history.
# `cross join` pins the join order so the planner cannot scan samples.
# Pairs cover the exporter id under every stored type and the expected id under
# every stored type (a sample stored under another type).
SAMPLES_SQL: Final = """
with candidate(type_code, client_record_id) as (
    select json_extract(value, '$[0]'), json_extract(value, '$[1]')
    from json_each(:pairs)
)
select samples.client_record_id, samples.type_code, samples.metadata_json
from candidate
cross join sources
cross join samples
  on samples.source_id = sources.source_id
 and samples.type_code = candidate.type_code
 and samples.client_record_id = candidate.client_record_id
order by samples.sample_id
"""
DISTINCT_TYPE_CODES_SQL: Final = """
select type_code from health_types order by type_code
"""
# The same sample actively claimed by another effective component is a conflict.
OTHER_ACTIVE_CLAIMS_SQL: Final = """
select count(*)
from intake_sample_links as link
join intake_revisions as revision
  on revision.intake_revision_row_id = link.intake_revision_row_id
where revision.owner_id = :owner_id
  and link.sample_uuid = :sample_uuid
  and link.disposition = 'active'
  and not (
      revision.producer_id = :producer_id
      and revision.intake_id = :intake_id
      and link.component_id = :component_id
  )
  and revision.revision = (
      select max(newer.revision) from intake_revisions as newer
      where newer.owner_id = revision.owner_id
        and newer.producer_id = revision.producer_id
        and newer.intake_id = revision.intake_id
  )
  and link.projection_sequence = (
      select max(snapshot.projection_sequence)
      from intake_projection_snapshots as snapshot
      where snapshot.intake_revision_row_id = link.intake_revision_row_id
  )
  and not exists (
      select 1 from intake_tombstones as tombstone
      where tombstone.owner_id = revision.owner_id
        and tombstone.producer_id = revision.producer_id
        and tombstone.intake_id = revision.intake_id
  )
  and not exists (
      select 1 from intake_state as state
      where state.owner_id = revision.owner_id
        and state.producer_id = revision.producer_id
        and state.intake_id = revision.intake_id
        and state.deleted = 1
  )
"""
DELETED_SAMPLE_SQL: Final = """
select 1
from sources
join deleted_records
  on deleted_records.source_id = sources.source_id
 and deleted_records.record_family = 'sample'
 and deleted_records.client_record_id in (select value from json_each(:ids))
limit 1
"""
# One aggregate per component: how many of its current active links are not
# verified. Mirrors the per-link resolution: no exact sample from the registered
# writer, a copy under another (type, id), a tombstone under any candidate id,
# or another effective component actively claiming the same sample.
UNVERIFIED_LINK_COUNT_SQL: Final = """
select count(*)
from intake_sample_links as link
where link.intake_revision_row_id = :revision_row_id
  and link.component_id = :component_id
  and link.disposition = 'active'
  and link.projection_sequence = (
      select max(snapshot.projection_sequence)
      from intake_projection_snapshots as snapshot
      where snapshot.intake_revision_row_id = link.intake_revision_row_id
  )
  and (
      not exists (
          select 1
          from sources as writer_src
          cross join samples as exact
            on exact.source_id = writer_src.source_id
           and exact.type_code = :code
           and exact.client_record_id = :id_prefix || lower(link.sample_uuid)
          where json_valid(exact.metadata_json)
            and json_extract(exact.metadata_json, '$.healthkit_source_bundle_id')
                = :writer_bundle_id
            and (
                json_extract(exact.metadata_json, '$.healthkit_identifier') is null
                or json_extract(exact.metadata_json, '$.healthkit_identifier')
                   = link.healthkit_type
            )
      )
      or exists (
          select 1
          from health_types as probe_type
          cross join sources as probe_src
          cross join samples as other
            on other.source_id = probe_src.source_id
           and other.type_code = probe_type.type_code
           and other.client_record_id = :id_prefix || lower(link.sample_uuid)
          where probe_type.type_code != :code
      )
      or exists (
          select 1
          from health_types as alias_type
          cross join sources as alias_src
          cross join samples as alias_sample
            on alias_sample.source_id = alias_src.source_id
           and alias_sample.type_code = alias_type.type_code
           and alias_sample.client_record_id = 'hk-quantity-'
               || replace(alias_type.type_code, '_', '-') || '-'
               || lower(link.sample_uuid)
          where alias_type.type_code != :code
      )
      or exists (
          select 1
          from sources as del_src
          cross join deleted_records as deleted
            on deleted.source_id = del_src.source_id
           and deleted.record_family = 'sample'
           and deleted.client_record_id = :id_prefix || lower(link.sample_uuid)
      )
      or exists (
          select 1
          from health_types as del_type
          cross join sources as del_type_src
          cross join deleted_records as deleted_alias
            on deleted_alias.source_id = del_type_src.source_id
           and deleted_alias.record_family = 'sample'
           and deleted_alias.client_record_id = 'hk-quantity-'
               || replace(del_type.type_code, '_', '-') || '-'
               || lower(link.sample_uuid)
      )
      or exists (
          select 1
          from intake_sample_links as claim
          join intake_revisions as claim_revision
            on claim_revision.intake_revision_row_id = claim.intake_revision_row_id
          where claim_revision.owner_id = :owner_id
            and claim.sample_uuid = link.sample_uuid
            and claim.disposition = 'active'
            and not (
                claim_revision.producer_id = :producer_id
                and claim_revision.intake_id = :intake_id
                and claim.component_id = :component_id
            )
            and claim_revision.revision = (
                select max(newer.revision) from intake_revisions as newer
                where newer.owner_id = claim_revision.owner_id
                  and newer.producer_id = claim_revision.producer_id
                  and newer.intake_id = claim_revision.intake_id
            )
            and claim.projection_sequence = (
                select max(snapshot.projection_sequence)
                from intake_projection_snapshots as snapshot
                where snapshot.intake_revision_row_id = claim.intake_revision_row_id
            )
            and not exists (
                select 1 from intake_tombstones as tombstone
                where tombstone.owner_id = claim_revision.owner_id
                  and tombstone.producer_id = claim_revision.producer_id
                  and tombstone.intake_id = claim_revision.intake_id
            )
            and not exists (
                select 1 from intake_state as state
                where state.owner_id = claim_revision.owner_id
                  and state.producer_id = claim_revision.producer_id
                  and state.intake_id = claim_revision.intake_id
                  and state.deleted = 1
            )
      )
  )
"""


@final
class InvalidIntakeEvidenceCursorError(ValueError):
    def __init__(self) -> None:
        super().__init__("cursor is not a valid intake evidence cursor")


@final
class InvalidIntakeEvidenceLimitError(ValueError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}, got {limit}",
        )


@dataclass(frozen=True, slots=True)
class IntakeEvidenceItem:
    intake_id: str
    producer_id: str
    revision: int
    component_id: str
    kind: str
    code: str
    amount: str | None
    unit: str | None
    value_state: str
    sample_uuid: str | None
    client_record_id: str | None
    healthkit_type: str | None
    writer_bundle_id: str | None
    link_status: LinkStatus
    complete: bool


@dataclass(frozen=True, slots=True)
class IntakeEvidencePage:
    items: list[IntakeEvidenceItem]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _Position:
    intake_id: str
    producer_id: str
    position: int
    link_key: str


@dataclass(frozen=True, slots=True)
class _Claim:
    owner_id: str
    producer_id: str
    intake_id: str
    component_id: str
    code: str
    writer_bundle_id: str | None
    type_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Resolution:
    sample_uuid: str
    client_record_id: str
    healthkit_type: str
    status: LinkStatus


def exporter_client_record_id(code: str, sample_uuid: str) -> str:
    """The `client_record_id` the iOS exporter gives a quantity sample.

    `hk-quantity-<code with "_" replaced by "-">-<lowercase sample UUID>`, where
    `code` is the HealthRelay type code of the stored sample.
    """
    return f"{EXPORTER_RECORD_PREFIX}{code.replace('_', '-')}-{sample_uuid.lower()}"


def _encode_cursor(position: _Position) -> str:
    raw = json.dumps(
        [
            position.intake_id,
            position.producer_id,
            position.position,
            position.link_key,
        ],
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> _Position:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parts = cast("object", json.loads(base64.urlsafe_b64decode(padded.encode())))
    except (ValueError, binascii.Error) as error:
        raise InvalidIntakeEvidenceCursorError from error
    match parts:
        case [
            str() as intake_id,
            str() as producer_id,
            int() as position,
            str() as link_key,
        ] if not any(isinstance(part, bool) for part in parts) and position >= 0:
            return _Position(intake_id, producer_id, position, link_key)
        case _:
            raise InvalidIntakeEvidenceCursorError


def _metadata(raw: str | None) -> dict[str, object]:
    if raw is None:
        return {}
    try:
        parsed = cast("object", json.loads(raw))
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): value for key, value in parsed.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def _other_active_claims(
    connection: sqlite3.Connection,
    claim: _Claim,
    sample_uuid: str,
) -> int:
    row = cast(
        "tuple[int]",
        connection.execute(
            OTHER_ACTIVE_CLAIMS_SQL,
            {
                "owner_id": claim.owner_id,
                "sample_uuid": sample_uuid,
                "producer_id": claim.producer_id,
                "intake_id": claim.intake_id,
                "component_id": claim.component_id,
            },
        ).fetchone(),
    )
    return row[0]


def _resolve(
    connection: sqlite3.Connection,
    *,
    claim: _Claim,
    sample_uuid: str,
    healthkit_type: str,
) -> _Resolution:
    code, writer_bundle_id = claim.code, claim.writer_bundle_id
    expected_id = exporter_client_record_id(code, sample_uuid)
    contested = _other_active_claims(connection, claim, sample_uuid) > 0
    types = {code, *claim.type_codes}
    pairs = {
        (type_code, exporter_client_record_id(type_code, sample_uuid))
        for type_code in types
    } | {(type_code, expected_id) for type_code in types}
    deleted = cast(
        "object",
        connection.execute(
            DELETED_SAMPLE_SQL,
            {"ids": json.dumps(sorted({record_id for _, record_id in pairs}))},
        ).fetchone(),
    )
    rows = SAMPLE_ROWS_ADAPTER.validate_python(
        connection.execute(
            SAMPLES_SQL,
            {"pairs": json.dumps(sorted(pairs))},
        ).fetchall(),
    )
    # A claim another component also holds, or a sample deleted at its source
    # (even if another source still has a copy), is a conflict, never pending
    # or verified.
    if not rows:
        status: LinkStatus = (
            "pending" if deleted is None and not contested else "mismatch"
        )
        return _Resolution(sample_uuid, expected_id, healthkit_type, status)
    first_id = rows[0][0]
    exact: list[dict[str, object]] = []
    for client_record_id, type_code, metadata_json in rows:
        metadata = _metadata(metadata_json)
        identifier = metadata.get(HEALTHKIT_IDENTIFIER_METADATA_KEY)
        if (
            client_record_id == expected_id
            and type_code == code
            and (identifier is None or identifier == healthkit_type)
        ):
            exact.append(metadata)
    verified = writer_bundle_id is not None and any(
        metadata.get(SOURCE_BUNDLE_METADATA_KEY) == writer_bundle_id
        for metadata in exact
    )
    foreign = any(
        client_record_id != expected_id or type_code != code
        for client_record_id, type_code, _ in rows
    )
    if verified and not foreign and not contested and deleted is None:
        return _Resolution(sample_uuid, expected_id, healthkit_type, "verified")
    stored_id = expected_id if exact else first_id
    return _Resolution(sample_uuid, stored_id, healthkit_type, "mismatch")


def _item(
    row: ComponentRow,
    resolution: _Resolution | None,
    *,
    complete: bool,
) -> IntakeEvidenceItem:
    return IntakeEvidenceItem(
        intake_id=row[0],
        producer_id=row[1],
        revision=row[2],
        component_id=row[5],
        kind=row[6],
        code=row[7],
        amount=row[8],
        unit=row[9],
        value_state=row[10],
        sample_uuid=resolution.sample_uuid if resolution else None,
        client_record_id=resolution.client_record_id if resolution else None,
        healthkit_type=resolution.healthkit_type if resolution else None,
        writer_bundle_id=row[11],
        link_status=resolution.status if resolution else "unlinked",
        complete=complete,
    )


def _links_for(
    connection: sqlite3.Connection,
    row: ComponentRow,
    *,
    after_link: str | None,
    link_limit: int,
) -> list[tuple[str, str]]:
    return [
        (sample_uuid, healthkit_type)
        for sample_uuid, healthkit_type in ACTIVE_LINK_ROWS_ADAPTER.validate_python(
            connection.execute(
                COMPONENT_LINKS_SQL,
                {
                    "revision_row_id": row[3],
                    "component_id": row[5],
                    "after_link": after_link,
                    "link_limit": link_limit,
                },
            ).fetchall(),
        )
    ]


def _is_complete(
    connection: sqlite3.Connection,
    row: ComponentRow,
    owner_id: str,
) -> bool:
    code = row[7]
    unverified = cast(
        "tuple[int]",
        connection.execute(
            UNVERIFIED_LINK_COUNT_SQL,
            {
                "revision_row_id": row[3],
                "component_id": row[5],
                "code": code,
                "id_prefix": f"{EXPORTER_RECORD_PREFIX}{code.replace('_', '-')}-",
                "writer_bundle_id": row[11],
                "owner_id": owner_id,
                "producer_id": row[1],
                "intake_id": row[0],
            },
        ).fetchone(),
    )
    return unverified[0] == 0


def list_intake_evidence(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    intake_id: str | None = None,
) -> IntakeEvidencePage:
    """List the effective components of live intakes with their link evidence.

    Items are ordered by (intake_id, producer, component position), then by
    sample UUID when a component has several active links. A component with no
    active link in its newest projection snapshot yields one `unlinked` item.
    `complete` is false for every item of a component that has an active link
    not yet verified.

    The cursor names a component position and link inside an intake. If the
    intake's effective revision changes between pages, the next page continues
    from the position after the cursor in the new revision; components at or
    before that position are not emitted again. A caller that needs one
    consistent revision lists that intake again with `intake_id`.

    Work is bounded by the page: at most `limit + 1` links are collected, only
    page items are resolved, and completeness is one aggregate per component.
    """
    if isinstance(limit, bool) or not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise InvalidIntakeEvidenceLimitError(limit)
    after = _decode_cursor(cursor) if cursor is not None else None
    rows = COMPONENT_ROWS_ADAPTER.validate_python(
        connection.execute(
            COMPONENTS_SQL,
            {
                "owner_id": owner_id,
                "intake_id": intake_id,
                "after_intake_id": after.intake_id if after else None,
                "after_producer_id": after.producer_id if after else None,
                "after_position": after.position if after else None,
                # The cursor's own component may yield no further item, so one
                # extra row guarantees the lookahead item that proves a next page.
                "row_limit": limit + 2,
            },
        ).fetchall(),
    )
    type_codes = tuple(
        row[0]
        for row in connection.execute(DISTINCT_TYPE_CODES_SQL).fetchall()  # pyright: ignore[reportAny]
    )
    candidates: list[tuple[_Position, ComponentRow, tuple[str, str] | None]] = []
    for row in rows:
        row_intake_id, producer_id, _revision, _row_id, position = row[:5]
        own_component = after is not None and (
            row_intake_id,
            producer_id,
            position,
        ) == (after.intake_id, after.producer_id, after.position)
        links = _links_for(
            connection,
            row,
            after_link=after.link_key if after is not None and own_component else None,
            link_limit=limit + 1 - len(candidates),
        )
        for claim_link in links or [None]:
            link_key = claim_link[0] if claim_link else ""
            where = _Position(row_intake_id, producer_id, position, link_key)
            if after is not None and (
                row_intake_id,
                producer_id,
                position,
                link_key,
            ) <= (after.intake_id, after.producer_id, after.position, after.link_key):
                continue
            candidates.append((where, row, claim_link))
        if len(candidates) > limit:
            break
    page = candidates[:limit]
    completeness: dict[tuple[str, str, int], bool] = {}
    items: list[IntakeEvidenceItem] = []
    for _, row, claim_link in page:
        key = (row[0], row[1], row[4])
        if key not in completeness:
            completeness[key] = _is_complete(connection, row, owner_id)
        resolution = (
            _resolve(
                connection,
                claim=_Claim(
                    owner_id,
                    row[1],
                    row[0],
                    row[5],
                    row[7],
                    row[11],
                    type_codes,
                ),
                sample_uuid=claim_link[0],
                healthkit_type=claim_link[1],
            )
            if claim_link
            else None
        )
        items.append(_item(row, resolution, complete=completeness[key]))
    next_cursor = (
        _encode_cursor(page[-1][0]) if len(candidates) > limit and page else None
    )
    return IntakeEvidencePage(items=items, next_cursor=next_cursor)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "IntakeEvidenceItem",
    "IntakeEvidencePage",
    "InvalidIntakeEvidenceCursorError",
    "InvalidIntakeEvidenceLimitError",
    "LinkStatus",
    "exporter_client_record_id",
    "list_intake_evidence",
]

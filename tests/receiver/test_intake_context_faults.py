"""Adversarial protocol suite for intake-context acceptance.

Each test drives ``accept_batch`` against a real on-disk SQLite database and
states its expectations from docs/reference/intake-context-v1.md: lost
acknowledgements, reordering, tombstones, partial batches, reused delivery
identifiers, owner isolation and durability.
"""

import copy
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Final, cast

import pytest

from health_bridge.receiver.intake_context_acceptance import (
    OperationResult,
    accept_batch,
)
from health_bridge.storage import initialize_database
from health_bridge.storage.intake_context import (
    read_intake_state,
    read_operation_receipt,
    read_producer,
    read_revision,
    read_tombstone,
)
from tests.intake_context import reference as ref
from tests.intake_context.reference import JsonObject

FIXTURES: Final = Path(__file__).parents[1] / "fixtures" / "intake_context"
OWNER: Final = "owner-a"
OTHER: Final = "owner-b"
NOW: Final = "2026-10-02T12:00:00Z"
LATER: Final = "2026-10-02T13:00:00Z"
PRODUCER: Final = "nutrition-app"
INTAKE: Final = "e6677963-418c-4027-b563-551d8a531eed"
INTAKE_B: Final = "7777aaaa-7777-4777-8777-777777777777"
INTAKE_C: Final = "8888bbbb-8888-4888-8888-888888888888"
SAMPLE_A: Final = "2c932bd1-c46d-4e38-b481-e0d842fdd429"
SAMPLE_B: Final = "9a1f3c57-8e2d-4b60-a7c4-d5e0b1f28396"
SAMPLE_C: Final = "55555555-5555-4555-8555-555555555555"
WATER_TYPE: Final = "HKQuantityTypeIdentifierDietaryWater"
CAFFEINE_TYPE: Final = "HKQuantityTypeIdentifierDietaryCaffeine"
TABLES: Final = (
    "intake_revisions",
    "intake_projection_snapshots",
    "intake_sample_links",
    "intake_operation_receipts",
    "intake_tombstones",
)


def oid(n: int) -> str:
    d = f"{n:x}"
    return f"{d * 8}-{d * 4}-4{d * 3}-8{d * 3}-{d * 12}"


WORKED: Final = cast(
    "JsonObject",
    json.loads((FIXTURES / "valid_worked_example.json").read_text(encoding="utf-8")),
)


def link(
    sample: str,
    version: int,
    disposition: str = "active",
    *,
    component: str = "water",
) -> JsonObject:
    kind = CAFFEINE_TYPE if component == "caffeine" else WATER_TYPE
    identifier = component
    return {
        "component_id": component,
        "healthkit_sample_uuid": sample,
        "healthkit_type": kind,
        "sync_identifier": f"intake:{INTAKE}:{identifier}",
        "sync_version": version,
        "disposition": disposition,
    }


def upsert(
    revision: int,
    op: int,
    links: list[JsonObject] | None = None,
    intake: str = INTAKE,
    **changes: object,
) -> JsonObject:
    base = cast("list[JsonObject]", copy.deepcopy(WORKED["operations"]))[0]
    base.update(
        operation_id=oid(op),
        intake_id=intake,
        revision=revision,
        display_name=f"Water revision {revision}",
        healthkit_links=[link(SAMPLE_A, 2)] if links is None else links,
    )
    base.update(changes)
    return base


def projection(
    revision: int,
    sequence: int,
    op: int,
    links: list[JsonObject],
    intake: str = INTAKE,
) -> JsonObject:
    return {
        "operation_id": oid(op),
        "operation": "link_projection",
        "intake_id": intake,
        "revision": revision,
        "projection_sequence": sequence,
        "healthkit_links": links,
    }


def delete(revision: int, op: int, intake: str = INTAKE) -> JsonObject:
    return {
        "operation_id": oid(op),
        "operation": "delete",
        "intake_id": intake,
        "revision": revision,
        "deleted_at": "2026-09-30T18:05:00Z",
    }


def batch(*operations: JsonObject) -> JsonObject:
    envelope = copy.deepcopy(WORKED)
    envelope["operations"] = copy.deepcopy(list(operations))
    return ref.seal(envelope)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "faults.sqlite"
    initialize_database(path)
    return path


@pytest.fixture
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(db_path)) as conn:
        _ = conn.execute("pragma foreign_keys = on")
        yield conn


def run(
    conn: sqlite3.Connection,
    payload: JsonObject,
    owner: str = OWNER,
    at: str = NOW,
) -> list[OperationResult]:
    return accept_batch(
        conn, owner_id=owner, payload=copy.deepcopy(payload), received_at=at
    )


def one(
    conn: sqlite3.Connection,
    operation: JsonObject,
    owner: str = OWNER,
    at: str = NOW,
) -> OperationResult:
    (result,) = run(conn, batch(operation), owner, at)
    return result


def names(results: list[OperationResult]) -> list[str]:
    return [r.result for r in results]


def state(conn: sqlite3.Connection, owner: str = OWNER, intake: str = INTAKE):  # noqa: ANN201
    return read_intake_state(
        conn, owner_id=owner, producer_id=PRODUCER, intake_id=intake
    )


def stored(conn: sqlite3.Connection, revision: int, owner: str = OWNER):  # noqa: ANN201
    return read_revision(
        conn,
        owner_id=owner,
        producer_id=PRODUCER,
        intake_id=INTAKE,
        revision=revision,
    )


def receipt(conn: sqlite3.Connection, op: int, owner: str = OWNER):  # noqa: ANN201
    return read_operation_receipt(
        conn, owner_id=owner, producer_id=PRODUCER, operation_id=oid(op)
    )


def tombstone(conn: sqlite3.Connection, owner: str = OWNER):  # noqa: ANN201
    return read_tombstone(conn, owner_id=owner, producer_id=PRODUCER, intake_id=INTAKE)


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: cast(
            "int",
            conn.execute(f"select count(*) from {table}").fetchone()[0],  # noqa: S608
        )
        for table in TABLES
    }


def position(conn: sqlite3.Connection) -> tuple[int | None, int | None, bool | None]:
    current = state(conn)
    if current is None:
        return (None, None, None)
    return (
        current.current_revision,
        current.current_projection_sequence,
        current.deleted,
    )


def test_lost_ack_replay_is_duplicate_and_changes_nothing(
    connection: sqlite3.Connection,
) -> None:
    payload = batch(upsert(2, 1))
    (first,) = run(connection, payload)
    assert first.result == "accepted"
    before = (counts(connection), position(connection))
    (again,) = run(connection, payload, at=LATER)
    assert again.result == "duplicate"
    assert again.accepted_revision == first.accepted_revision == 2
    assert again.projection_sequence == 1
    assert again.server_cursor == first.server_cursor
    assert (counts(connection), position(connection)) == before
    current = state(connection)
    assert current is not None
    assert current.updated_at == NOW


def test_lost_ack_replay_after_later_revision_is_still_duplicate(
    connection: sqlite3.Connection,
) -> None:
    first = one(connection, upsert(2, 1))
    assert one(connection, upsert(3, 2)).result == "accepted"
    before = (counts(connection), position(connection))
    again = one(connection, upsert(2, 1))
    assert again.result == "duplicate"
    assert again.server_cursor == first.server_cursor
    assert (counts(connection), position(connection)) == before
    assert position(connection) == (3, 1, False)


def test_out_of_order_revisions_end_at_the_newest(
    connection: sqlite3.Connection,
) -> None:
    newest = one(connection, upsert(3, 3))
    assert newest.result == "accepted"
    for revision, op in ((2, 2), (1, 1)):
        late = one(connection, upsert(revision, op))
        assert late.result == "stale_revision"
        assert late.current_revision == 3
        assert stored(connection, revision) is None
    assert position(connection) == (3, 1, False)
    held = stored(connection, 3)
    assert held is not None
    assert held.record.display_name == "Water revision 3"
    assert counts(connection)["intake_revisions"] == 1


def test_out_of_order_link_projections_keep_the_highest_sequence(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    seq3 = projection(
        2,
        3,
        3,
        [
            link(SAMPLE_C, 4),
            link(SAMPLE_B, 3, "superseded"),
            link(SAMPLE_A, 2, "superseded"),
        ],
    )
    seq2 = projection(2, 2, 2, [link(SAMPLE_B, 3), link(SAMPLE_A, 2, "superseded")])
    assert one(connection, seq3).result == "accepted"
    late = one(connection, seq2)
    assert late.result == "stale_revision"
    assert late.current_revision == 2
    assert position(connection) == (2, 3, False)
    held = stored(connection, 2)
    assert held is not None
    assert [s.projection_sequence for s in held.projections] == [1, 3]
    active = [
        s.sample_uuid for s in held.projections[-1].links if s.disposition == "active"
    ]
    assert active == [SAMPLE_C]


def test_link_projection_before_its_upsert_is_retryable_then_accepted(
    connection: sqlite3.Connection,
) -> None:
    early = projection(2, 2, 2, [link(SAMPLE_B, 3), link(SAMPLE_A, 2, "superseded")])
    result = one(connection, early)
    assert result.result == "retryable_failure"
    assert result.server_cursor is None
    assert receipt(connection, 2) is None
    assert state(connection) is None
    assert counts(connection) == dict.fromkeys(TABLES, 0)
    assert read_producer(connection, owner_id=OWNER, producer_id=PRODUCER) is None
    assert one(connection, upsert(2, 1)).result == "accepted"
    retried = one(connection, early)
    assert retried.result == "accepted"
    assert (retried.accepted_revision, retried.projection_sequence) == (2, 2)
    assert position(connection) == (2, 2, False)


def test_new_revision_before_delete_of_old_keeps_new_state(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    assert one(connection, upsert(3, 2)).result == "accepted"
    for revision, op in ((2, 3), (3, 4)):
        late = one(connection, delete(revision, op))
        assert late.result == "stale_revision"
        assert late.current_revision == 3
    assert tombstone(connection) is None
    assert position(connection) == (3, 1, False)
    assert stored(connection, 3) is not None


def test_stale_replay_after_tombstone_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    assert one(connection, delete(3, 2)).result == "accepted"
    before = counts(connection)
    stale_upsert = one(connection, upsert(1, 3, display_name="Delayed older copy"))
    stale_link = one(connection, projection(2, 2, 4, [link(SAMPLE_B, 3)]))
    assert stale_upsert.result == "stale_revision"
    assert stale_upsert.current_revision == 3
    assert stale_link.result == "stale_revision"
    assert stale_link.current_revision == 3
    assert stored(connection, 1) is None
    held = stored(connection, 2)
    assert held is not None
    assert [s.projection_sequence for s in held.projections] == [1]
    assert counts(connection)["intake_revisions"] == before["intake_revisions"]
    assert (
        counts(connection)["intake_projection_snapshots"]
        == before["intake_projection_snapshots"]
    )
    assert position(connection) == (3, 1, True)


def test_resurrection_after_tombstone_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    assert one(connection, delete(3, 2)).result == "accepted"
    for revision, op in ((2, 3), (3, 4)):
        again = one(connection, upsert(revision, op, display_name="Back from the dead"))
        assert again.result == "stale_revision"
        assert again.current_revision == 3
    assert stored(connection, 3) is None
    held = stored(connection, 2)
    assert held is not None
    assert held.record.display_name == "Water revision 2"
    tomb = tombstone(connection)
    assert tomb is not None
    assert tomb.revision == 3
    assert position(connection) == (3, 1, True)


def test_partial_batch_keeps_committed_operations(
    connection: sqlite3.Connection,
) -> None:
    ghost = projection(2, 2, 2, [link(SAMPLE_B, 3, component="ghost")])
    good = projection(2, 2, 3, [link(SAMPLE_B, 3), link(SAMPLE_A, 2, "superseded")])
    results = run(connection, batch(upsert(2, 1), ghost, good))
    assert names(results) == ["accepted", "permanent_failure", "accepted"]
    assert [r.operation_id for r in results] == [oid(1), oid(2), oid(3)]
    assert position(connection) == (2, 2, False)
    held = stored(connection, 2)
    assert held is not None
    assert [s.projection_sequence for s in held.projections] == [1, 2]
    assert receipt(connection, 1) is not None
    assert receipt(connection, 3) is not None


def test_partial_batch_retry_is_duplicate_for_committed_and_applies_the_rest(
    connection: sqlite3.Connection,
) -> None:
    payload = batch(
        upsert(2, 1),
        projection(2, 2, 2, [link(SAMPLE_B, 3)], intake=INTAKE_B),
        upsert(2, 3, intake=INTAKE_C),
    )
    first = run(connection, payload)
    assert names(first) == ["accepted", "retryable_failure", "accepted"]
    assert receipt(connection, 2) is None
    assert one(connection, upsert(2, 4, intake=INTAKE_B)).result == "accepted"
    retry = run(connection, payload)
    assert names(retry) == ["duplicate", "accepted", "duplicate"]
    assert retry[0].server_cursor == first[0].server_cursor
    assert retry[2].server_cursor == first[2].server_cursor
    assert state(connection, intake=INTAKE_B) is not None
    assert counts(connection)["intake_revisions"] == 3
    # three first snapshots (one per upsert) plus the one link projection
    assert counts(connection)["intake_projection_snapshots"] == 4
    assert names(run(connection, payload)) == ["duplicate"] * 3


def test_same_sample_on_two_components_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    caffeine_fact: JsonObject = {
        "component_id": "caffeine",
        "kind": "nutrient",
        "code": "dietary_caffeine",
        "amount": "100",
        "unit": "mg",
        "value_state": "known",
        "aggregation_role": "context_only",
        "provenance": "user_confirmed",
    }
    worked_op = cast("list[JsonObject]", WORKED["operations"])[0]
    facts = [*cast("list[JsonObject]", worked_op["facts"]), caffeine_fact]
    shared = [
        link(SAMPLE_A, 2),
        link(SAMPLE_A, 1, component="caffeine"),
    ]
    rejected = one(connection, upsert(2, 1, shared, facts=facts))
    assert rejected.result == "permanent_failure"
    assert state(connection) is None
    assert counts(connection) == dict.fromkeys(TABLES, 0)
    distinct = [
        link(SAMPLE_A, 2),
        link(SAMPLE_B, 1, component="caffeine"),
    ]
    assert one(connection, upsert(2, 2, distinct, facts=facts)).result == "accepted"
    moved = projection(
        2,
        2,
        3,
        [
            link(SAMPLE_A, 2),
            link(SAMPLE_A, 1, component="caffeine"),
        ],
    )
    assert one(connection, moved).result == "permanent_failure"
    assert position(connection) == (2, 1, False)


def test_reused_operation_id_with_new_content_never_duplicate(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    assert one(connection, projection(2, 2, 2, [link(SAMPLE_B, 3)])).result == (
        "accepted"
    )
    assert one(connection, delete(3, 3)).result == "accepted"
    before = (counts(connection), position(connection))
    changed_upsert = one(connection, upsert(2, 1, display_name="Changed"))
    changed_link = one(connection, projection(2, 2, 2, [link(SAMPLE_C, 5)]))
    changed_delete = one(connection, delete(4, 3))
    assert changed_upsert.result == "domain_conflict"
    assert changed_link.result == "projection_conflict"
    assert changed_delete.result == "domain_conflict"
    for result in (changed_upsert, changed_link, changed_delete):
        assert result.result not in {"duplicate", "accepted"}
    assert (counts(connection), position(connection)) == before


def test_other_owner_is_isolated(connection: sqlite3.Connection) -> None:
    payload = batch(upsert(2, 1))
    assert names(run(connection, payload)) == ["accepted"]
    assert names(run(connection, payload, OTHER)) == ["accepted"]
    assert one(connection, delete(3, 2), OTHER).result == "accepted"
    assert position(connection) == (2, 1, False)
    assert tombstone(connection) is None
    assert tombstone(connection, OTHER) is not None
    assert state(connection, OTHER) is not None
    assert receipt(connection, 2) is None
    assert receipt(connection, 2, OTHER) is not None
    assert stored(connection, 2, OTHER) is not None
    assert one(connection, projection(2, 2, 3, [link(SAMPLE_B, 3)]), OTHER).result == (
        "stale_revision"
    )
    assert one(connection, projection(2, 2, 3, [link(SAMPLE_B, 3)])).result == (
        "accepted"
    )


def test_digest_tamper_is_permanent_and_not_stored(
    connection: sqlite3.Connection,
) -> None:
    facts_tamper = batch(upsert(2, 1))
    cast("list[JsonObject]", facts_tamper["operations"])[0]["display_name"] = "Edited"
    client_tamper = batch(upsert(2, 2))
    cast("list[JsonObject]", client_tamper["operations"])[0]["client_payload_hash"] = (
        "sha256:" + "0" * 64
    )
    for payload, op in ((facts_tamper, 1), (client_tamper, 2)):
        (result,) = run(connection, payload)
        assert result.result == "permanent_failure"
        assert result.server_cursor is None
        assert receipt(connection, op) is None
    assert state(connection) is None
    assert stored(connection, 2) is None
    assert counts(connection) == dict.fromkeys(TABLES, 0)
    assert read_producer(connection, owner_id=OWNER, producer_id=PRODUCER) is None
    mixed = batch(upsert(2, 3), upsert(3, 4))
    cast("list[JsonObject]", mixed["operations"])[1]["display_name"] = "Edited"
    assert names(run(connection, mixed)) == ["accepted", "permanent_failure"]
    assert receipt(connection, 4) is None
    assert position(connection) == (2, 1, False)
    assert one(connection, upsert(3, 4)).result == "accepted"


def test_receipts_survive_reopening_the_database(
    db_path: Path,
) -> None:
    payload = batch(upsert(2, 1), projection(2, 2, 2, [link(SAMPLE_B, 3)]))
    with closing(sqlite3.connect(db_path)) as first_conn:
        first = run(first_conn, payload)
    assert names(first) == ["accepted", "accepted"]
    with closing(sqlite3.connect(db_path)) as reopened:
        found = receipt(reopened, 1)
        assert found is not None
        assert found.server_cursor == first[0].server_cursor
        replay = run(reopened, payload, at=LATER)
        assert names(replay) == ["duplicate", "duplicate"]
        assert [r.server_cursor for r in replay] == [r.server_cursor for r in first]
        assert position(reopened) == (2, 2, False)
        assert one(reopened, delete(3, 3)).result == "accepted"
    with closing(sqlite3.connect(db_path)) as third:
        assert tombstone(third) is not None
        assert one(third, upsert(1, 4)).result == "stale_revision"
        assert position(third) == (3, 2, True)


def test_original_ack_replay_after_tombstone_is_still_duplicate(
    connection: sqlite3.Connection,
) -> None:
    first = one(connection, upsert(2, 1))
    assert one(connection, delete(3, 2)).result == "accepted"
    again = one(connection, upsert(2, 1))
    assert again.result == "duplicate"
    assert again.server_cursor == first.server_cursor
    assert position(connection) == (3, 1, True)


def test_delete_replay_under_new_operation_id_is_duplicate(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    assert one(connection, delete(3, 2)).result == "accepted"
    before = counts(connection)
    again = one(connection, delete(3, 3))
    assert again.result == "duplicate"
    assert again.accepted_revision == 3
    assert counts(connection)["intake_tombstones"] == before["intake_tombstones"]
    assert position(connection) == (3, 1, True)


def test_equal_sequence_with_other_snapshot_is_projection_conflict(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    first = [link(SAMPLE_B, 3), link(SAMPLE_A, 2, "superseded")]
    assert one(connection, projection(2, 2, 2, first)).result == "accepted"
    same = one(connection, projection(2, 2, 3, first))
    other = one(connection, projection(2, 2, 4, [link(SAMPLE_C, 4)]))
    assert same.result == "duplicate"
    assert other.result == "projection_conflict"
    assert position(connection) == (2, 2, False)
    held = stored(connection, 2)
    assert held is not None
    assert [s.projection_sequence for s in held.projections] == [1, 2]
    first_upsert = one(connection, upsert(2, 5, links=[link(SAMPLE_C, 4)]))
    assert first_upsert.result == "projection_conflict"


def test_stale_link_projection_after_newer_revision_is_stale(
    connection: sqlite3.Connection,
) -> None:
    assert one(connection, upsert(2, 1)).result == "accepted"
    assert one(connection, upsert(3, 2, links=[link(SAMPLE_B, 3)])).result == (
        "accepted"
    )
    late = one(connection, projection(2, 2, 3, [link(SAMPLE_C, 4)]))
    assert late.result == "stale_revision"
    assert late.current_revision == 3
    held = stored(connection, 2)
    assert held is not None
    assert [s.projection_sequence for s in held.projections] == [1]
    assert position(connection) == (3, 1, False)

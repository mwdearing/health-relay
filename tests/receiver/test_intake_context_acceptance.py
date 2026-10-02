"""Acceptance logic for intake-context batches: every outcome rule, per operation."""

import copy
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Final, cast

import pytest

from health_bridge.contract.intake_context_v1 import IntakeContextBatchV1
from health_bridge.receiver import intake_context_acceptance as module
from health_bridge.receiver.intake_context_acceptance import (
    Operation,
    OperationResult,
    accept_batch,
)
from health_bridge.storage import initialize_database
from health_bridge.storage.intake_context import (
    IntakeStateRegressionError,
    OperationReceiptRecord,
    read_intake_state,
    read_operation_receipt,
    read_producer,
    read_tombstone,
)
from tests.intake_context import reference as ref
from tests.intake_context.reference import JsonObject

FIXTURES: Final = Path(__file__).parents[1] / "fixtures" / "intake_context"
OWNER: Final = "owner-a"
OTHER: Final = "owner-b"
NOW: Final = "2026-10-02T12:00:00Z"
PRODUCER: Final = "nutrition-app"
INTAKE: Final = "e6677963-418c-4027-b563-551d8a531eed"
NEW_IDS: Final = [
    f"{n}{n}{n}{n}{n}{n}{n}{n}-{n}{n}{n}{n}-4{n}{n}{n}-8{n}{n}{n}-{n * 12}"
    for n in "123456"
]
OTHER_SAMPLE: Final = "44444444-4444-4444-8444-444444444444"


def load(name: str) -> JsonObject:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return cast("JsonObject", json.loads(text))


def ops(batch: JsonObject) -> list[JsonObject]:
    return cast("list[JsonObject]", batch["operations"])


def op0(batch: JsonObject) -> JsonObject:
    return ops(batch)[0]


def op_id_of(batch: JsonObject) -> str:
    return cast("str", op0(batch)["operation_id"])


def links_of(batch: JsonObject) -> list[JsonObject]:
    return cast("list[JsonObject]", op0(batch)["healthkit_links"])


def clone(batch: JsonObject) -> JsonObject:
    return copy.deepcopy(batch)


WORKED: Final = load("valid_worked_example.json")
SEQ2: Final = load("valid_link_projection_seq2.json")
DELETE: Final = load("valid_delete.json")
BLEND: Final = load("valid_proprietary_blend.json")


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "db.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as conn:
        _ = conn.execute("pragma foreign_keys = on")
        yield conn


def run(
    conn: sqlite3.Connection, batch: JsonObject, owner: str = OWNER
) -> list[OperationResult]:
    return accept_batch(conn, owner_id=owner, payload=clone(batch), received_at=NOW)


def names(results: list[OperationResult]) -> list[str]:
    return [r.result for r in results]


def variant(
    batch: JsonObject, op_id: str | None = None, **changes: object
) -> JsonObject:
    result = clone(batch)
    op = op0(result)
    if op_id:
        op["operation_id"] = op_id
    op.update(changes)
    return ref.seal(result)


def state(conn: sqlite3.Connection, owner: str = OWNER):  # noqa: ANN201
    return read_intake_state(
        conn, owner_id=owner, producer_id=PRODUCER, intake_id=INTAKE
    )


def test_upsert_is_accepted_with_revision_sequence_and_cursor(
    connection: sqlite3.Connection,
) -> None:
    (result,) = run(connection, WORKED)
    assert result.result == "accepted"
    assert result.operation_id == op_id_of(WORKED)
    assert (result.accepted_revision, result.projection_sequence) == (2, 1)
    assert isinstance(result.server_cursor, int)
    current = state(connection)
    assert current is not None
    assert (current.current_revision, current.current_projection_sequence) == (2, 1)


def test_identical_replay_is_duplicate(connection: sqlite3.Connection) -> None:
    _ = run(connection, WORKED)
    assert names(run(connection, WORKED)) == ["duplicate"]


def test_same_facts_under_a_new_operation_id_is_duplicate(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    assert names(run(connection, variant(WORKED, NEW_IDS[0]))) == ["duplicate"]


def test_different_facts_at_the_same_revision_is_domain_conflict(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    changed = variant(WORKED, NEW_IDS[0], display_name="Something else")
    assert names(run(connection, changed)) == ["domain_conflict"]


def test_reused_operation_id_with_different_content_is_domain_conflict(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    changed = variant(WORKED, display_name="Something else")
    assert names(run(connection, changed)) == ["domain_conflict"]


def test_owners_are_independent(connection: sqlite3.Connection) -> None:
    assert names(run(connection, WORKED)) == ["accepted"]
    assert names(run(connection, WORKED, OTHER)) == ["accepted"]
    assert state(connection, OTHER) is not None
    receipt = read_operation_receipt(
        connection,
        owner_id=OTHER,
        producer_id=PRODUCER,
        operation_id=op_id_of(WORKED),
    )
    assert receipt is not None


@pytest.mark.parametrize(
    "name",
    ["scenario_same_operation_different_content.json", "scenario_stale_revision.json"],
)
def test_scenario_fixtures_replay_as_expected(
    connection: sqlite3.Connection, name: str
) -> None:
    steps = cast("list[JsonObject]", load(name)["steps"])
    for step in steps:
        batch = cast("JsonObject", step["batch"])
        got = [(r.operation_id, r.result) for r in run(connection, batch)]
        expect = cast("list[dict[str, str]]", step["expect"])
        assert got == [(e["operation_id"], e["result"]) for e in expect]


def test_older_revision_is_stale_and_reports_the_current_one(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, variant(WORKED, NEW_IDS[0], revision=3))
    (result,) = run(connection, WORKED)
    assert (result.result, result.current_revision) == ("stale_revision", 3)


def test_link_projection_is_accepted_then_duplicate(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    (first,) = run(connection, SEQ2)
    assert (first.result, first.accepted_revision, first.projection_sequence) == (
        "accepted",
        2,
        2,
    )
    assert names(run(connection, SEQ2)) == ["duplicate"]
    current = state(connection)
    assert current is not None
    assert current.current_projection_sequence == 2


def _other_links() -> list[JsonObject]:
    links = [dict(link) for link in links_of(SEQ2)]
    links[0]["healthkit_sample_uuid"] = OTHER_SAMPLE
    return links


def test_different_snapshot_at_equal_sequence_is_projection_conflict(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = run(connection, SEQ2)
    changed = variant(SEQ2, NEW_IDS[0], healthkit_links=_other_links())
    assert names(run(connection, changed)) == ["projection_conflict"]


def test_reused_operation_id_on_a_link_projection_is_projection_conflict(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = run(connection, SEQ2)
    assert names(run(connection, variant(SEQ2, healthkit_links=_other_links()))) == [
        "projection_conflict"
    ]


def test_lower_sequence_never_replaces_newer_state(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    assert names(run(connection, variant(SEQ2, NEW_IDS[0], projection_sequence=4))) == [
        "accepted"
    ]
    result = run(connection, variant(SEQ2, NEW_IDS[1], projection_sequence=3))
    assert names(result) == ["stale_revision"]
    current = state(connection)
    assert current is not None
    assert current.current_projection_sequence == 4


def test_link_projection_before_its_revision_is_retryable_and_not_stored(
    connection: sqlite3.Connection,
) -> None:
    (result,) = run(connection, SEQ2)
    assert result.result == "retryable_failure"
    assert (
        read_operation_receipt(
            connection,
            owner_id=OWNER,
            producer_id=PRODUCER,
            operation_id=op_id_of(SEQ2),
        )
        is None
    )
    assert names(run(connection, WORKED)) == ["accepted"]
    assert names(run(connection, SEQ2)) == ["accepted"]


def test_link_projection_for_an_older_revision_is_stale(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, variant(WORKED, NEW_IDS[0], revision=3))
    (result,) = run(connection, SEQ2)
    assert (result.result, result.current_revision) == ("stale_revision", 3)


def test_link_projection_to_a_foreign_component_is_permanent_failure(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    links = [dict(link) for link in links_of(SEQ2)]
    links[0]["component_id"] = "ghost"
    assert names(run(connection, variant(SEQ2, NEW_IDS[0], healthkit_links=links))) == [
        "permanent_failure"
    ]


def test_delete_is_accepted_and_writes_a_tombstone(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    (result,) = run(connection, DELETE)
    assert (result.result, result.accepted_revision) == ("accepted", 3)
    assert read_tombstone(
        connection, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE
    )
    current = state(connection)
    assert current is not None
    assert current.deleted


def test_tombstone_blocks_upserts_and_link_projections(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = run(connection, DELETE)
    (upsert,) = run(connection, variant(WORKED, NEW_IDS[0]))
    assert (upsert.result, upsert.current_revision) == ("stale_revision", 3)
    assert names(run(connection, variant(SEQ2, NEW_IDS[1]))) == ["stale_revision"]


def test_tombstone_blocks_resurrection_by_a_higher_revision(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = run(connection, DELETE)
    assert names(run(connection, variant(WORKED, NEW_IDS[0], revision=4))) == [
        "stale_revision"
    ]
    current = state(connection)
    assert current is not None
    assert current.deleted


def test_delete_needs_a_higher_revision_than_any_accepted(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    result = run(connection, variant(DELETE, NEW_IDS[0], revision=2))
    assert names(result) == ["stale_revision"]
    assert result[0].current_revision == 2


def test_digest_mismatch_is_permanent_failure(connection: sqlite3.Connection) -> None:
    bad = clone(WORKED)
    op0(bad)["domain_facts_hash"] = "sha256:" + "0" * 64
    assert names(run(connection, bad)) == ["permanent_failure"]
    assert state(connection) is None


def test_unknown_major_version_is_permanent_failure(
    connection: sqlite3.Connection,
) -> None:
    assert names(run(connection, load("invalid_unknown_major_version.json"))) == [
        "permanent_failure"
    ]


def test_invalid_batch_gives_one_failure_per_operation_id_found(
    connection: sqlite3.Connection,
) -> None:
    batch = clone(WORKED)
    ops(batch).append(clone(op0(SEQ2)))
    batch["schema_version"] = "2.0"
    results = run(connection, batch)
    assert [r.operation_id for r in results] == [
        op_id_of(WORKED),
        op_id_of(SEQ2),
    ]
    assert set(names(results)) == {"permanent_failure"}


def test_unparseable_text_still_reports_the_operation_ids(
    connection: sqlite3.Connection,
) -> None:
    text = (FIXTURES / "invalid_duplicate_object_name.json").read_text(encoding="utf-8")
    results = accept_batch(connection, owner_id=OWNER, payload=text, received_at=NOW)
    assert results
    assert set(names(results)) == {"permanent_failure"}


def test_bytes_payload_is_accepted(connection: sqlite3.Connection) -> None:
    payload = json.dumps(WORKED).encode()
    results = accept_batch(connection, owner_id=OWNER, payload=payload, received_at=NOW)
    assert names(results) == ["accepted"]


def test_sync_version_order_violation_is_permanent_failure(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    bad = clone(SEQ2)
    links_of(bad)[0]["sync_version"] = 1
    assert names(run(connection, ref.seal(bad))) == ["permanent_failure"]


def test_a_failed_operation_does_not_undo_an_earlier_one(
    connection: sqlite3.Connection,
) -> None:
    mixed = clone(BLEND)
    bad = clone(op0(WORKED))
    bad["domain_facts_hash"] = "sha256:" + "0" * 64
    ops(mixed).append(bad)
    assert names(run(connection, mixed)) == ["accepted", "permanent_failure"]
    assert names(run(connection, BLEND)) == ["duplicate"]


def test_operations_run_in_array_order_and_see_each_other(
    connection: sqlite3.Connection,
) -> None:
    both = clone(WORKED)
    ops(both).append(clone(op0(SEQ2)))
    assert names(run(connection, both)) == ["accepted", "accepted"]
    reverse = clone(both)
    ops(reverse).reverse()
    assert names(run(connection, reverse, OTHER)) == ["retryable_failure", "accepted"]


def test_receipts_are_stored_for_terminal_results(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = run(connection, DELETE)
    stale = variant(WORKED, NEW_IDS[0])
    _ = run(connection, stale)
    receipt = read_operation_receipt(
        connection,
        owner_id=OWNER,
        producer_id=PRODUCER,
        operation_id=NEW_IDS[0],
    )
    assert receipt is not None
    assert receipt.outcome == "stale_revision"
    assert names(run(connection, stale)) == ["stale_revision"]


def test_a_second_writer_bundle_for_a_producer_is_permanent_failure(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    other = clone(BLEND)
    other["writer_bundle_id"] = "com.example.someone.else"
    assert names(run(connection, ref.seal(other))) == ["permanent_failure"]


def _receipt(
    conn: sqlite3.Connection, op_id: str, owner: str = OWNER
) -> OperationReceiptRecord | None:
    return read_operation_receipt(
        conn, owner_id=owner, producer_id=PRODUCER, operation_id=op_id
    )


def _fail_after_apply(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    real = module._apply  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def failing(
        connection: sqlite3.Connection,
        batch: IntakeContextBatchV1,
        operation: Operation,
        owner_id: str,
        received_at: str,
    ) -> OperationResult:
        _ = real(connection, batch, operation, owner_id, received_at)
        raise error

    monkeypatch.setattr(module, "_apply", failing)


@pytest.mark.parametrize(
    "message",
    ["database is locked", "database table is locked", "database is busy"],
)
def test_lock_error_is_retryable_and_leaves_nothing_behind(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    _fail_after_apply(monkeypatch, sqlite3.OperationalError(message))
    assert names(run(connection, WORKED)) == ["retryable_failure"]
    assert not connection.in_transaction
    assert _receipt(connection, op_id_of(WORKED)) is None
    assert state(connection) is None


def test_other_operational_errors_propagate(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_after_apply(monkeypatch, sqlite3.OperationalError("no such table: x"))
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _ = run(connection, WORKED)
    assert not connection.in_transaction
    assert state(connection) is None


def test_commit_failure_rolls_back(connection: sqlite3.Connection) -> None:
    class Wrapper:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self.inner: sqlite3.Connection = inner

        def __getattr__(self, name: str) -> object:
            return cast("object", getattr(self.inner, name))

        def commit(self) -> None:
            message = "database is locked"
            raise sqlite3.OperationalError(message)

    wrapped = Wrapper(connection)
    results = accept_batch(
        wrapped,  # pyright: ignore[reportArgumentType]
        owner_id=OWNER,
        payload=clone(WORKED),
        received_at=NOW,
    )
    assert names(results) == ["retryable_failure"]
    assert not connection.in_transaction
    assert state(connection) is None


def test_open_transaction_is_refused_not_retryable(
    connection: sqlite3.Connection,
) -> None:
    _ = connection.execute("begin immediate")
    with pytest.raises(sqlite3.Error):
        _ = run(connection, WORKED)
    assert connection.in_transaction
    connection.rollback()


def test_revoked_producer_is_permanent_failure(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = connection.execute(
        "update intake_producers set revoked_at = ? where producer_id = ?",
        (NOW, PRODUCER),
    )
    connection.commit()
    assert names(run(connection, variant(WORKED, NEW_IDS[0], revision=3))) == [
        "permanent_failure"
    ]


def test_replayed_delete_under_a_new_operation_id_is_duplicate(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, WORKED)
    _ = run(connection, DELETE)
    again = variant(DELETE, NEW_IDS[0])
    assert names(run(connection, again)) == ["duplicate"]


def test_delete_at_or_below_current_revision_is_stale(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, variant(WORKED, NEW_IDS[0], revision=5))
    for index, revision in enumerate((5, 4)):
        (result,) = run(
            connection, variant(DELETE, NEW_IDS[index + 1], revision=revision)
        )
        assert (result.result, result.current_revision) == ("stale_revision", 5)


def test_state_regression_is_permanent_failure_and_later_operations_run(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = module._apply  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    calls: list[str] = []

    def flaky(
        connection: sqlite3.Connection,
        batch: IntakeContextBatchV1,
        operation: Operation,
        owner_id: str,
        received_at: str,
    ) -> OperationResult:
        calls.append(str(operation))
        if len(calls) == 1:
            _ = real(connection, batch, operation, owner_id, received_at)
            raise IntakeStateRegressionError(INTAKE)
        return real(connection, batch, operation, owner_id, received_at)

    monkeypatch.setattr(module, "_apply", flaky)
    both = clone(BLEND)
    ops(both).append(clone(op0(WORKED)))
    results = run(connection, both)
    assert names(results) == ["permanent_failure", "accepted"]
    assert results[0].detail == "IntakeStateRegressionError"
    assert not connection.in_transaction
    assert _receipt(connection, op_id_of(both)) is None
    assert _receipt(connection, cast("str", ops(both)[1]["operation_id"])) is not None


def test_integrity_error_is_permanent_failure_for_that_operation(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_after_apply(monkeypatch, sqlite3.IntegrityError("UNIQUE constraint x"))
    (result,) = run(connection, WORKED)
    assert (result.result, result.detail) == ("permanent_failure", "IntegrityError")
    assert state(connection) is None


def test_stale_replay_reports_the_current_revision(
    connection: sqlite3.Connection,
) -> None:
    _ = run(connection, variant(WORKED, NEW_IDS[0], revision=3))
    (first,) = run(connection, WORKED)
    assert (first.result, first.current_revision) == ("stale_revision", 3)
    _ = run(connection, variant(WORKED, NEW_IDS[1], revision=7))
    (replay,) = run(connection, WORKED)
    assert (replay.result, replay.current_revision) == ("stale_revision", 7)


def test_same_operation_and_intake_from_another_owner_is_independent(
    connection: sqlite3.Connection,
) -> None:
    op_id = op_id_of(WORKED)
    _ = run(connection, WORKED)
    _ = run(connection, DELETE)
    assert names(run(connection, WORKED, OTHER)) == ["accepted"]
    assert _receipt(connection, op_id, OTHER) is not None
    assert (
        read_tombstone(
            connection, owner_id=OTHER, producer_id=PRODUCER, intake_id=INTAKE
        )
        is None
    )
    other = state(connection, OTHER)
    assert other is not None
    assert not other.deleted
    assert names(run(connection, WORKED, OTHER)) == ["duplicate"]


def _duplicate_id_batch() -> JsonObject:
    batch = clone(WORKED)
    second = clone(op0(WORKED))
    second["revision"] = 3
    ops(batch).append(second)
    return ref.seal(batch)


def test_digest_failure_is_tracked_by_position_not_operation_id(
    connection: sqlite3.Connection,
) -> None:
    batch = _duplicate_id_batch()
    ops(batch)[1]["domain_facts_hash"] = "sha256:" + "0" * 64
    results = run(connection, batch)
    assert names(results) == ["accepted", "permanent_failure"]
    current = state(connection)
    assert current is not None
    assert current.current_revision == 2


def test_invalid_batch_keeps_duplicate_operation_ids_in_array_order(
    connection: sqlite3.Connection,
) -> None:
    batch = _duplicate_id_batch()
    batch["schema_version"] = "2.0"
    results = run(connection, batch)
    assert [r.operation_id for r in results] == [op_id_of(WORKED)] * 2
    assert set(names(results)) == {"permanent_failure"}


def _producer_row(connection: sqlite3.Connection) -> object:
    return read_producer(connection, owner_id=OWNER, producer_id=PRODUCER)


def test_retryable_first_operation_registers_no_producer(
    connection: sqlite3.Connection,
) -> None:
    assert names(run(connection, SEQ2)) == ["retryable_failure"]
    assert _producer_row(connection) is None
    other = clone(WORKED)
    other["writer_bundle_id"] = "com.example.someone.else"
    assert names(run(connection, ref.seal(other))) == ["accepted"]
